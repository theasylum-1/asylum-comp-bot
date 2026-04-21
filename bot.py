import discord
from discord.ext import commands
import aiohttp
import os
import json
import re
import base64
from datetime import datetime
from urllib.parse import quote_plus

# ── Config ─────────────────────────────────────────────────────────────────────
DISCORD_TOKEN    = os.environ["DISCORD_TOKEN"]
OPENAI_API_KEY   = os.environ["OPENAI_API_KEY"]
JUSTTCG_API_KEY  = os.environ["JUSTTCG_API_KEY"]
EBAY_APP_ID      = os.environ.get("EBAY_APP_ID", "").strip()
EBAY_CERT_ID     = os.environ.get("EBAY_CERT_ID", "").strip()
PRICE_CHECK_CHANNEL = "price-check"

TCG_SPORTS = {"pokemon", "one piece", "onepiece", "one-piece", "magic", "yugioh", "yu-gi-oh"}

POKEMON_SET_CODES = {
    "PFL": "Phantasmal Flames",
    "JTG": "Journey Together",
    "PRE": "Prismatic Evolutions",
    "SSP": "Surging Sparks",
    "SCR": "Stellar Crown",
    "SFA": "Shrouded Fable",
    "TWM": "Twilight Masquerade",
    "TEF": "Temporal Forces",
    "PAF": "Paldean Fates",
    "PAR": "Paradox Rift",
    "MEW": "151",
    "OBF": "Obsidian Flames",
    "PAL": "Paldea Evolved",
    "SVI": "Scarlet & Violet",
    "CRZ": "Crown Zenith",
    "SIT": "Silver Tempest",
    "LOR": "Lost Origin",
    "PGO": "Pokemon GO",
    "ASR": "Astral Radiance",
    "BRS": "Brilliant Stars",
    "FST": "Fusion Strike",
    "CEL": "Celebrations",
    "EVS": "Evolving Skies",
    "CRE": "Chilling Reign",
    "BST": "Battle Styles",
    "SHF": "Shining Fates",
    "VIV": "Vivid Voltage",
    "DAA": "Darkness Ablaze",
    "RCL": "Rebel Clash",
    "SSH": "Sword & Shield",
}

ONE_PIECE_SET_CODES = {
    "OP01": "Romance Dawn",
    "OP02": "Paramount War",
    "OP03": "Pillars of Strength",
    "OP04": "Kingdoms of Intrigue",
    "OP05": "Awakening of the New Era",
    "OP06": "Wings of the Captain",
    "OP07": "500 Years in the Future",
    "OP08": "Two Legends",
    "OP09": "Emperors in the New World",
    "OP10": "Royal Blood",
    "OP11": "A Fist of Divine Speed",
    "ST01": "Straw Hat Crew",
    "ST02": "Worst Generation",
    "ST03": "The Seven Warlords of the Sea",
    "ST04": "Animal Kingdom Pirates",
    "ST05": "Film Edition",
    "ST06": "Absolute Justice",
    "ST07": "Big Mom Pirates",
    "ST08": "Monkey D. Luffy",
    "ST09": "Yamato",
    "ST10": "UTA",
    "ST13": "The Three Brothers",
    "ST14": "3D2Y",
    "ST15": "Red Edward Newgate",
    "ST16": "Green Uta",
    "EB01": "Extra Booster Memorial Collection",
    "ME01": "Premium Card Collection",
    "ME02": "Phantasmal Flames",
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
processed_messages = set()

# ── eBay OAuth token cache ─────────────────────────────────────────────────────
_ebay_token = None
_ebay_token_expiry = 0


async def get_ebay_token() -> str | None:
    """Fetch a client-credentials OAuth token from eBay (cached until expiry)."""
    global _ebay_token, _ebay_token_expiry

    if _ebay_token and datetime.utcnow().timestamp() < _ebay_token_expiry - 60:
        return _ebay_token

    if not EBAY_APP_ID or not EBAY_CERT_ID:
        print("eBay credentials not configured")
        return None

    credentials = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()

    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    payload = "grant_type=client_credentials&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.ebay.com/identity/v1/oauth2/token",
                headers=headers,
                data=payload,
            ) as resp:
                data = await resp.json()

        if "access_token" not in data:
            print(f"eBay token error: {data}")
            return None

        _ebay_token = data["access_token"]
        _ebay_token_expiry = datetime.utcnow().timestamp() + data.get("expires_in", 7200)
        print("eBay OAuth token obtained successfully")
        return _ebay_token

    except Exception as e:
        print(f"eBay token exception: {e}")
        return None


async def get_ebay_comps(query: str) -> list:
    """Search eBay sold listings via the Browse API."""
    token = await get_ebay_token()
    if not token:
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        "Content-Type": "application/json",
    }

    params = {
        "q": query,
        "filter": "buyingOptions:{FIXED_PRICE},conditions:{USED|LIKE_NEW|VERY_GOOD|GOOD|ACCEPTABLE},soldItemsOnly:true",
        "sort": "endingSoonest",
        "limit": "5",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.ebay.com/buy/browse/v1/item_summary/search",
                headers=headers,
                params=params,
            ) as resp:
                status = resp.status
                data = await resp.json(content_type=None)

        print(f"eBay Browse API status: {status}")
        print(f"eBay Browse API response snippet: {json.dumps(data)[:400]}")

        if status != 200:
            print(f"eBay error: {data.get('errors', data)}")
            return []

        items = data.get("itemSummaries", [])
        results = []
        for item in items[:5]:
            try:
                price_info = item.get("price", {})
                price = float(price_info.get("value", 0))
                if price == 0:
                    continue
                last_sold = item.get("itemEndDate", item.get("itemCreationDate", ""))[:10]
                results.append({
                    "title": item.get("title", "")[:60],
                    "price": price,
                    "date": last_sold,
                    "url": item.get("itemWebUrl", ""),
                })
            except (KeyError, ValueError):
                continue

        print(f"eBay comps found: {len(results)}")
        return results

    except Exception as e:
        print(f"eBay Browse API exception: {e}")
        import traceback
        traceback.print_exc()
        return []


# ── Helpers ────────────────────────────────────────────────────────────────────

def resolve_set_from_code(set_code: str, sport: str) -> str:
    code = set_code.upper().strip()
    if "pokemon" in sport.lower():
        return POKEMON_SET_CODES.get(code, "")
    elif "one piece" in sport.lower() or "onepiece" in sport.lower():
        return ONE_PIECE_SET_CODES.get(code, "")
    return ""


async def identify_card(image_urls: list) -> dict:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    content = [
        {
            "type": "text",
            "text": (
                "You are a precise trading card OCR system. Your job is to READ text directly from the card image — do NOT guess or infer. "
                "Return ONLY a valid JSON object with no markdown, no explanation.\n\n"

                "STEP 1 — IS THE CARD IN A GRADED SLAB?\n"
                "Look for a hard plastic case with a printed label (PSA, BGS, SGC, CGC). "
                "If graded, read ALL text from the slab label directly — name, set, card number, grade.\n\n"

                "STEP 2 — IDENTIFY THE GAME by visual cues:\n"
                "- POKEMON: 'HP' top-right, energy symbols, bottom copyright '© Nintendo/Creatures/GAMEFREAK'\n"
                "- ONE PIECE: ONE PIECE logo top-left, card type (Leader/Character/Event/Stage) on left edge\n"
                "- SPORTS CARD: Photo of real athlete, brand logo (Topps, Bowman, Panini, Upper Deck, Donruss, Prizm, Select)\n"
                "- MAGIC: Mana cost symbols top-right, 'Illustrated by' credit at bottom\n\n"

                "STEP 3 — READ THE CARD NUMBER (critical — read character by character):\n"
                "- Pokemon: bottom-right corner, format like '025/091' or '201/165' or 'SWSH001'\n"
                "- One Piece: bottom-left, format like 'OP07-001' — the prefix IS the set code\n"
                "- Sports: usually bottom of card or back, may say '#123' or 'Card 123 of 500'\n"
                "Do NOT guess this number. If you cannot read it clearly, return empty string.\n\n"

                "STEP 4 — READ THE SET CODE (bottom-left corner for Pokemon, embedded in card number for One Piece):\n"
                "For Pokemon, look for 2-3 letter codes like: SSH, BST, CRE, EVS, CEL, FST, BRS, ASR, PGO, LOR, SIT, CRZ, PAL, SVI, OBF, MEW, PAR, PAF, TEF, TWM, SFA, SCR, SSP, PRE, JTG, PFL\n"
                "Read each character individually — do not confuse O/0, I/1, B/8, S/5.\n\n"

                "STEP 5 — IDENTIFY THE VARIANT precisely:\n"
                "- Pokemon variants: Base, Holo Rare, Reverse Holo, Full Art, Ultra Rare, Secret Rare, Rainbow Rare, Gold Rare, "
                "Illustration Rare, Special Illustration Rare, Hyper Rare, Trainer Gallery, Shiny, Promo\n"
                "  Look for: holographic foil pattern on artwork (Full Art/Illustration Rare), rainbow shimmer (Rainbow/Hyper Rare), "
                "  gold card border (Gold Rare), textured artwork (Special Illustration Rare)\n"
                "- Sports variants: Base, Holo, Refractor, Chrome, Prizm, Auto (signed), Relic/Patch, "
                "  Gold Refractor, Color Match Refractor, Superfractor — read any foil/color indicators\n"
                "- If it is a numbered card, read the serial number as 'X/Y' and return only the denominator Y in 'serial'\n\n"

                "Return EXACTLY this JSON with these keys:\n"
                "{\n"
                "  'player': 'Full character or athlete name as printed on card',\n"
                "  'year': 'Four-digit year',\n"
                "  'brand': 'Pokemon / One Piece / Topps / Bowman / Panini / Upper Deck / etc',\n"
                "  'set': 'Full set name if printed on card, else empty string',\n"
                "  'set_code': 'Exact code read from card (e.g. PFL, OP07, SSH) — empty if unreadable',\n"
                "  'variation': 'Exact variant type from Step 5 — empty string only if confirmed base non-holo',\n"
                "  'serial': 'Denominator only from numbered card (e.g. 75 from 54/75) — empty if not numbered',\n"
                "  'card_number': 'Exact number as printed (e.g. 025/091, OP07-112, SWSH001) — empty if unreadable',\n"
                "  'sport': 'EXACTLY one of: Baseball / Football / Basketball / Hockey / Pokemon / One Piece / Magic / YuGiOh / Other',\n"
                "  'graded': 'true or false',\n"
                "  'grading_company': 'PSA / BGS / SGC / CGC / empty string',\n"
                "  'grade': 'Numeric grade e.g. 10 or 9.5 — empty if not graded'\n"
                "}\n\n"
                "IMPORTANT RULES:\n"
                "- Never guess or hallucinate a card number or set code. Empty string is better than wrong.\n"
                "- Read card_number and set_code character by character from the actual image.\n"
                "- All values must be single-line strings with no newlines."
            ),
        }
    ]

    for url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})

    payload = {
        "model": "gpt-4o",
        "max_tokens": 800,
        "messages": [{"role": "user", "content": content}],
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            data = await resp.json()

    raw = data["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
    card = json.loads(raw)

    for key in card:
        if isinstance(card[key], str):
            card[key] = card[key].replace("\n", " ").replace("\r", " ").strip()

    # Resolve set name from set code lookup table
    set_code = card.get("set_code", "")
    sport = card.get("sport", "")
    if set_code:
        resolved = resolve_set_from_code(set_code, sport)
        if resolved:
            print(f"Resolved set code '{set_code}' -> '{resolved}'")
            card["set"] = resolved

    # For Pokemon cards, attempt a hard confirmation via the Pokemon TCG API
    if "pokemon" in sport.lower() and (set_code or card.get("card_number")):
        confirmed = await confirm_pokemon_card(set_code, card.get("card_number", ""), card.get("player", ""))
        if confirmed:
            print(f"Pokemon TCG API confirmed: {confirmed}")
            # Overwrite GPT-4o guesses with canonical API data
            card["player"]      = confirmed.get("name", card["player"])
            card["set"]         = confirmed.get("set", card["set"])
            card["set_code"]    = confirmed.get("set_code", card["set_code"])
            card["card_number"] = confirmed.get("number", card["card_number"])
            card["variation"]   = confirmed.get("rarity", card["variation"])
            card["year"]        = confirmed.get("year", card["year"])
            card["_ptcg_id"]    = confirmed.get("id", "")  # store for later use

    return card


async def confirm_pokemon_card(set_code: str, card_number: str, player_name: str) -> dict | None:
    """
    Look up a Pokemon card in the Pokemon TCG API using set code + card number.
    Falls back to name search if the number lookup fails.
    Returns canonical card data to overwrite GPT-4o output.
    """
    base_url = "https://api.pokemontcg.io/v2/cards"
    headers = {}  # No API key needed for free tier, but add if you have one
    # If you have a Pokemon TCG API key, add it:
    # POKEMON_TCG_API_KEY = os.environ.get("POKEMON_TCG_API_KEY", "")
    # if POKEMON_TCG_API_KEY:
    #     headers["X-Api-Key"] = POKEMON_TCG_API_KEY

    try:
        # Strategy 1: set code + card number — most precise
        if set_code and card_number:
            # Normalize card number: strip leading zeros from X/Y format for query
            # Pokemon TCG API uses format like "set_id-number" e.g. "pfl-25"
            # But we can also query by number field directly
            clean_num = card_number.split("/")[0].lstrip("0") or "0"
            # Try direct ID lookup: {set_id}-{number} e.g. pfl-25
            ptcg_set_id = set_code.lower()
            card_id = f"{ptcg_set_id}-{clean_num}"
            print(f"Pokemon TCG API: trying ID lookup '{card_id}'")

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{base_url}/{card_id}",
                    headers=headers,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return _parse_ptcg_card(data.get("data", {}))

            # Strategy 2: query by set + number fields
            query = f'set.ptcgoCode:"{set_code}" number:"{card_number.split("/")[0]}"'
            print(f"Pokemon TCG API: trying query '{query}'")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    base_url,
                    headers=headers,
                    params={"q": query, "pageSize": 5},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("data", [])
                        if results:
                            return _parse_ptcg_card(results[0])

        # Strategy 3: name + set code fallback
        if player_name and set_code:
            query = f'name:"{player_name}" set.ptcgoCode:"{set_code}"'
            print(f"Pokemon TCG API: fallback query '{query}'")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    base_url,
                    headers=headers,
                    params={"q": query, "pageSize": 5},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("data", [])
                        if results:
                            return _parse_ptcg_card(results[0])

        print("Pokemon TCG API: no match found")
        return None

    except Exception as e:
        print(f"Pokemon TCG API error: {e}")
        return None


def _parse_ptcg_card(card: dict) -> dict | None:
    """Extract the fields we care about from a Pokemon TCG API card object."""
    if not card:
        return None
    return {
        "id":       card.get("id", ""),
        "name":     card.get("name", ""),
        "number":   card.get("number", ""),
        "set":      card.get("set", {}).get("name", ""),
        "set_code": card.get("set", {}).get("ptcgoCode", ""),
        "rarity":   card.get("rarity", ""),
        "year":     str(card.get("set", {}).get("releaseDate", "")[:4]),
    }


def is_tcg_card(card: dict) -> bool:
    sport = card.get("sport", "").lower().strip()
    return any(t in sport for t in TCG_SPORTS)


def build_search_query(card: dict) -> str:
    parts = []
    if card.get("year"):      parts.append(card["year"])
    if card.get("player"):    parts.append(card["player"])
    if card.get("brand"):     parts.append(card["brand"])
    if card.get("set"):
        parts.append(card["set"])
    elif card.get("set_code"):
        parts.append(card["set_code"])
    if card.get("variation"): parts.append(card["variation"])
    if card.get("serial"):    parts.append(f"/{card['serial']}")
    if card.get("graded") == "true" and card.get("grading_company") and card.get("grade"):
        parts.append(f"{card['grading_company']} {card['grade']}")
    query = " ".join(parts)
    return re.sub(r"[\r\n\t]+", " ", query).strip()


def build_ebay_links(query: str) -> dict:
    encoded = quote_plus(query)
    return {
        "ebay_sold": f"https://www.ebay.com/sch/i.html?_nkw={encoded}&LH_Complete=1&LH_Sold=1&_sop=13",
        "ebay_active": f"https://www.ebay.com/sch/i.html?_nkw={encoded}&_sop=13",
    }


async def _justtcg_search(game: str, query: str, card_number: str = "", number_filter: str = "") -> dict | None:
    headers = {
        "x-api-key": JUSTTCG_API_KEY,
        "Content-Type": "application/json",
    }

    params = {
        "q": query,
        "game": game,
        "limit": 10,
        "include_price_history": "false",
        "include_statistics": "7d",
    }

    if number_filter:
        params["number"] = number_filter

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.justtcg.com/v1/cards",
                headers=headers,
                params=params,
            ) as resp:
                status = resp.status
                content_type = resp.headers.get("content-type", "")
                if "json" not in content_type:
                    return None
                data = await resp.json()

        results = data.get("data", [])
        if not results:
            return None

        best = None
        if card_number:
            clean_num = card_number.replace(" ", "").lower()
            for c in results:
                c_num = str(c.get("number", "")).replace(" ", "").lower()
                if c_num == clean_num or clean_num in c_num:
                    best = c
                    break

        if not best:
            best = results[0]

        variants = best.get("variants", [])
        if not variants:
            return None

        nm_variant = next(
            (v for v in variants if "near mint" in v.get("condition", "").lower() and "normal" in v.get("printing", "").lower()),
            variants[0]
        )

        nm_prices = [v.get("price") for v in variants if v.get("price") and "near mint" in v.get("condition", "").lower()]
        low = min(nm_prices) if nm_prices else None
        high = max(nm_prices) if nm_prices else None
        market = nm_variant.get("price")

        return {
            "name": best.get("name", ""),
            "set": best.get("set_name", ""),
            "number": best.get("number", ""),
            "rarity": best.get("rarity", ""),
            "market_price": market,
            "low_price": low,
            "high_price": high,
            "tcgplayer_url": f"https://www.tcgplayer.com/product/{best.get('tcgplayerId')}" if best.get("tcgplayerId") else "",
        }

    except Exception as e:
        print(f"JustTCG error: {e}")
        return None


async def get_justtcg_price(card: dict) -> dict | None:
    sport = card.get("sport", "").lower()

    game_map = {
        "pokemon": "pokemon",
        "one piece": "one-piece-card-game",
        "onepiece": "one-piece-card-game",
        "one-piece": "one-piece-card-game",
        "magic": "magic-the-gathering",
        "yugioh": "yugioh",
    }

    game = None
    for key, value in game_map.items():
        if key in sport:
            game = value
            break

    if not game:
        return None

    player = card.get("player", "")
    set_name = card.get("set", "")
    card_number = card.get("card_number", "")

    if player and card_number:
        result = await _justtcg_search(game, player, card_number, card_number)
        if result:
            return result

    if player and set_name:
        result = await _justtcg_search(game, f"{player} {set_name}", card_number)
        if result:
            return result

    if player:
        result = await _justtcg_search(game, player, card_number)
        if result:
            return result

    return None


# ── Embed formatters ───────────────────────────────────────────────────────────

def format_tcg_response(card: dict, query: str, tcg_data: dict | None, ebay_links: dict, manual: bool = False) -> discord.Embed:
    card_name = card.get("player", "") or query
    if manual:
        card_name = query

    embed = discord.Embed(
        title=f"🃏 {card_name}",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow(),
    )

    search_label = f"`{query}`" + (" *(manual)*" if manual else "")
    if card.get("_ptcg_id"):
        search_label += "\n✅ *Confirmed via Pokemon TCG API*"
    embed.add_field(name="🔍 Search Used", value=search_label, inline=False)

    if not manual and card:
        details = []
        if card.get("sport"):       details.append(f"**Game:** {card['sport']}")
        if card.get("set"):         details.append(f"**Set:** {card['set']}")
        if card.get("set_code"):    details.append(f"**Set Code:** {card['set_code']}")
        if card.get("variation"):   details.append(f"**Variant:** {card['variation']}")
        if card.get("card_number"): details.append(f"**Card #:** {card['card_number']}")
        if card.get("year"):        details.append(f"**Year:** {card['year']}")
        if card.get("graded") == "true":
            grade_str = f"{card.get('grading_company', '')} {card.get('grade', '')}".strip()
            if grade_str:
                details.append(f"**Grade:** {grade_str}")
        if details:
            embed.add_field(name="📋 Card Details", value="\n".join(details), inline=False)

    if tcg_data:
        price_lines = []
        if tcg_data.get("low_price"):    price_lines.append(f"**Low:** ${tcg_data['low_price']:.2f}")
        if tcg_data.get("market_price"): price_lines.append(f"**Market:** ${tcg_data['market_price']:.2f}")
        if tcg_data.get("high_price"):   price_lines.append(f"**High:** ${tcg_data['high_price']:.2f}")
        if tcg_data.get("rarity"):       price_lines.append(f"**Rarity:** {tcg_data['rarity']}")
        if price_lines:
            value = "\n".join(price_lines)
            if tcg_data.get("tcgplayer_url"):
                value += f"\n[View on TCGPlayer]({tcg_data['tcgplayer_url']})"
            embed.add_field(name="💰 TCGPlayer Prices (Near Mint)", value=value, inline=False)
    else:
        embed.add_field(
            name="💰 TCGPlayer Prices",
            value="Could not find this card. Try adding a caption with the exact card name and set.",
            inline=False
        )

    embed.add_field(
        name="🔗 eBay Comps",
        value=(
            f"[💵 Sold Listings]({ebay_links['ebay_sold']})  |  "
            f"[🛒 Active Listings]({ebay_links['ebay_active']})"
        ),
        inline=False
    )

    embed.set_footer(text="The Asylum • Card Comp Bot | TCGPlayer prices via JustTCG")
    return embed


def format_sports_response(card: dict, query: str, comps: list, ebay_links: dict, manual: bool = False) -> discord.Embed:
    card_name = " ".join(filter(None, [
        card.get("year"), card.get("player"),
        card.get("brand"), card.get("set"), card.get("variation"),
        f"/{card['serial']}" if card.get("serial") else "",
        f"{card.get('grading_company','')} {card.get('grade','')}".strip() if card.get("graded") == "true" else ""
    ])) or query

    if manual:
        card_name = query

    embed = discord.Embed(
        title=f"🃏 {card_name}",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow(),
    )

    embed.add_field(
        name="🔍 Search Used",
        value=f"`{query}`" + (" *(manual)*" if manual else ""),
        inline=False
    )

    if not manual and card:
        details = []
        if card.get("sport"):       details.append(f"**Sport:** {card['sport']}")
        if card.get("brand"):       details.append(f"**Brand:** {card['brand']}")
        if card.get("set"):         details.append(f"**Set:** {card['set']}")
        if card.get("year"):        details.append(f"**Year:** {card['year']}")
        if card.get("variation"):   details.append(f"**Variation:** {card['variation']}")
        if card.get("serial"):      details.append(f"**Print Run:** /{card['serial']}")
        if card.get("card_number"): details.append(f"**Card #:** {card['card_number']}")
        if card.get("graded") == "true":
            grade_str = f"{card.get('grading_company', '')} {card.get('grade', '')}".strip()
            if grade_str:
                details.append(f"**Grade:** {grade_str}")
        if details:
            embed.add_field(name="📋 Card Details", value="\n".join(details), inline=False)

    if comps:
        prices = [c["price"] for c in comps]
        avg = sum(prices) / len(prices)
        embed.add_field(name="💰 Avg Sold Price", value=f"**${avg:.2f}**", inline=True)
        embed.add_field(name="📦 Sales Found", value=str(len(comps)), inline=True)
        sales_lines = "\n".join(
            f"[${c['price']:.2f} — {c['date']}]({c['url']})" for c in comps
        )
        sales_lines += (
            f"\n\n[💵 See All Sold]({ebay_links['ebay_sold']})  |  "
            f"[🛒 Active Listings]({ebay_links['ebay_active']})"
        )
        embed.add_field(name="🧾 Recent eBay Sales", value=sales_lines, inline=False)
    else:
        embed.add_field(
            name="💰 eBay Comps",
            value=(
                f"No sold listings found via API.\n"
                f"[💵 Search Sold Listings]({ebay_links['ebay_sold']})\n"
                f"[🛒 Search Active Listings]({ebay_links['ebay_active']})"
            ),
            inline=False
        )

    embed.set_footer(text="The Asylum • Card Comp Bot | eBay sold data")
    return embed


# ── Bot events ─────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Asylum Bot is online as {bot.user}")
    print(f"eBay API: {'✅ App ID configured - ' + EBAY_APP_ID[:20] if EBAY_APP_ID else '❌ Not configured'}")
    print(f"eBay Cert: {'✅ Cert ID configured' if EBAY_CERT_ID else '❌ Not configured'}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.id in processed_messages:
        return
    processed_messages.add(message.id)
    if len(processed_messages) > 1000:
        oldest = list(processed_messages)[:500]
        for msg_id in oldest:
            processed_messages.discard(msg_id)

    channel_match = (
        message.channel.name == PRICE_CHECK_CHANNEL
        or str(message.channel.id) == PRICE_CHECK_CHANNEL
    )
    if not channel_match:
        await bot.process_commands(message)
        return

    image_attachments = [
        a for a in message.attachments
        if a.content_type and a.content_type.startswith("image/")
    ]
    if not image_attachments:
        await bot.process_commands(message)
        return

    thinking = await message.reply("🔍 Identifying your card… hang tight!")

    try:
        image_urls = [a.url for a in image_attachments[:2]]
        print(f"Images received: {len(image_urls)}")

        caption = message.content.strip() if message.content.strip() else None
        manual = False

        if caption:
            query = re.sub(r"[\r\n\t]+", " ", caption).strip()
            card = {}
            manual = True
            print(f"Manual query: {repr(query)}")
        else:
            card = await identify_card(image_urls)
            print(f"Card identified: {card}")
            query = build_search_query(card)
            print(f"Search query: {repr(query)}")

        if not query.strip():
            await thinking.edit(
                content=(
                    "❌ Couldn't identify the card. Try posting front **and back** together, "
                    "or add a caption like:\n`2022 Miguel Cabrera Topps Chrome`"
                )
            )
            return

        ebay_links = build_ebay_links(query)

        if not manual and is_tcg_card(card):
            print(f"TCG card: {card.get('sport')}")
            await thinking.edit(content="🔍 Found it! Pulling TCGPlayer prices… hang tight!")
            tcg_data = await get_justtcg_price(card)
            embed = format_tcg_response(card, query, tcg_data, ebay_links)
        else:
            await thinking.edit(content="🔍 Found it! Pulling eBay sold comps… hang tight!")
            comps = await get_ebay_comps(query)
            embed = format_sports_response(card, query, comps, ebay_links, manual=manual)

        await thinking.delete()
        await message.reply(embed=embed)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        await thinking.edit(content="❌ Something went wrong. Try again or check the logs.")

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)

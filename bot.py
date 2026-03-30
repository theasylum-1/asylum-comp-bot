import discord
from discord.ext import commands
import aiohttp
import os
import json
import re
from datetime import datetime
from urllib.parse import quote_plus

# ── Config ─────────────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.environ["DISCORD_TOKEN"]
OPENAI_API_KEY  = os.environ["OPENAI_API_KEY"]
JUSTTCG_API_KEY = os.environ["JUSTTCG_API_KEY"]
PRICE_CHECK_CHANNEL = "price-check"

TCG_SPORTS = {"pokemon", "one piece", "onepiece", "one-piece", "magic", "yugioh", "yu-gi-oh"}

# ── Pokemon set code -> full name ───────────────────────────────────────────────
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

# ── One Piece set code -> full name ─────────────────────────────────────────────
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
                "You are an expert trading card identifier. Examine ALL provided images carefully and return ONLY a JSON object with no markdown.\n\n"

                "STEP 1 — IDENTIFY THE GAME by looking at these clues:\n"
                "- POKEMON: Has 'HP' in top right, energy symbols, 'Pokémon / Nintendo / Creatures / GAME FREAK' in copyright. "
                "Set codes at bottom left like PFL, OBF, SVI, PAR, MEW, TWM, SSP, SCR, JTG, PRE etc.\n"
                "- ONE PIECE: Has ONE PIECE logo, pirate/anime characters. Set codes like OP01-OP10, ST01-ST16, EB01, ME01, ME02.\n"
                "- SPORTS: Real athletes, brands like Topps/Bowman/Panini/Upper Deck.\n"
                "- MAGIC: Fantasy art, mana symbols, Magic: The Gathering branding.\n\n"

                "STEP 2 — READ THE SET CODE from the bottom left corner exactly as printed (e.g. 'PFL', 'OBF', 'OP07'). This is critical.\n\n"

                "STEP 3 — READ THE COPYRIGHT LINE at the bottom to confirm the game.\n\n"

                "Return this exact JSON:\n"
                "- 'player': Character or player full name\n"
                "- 'year': Year from copyright line\n"
                "- 'brand': Pokemon, One Piece, Topps, Bowman, Panini, Upper Deck, etc\n"
                "- 'set': Full set name if visible, otherwise leave empty — the set_code will be used to look it up\n"
                "- 'set_code': The SHORT code printed on the card bottom left (e.g. 'PFL', 'OP07'). VERY IMPORTANT — read this carefully.\n"
                "- 'variation': Holo, Full Art, Alt Art, Illustration Rare, Special Illustration Rare, Secret Rare, Rainbow, Gold, Refractor, Auto, etc. Empty if base.\n"
                "- 'serial': Print run denominator only (e.g. '75' from '54/75'). Empty if not numbered.\n"
                "- 'card_number': Card number as printed (e.g. '107/094' or 'L-001')\n"
                "- 'sport': EXACTLY one of: Baseball, Football, Basketball, Hockey, Pokemon, One Piece, Magic, YuGiOh, Other\n\n"
                "All values single-line strings, no newlines. Empty string if unknown."
            ),
        }
    ]

    for url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})

    payload = {
        "model": "gpt-4o",
        "max_tokens": 600,
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

    # Auto-resolve set name from set code
    set_code = card.get("set_code", "")
    sport = card.get("sport", "")
    if set_code:
        resolved = resolve_set_from_code(set_code, sport)
        if resolved:
            print(f"Resolved set code '{set_code}' -> '{resolved}'")
            card["set"] = resolved

    return card


def is_tcg_card(card: dict) -> bool:
    sport = card.get("sport", "").lower().strip()
    return any(t in sport for t in TCG_SPORTS)


def build_search_query(card: dict) -> str:
    parts = []
    if card.get("year"):      parts.append(card["year"])
    if card.get("player"):    parts.append(card["player"])
    if card.get("brand"):     parts.append(card["brand"])
    if card.get("set"):       parts.append(card["set"])
    if card.get("variation"): parts.append(card["variation"])
    if card.get("serial"):    parts.append(f"/{card['serial']}")
    query = " ".join(parts)
    return re.sub(r"[\r\n\t]+", " ", query).strip()


async def _justtcg_search(game: str, query: str, card_number: str = "", number_filter: str = "") -> dict | None:
    """Search JustTCG v1 API with correct headers and endpoint."""
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
                print(f"JustTCG status: {status}, content-type: {content_type}")

                if "json" not in content_type:
                    text = await resp.text()
                    print(f"Non-JSON response: {text[:200]}")
                    return None

                data = await resp.json()

        results = data.get("data", [])
        print(f"JustTCG results: {len(results)}")

        if not results:
            return None

        # Try to match by card number
        best = None
        if card_number:
            clean_num = card_number.replace(" ", "").lower()
            for c in results:
                c_num = str(c.get("number", "")).replace(" ", "").lower()
                if c_num == clean_num or clean_num in c_num:
                    best = c
                    print(f"Matched by card number: {c.get('name')} #{c.get('number')}")
                    break

        if not best:
            best = results[0]
            print(f"Using first result: {best.get('name')} #{best.get('number')}")

        variants = best.get("variants", [])
        if not variants:
            return None

        # Get NM price, fall back to first variant
        nm_variant = next(
            (v for v in variants if "near mint" in v.get("condition", "").lower() and "normal" in v.get("printing", "").lower()),
            variants[0]
        )

        # Collect all NM prices for low/market/high
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

    # Try 1: name + card number filter
    if player and card_number:
        result = await _justtcg_search(game, player, card_number, card_number)
        if result:
            return result

    # Try 2: name + set
    if player and set_name:
        result = await _justtcg_search(game, f"{player} {set_name}", card_number)
        if result:
            return result

    # Try 3: name only
    if player:
        result = await _justtcg_search(game, player, card_number)
        if result:
            return result

    return None


def build_ebay_links(query: str) -> dict:
    encoded = quote_plus(query)
    return {
        "ebay_sold": f"https://www.ebay.com/sch/i.html?_nkw={encoded}&LH_Complete=1&LH_Sold=1&_sop=13",
        "ebay_active": f"https://www.ebay.com/sch/i.html?_nkw={encoded}&_sop=13",
    }


def format_tcg_response(card: dict, query: str, tcg_data: dict | None, ebay_links: dict, manual: bool = False) -> discord.Embed:
    card_name = card.get("player", "") or query
    if manual:
        card_name = query

    embed = discord.Embed(
        title=f"🃏 {card_name}",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow(),
    )

    embed.add_field(
        name="🔍 Search Used",
        value=f"`{query}`" + (" *(manual)*" if manual else ""),
        inline=False
    )

    if not manual and card:
        details = []
        if card.get("sport"):       details.append(f"**Game:** {card['sport']}")
        if card.get("set"):         details.append(f"**Set:** {card['set']}")
        if card.get("set_code"):    details.append(f"**Set Code:** {card['set_code']}")
        if card.get("variation"):   details.append(f"**Variant:** {card['variation']}")
        if card.get("card_number"): details.append(f"**Card #:** {card['card_number']}")
        if card.get("year"):        details.append(f"**Year:** {card['year']}")
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


def format_sports_response(card: dict, query: str, ebay_links: dict, manual: bool = False) -> discord.Embed:
    card_name = " ".join(filter(None, [
        card.get("year"), card.get("player"),
        card.get("brand"), card.get("set"), card.get("variation"),
        f"/{card['serial']}" if card.get("serial") else ""
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
        if details:
            embed.add_field(name="📋 Card Details", value="\n".join(details), inline=False)

    embed.add_field(
        name="💰 Check Comps",
        value=(
            f"[💵 eBay Sold Listings]({ebay_links['ebay_sold']})\n"
            f"[🛒 eBay Active Listings]({ebay_links['ebay_active']})"
        ),
        inline=False
    )

    embed.set_footer(text="The Asylum • Card Comp Bot")
    return embed


@bot.event
async def on_ready():
    print(f"✅ Asylum Bot is online as {bot.user}")


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
                    "or add a caption like:\n`Ambipom 107/094 Phantasmal Flames`"
                )
            )
            return

        ebay_links = build_ebay_links(query)

        if not manual and is_tcg_card(card):
            print(f"TCG card detected: {card.get('sport')}")
            await thinking.edit(content="🔍 Found it! Pulling TCGPlayer prices… hang tight!")
            tcg_data = await get_justtcg_price(card)
            embed = format_tcg_response(card, query, tcg_data, ebay_links)
        else:
            embed = format_sports_response(card, query, ebay_links, manual=manual)

        await thinking.delete()
        await message.reply(embed=embed)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        await thinking.edit(content="❌ Something went wrong. Try again or check the logs.")

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)

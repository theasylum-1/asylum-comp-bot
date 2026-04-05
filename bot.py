import discord
from discord.ext import commands
import aiohttp
import os
import json
import re
from datetime import datetime
from urllib.parse import quote_plus

# ── Config ─────────────────────────────────────────────────────────────────────
DISCORD_TOKEN    = os.environ["DISCORD_TOKEN"]
OPENAI_API_KEY   = os.environ["OPENAI_API_KEY"]
JUSTTCG_API_KEY  = os.environ["JUSTTCG_API_KEY"]
EBAY_APP_ID      = os.environ.get("EBAY_APP_ID", "").strip()
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
                "STEP 1 — CHECK IF THE CARD IS GRADED:\n"
                "Look for a plastic slab with a label. Grading companies: PSA, BGS, SGC, CGC.\n"
                "If graded, READ THE LABEL CAREFULLY.\n\n"
                "STEP 2 — IDENTIFY THE GAME:\n"
                "- POKEMON: Has 'HP' in top right, energy symbols, Pokémon copyright. Set codes like PFL, OBF, SVI etc.\n"
                "- ONE PIECE: Has ONE PIECE logo, pirate characters. Set codes like OP01-OP11, ST01-ST16, EB01, ME01, ME02.\n"
                "- SPORTS: Real athletes, brands like Topps/Bowman/Panini/Upper Deck.\n"
                "- MAGIC: Fantasy art, mana symbols.\n\n"
                "STEP 3 — READ THE SET CODE from the bottom left corner.\n\n"
                "Return this exact JSON:\n"
                "- 'player': Character or player full name\n"
                "- 'year': Year of the card\n"
                "- 'brand': Pokemon, One Piece, Topps, Bowman, Panini, Upper Deck, etc\n"
                "- 'set': Full set name if visible, otherwise leave empty\n"
                "- 'set_code': SHORT code printed on card bottom left (e.g. 'PFL', 'OP07')\n"
                "- 'variation': Holo, Full Art, Refractor, Auto, Gold Refractor, etc. Empty if base.\n"
                "- 'serial': Print run denominator only (e.g. '75' from '54/75'). Empty if not numbered.\n"
                "- 'card_number': Card number as printed\n"
                "- 'sport': EXACTLY one of: Baseball, Football, Basketball, Hockey, Pokemon, One Piece, Magic, YuGiOh, Other\n"
                "- 'graded': 'true' if in a grading slab, 'false' if raw\n"
                "- 'grading_company': PSA, BGS, SGC, CGC, or empty string\n"
                "- 'grade': Numeric grade (e.g. '10', '9.5'). Empty if not graded.\n\n"
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


async def get_ebay_comps(query: str) -> list:
    if not EBAY_APP_ID:
        print("No eBay App ID configured")
        return []

    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.0.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": query,
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "sortOrder": "EndTimeSoonest",
        "paginationInput.entriesPerPage": "5",
    }

    url = "https://svcs.ebay.com/services/search/FindingService/v1"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json(content_type=None)

        print(f"eBay raw response: {json.dumps(data)[:600]}")

        response = data.get("findCompletedItemsResponse", [{}])[0]
        ack = response.get("ack", [""])[0]
        print(f"eBay ack: {ack}")

        if ack != "Success":
            error = response.get("errorMessage", [{}])[0].get("error", [{}])[0].get("message", ["Unknown"])[0]
            print(f"eBay error message: {error}")
            return []

        items = response.get("searchResult", [{}])[0].get("item", [])
        results = []
        for item in items[:5]:
            try:
                price = float(item["sellingStatus"][0]["currentPrice"][0]["__value__"])
                end_time = item["listingInfo"][0]["endTime"][0][:10]
                results.append({
                    "title": item["title"][0][:60],
                    "price": price,
                    "date": end_time,
                    "url": item["viewItemURL"][0],
                })
            except (KeyError, IndexError, ValueError):
                continue

        print(f"eBay comps found: {len(results)}")
        return results

    except Exception as e:
        print(f"eBay exception: {e}")
        import traceback
        traceback.print_exc()
        return []


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
        embed.add_field(name="🧾 Recent eBay Sales", value=sales_lines, inline=False)
    else:
        embed.add_field(
            name="💰 eBay Comps",
            value=(
                f"[💵 Sold Listings]({ebay_links['ebay_sold']})\n"
                f"[🛒 Active Listings]({ebay_links['ebay_active']})"
            ),
            inline=False
        )

    embed.set_footer(text="The Asylum • Card Comp Bot | eBay sold data")
    return embed


@bot.event
async def on_ready():
    print(f"✅ Asylum Bot is online as {bot.user}")
    print(f"eBay API: {'✅ Connected - ' + EBAY_APP_ID[:20] if EBAY_APP_ID else '❌ Not configured'}")


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

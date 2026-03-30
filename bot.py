import discord
from discord.ext import commands
import aiohttp
import os
import json
import re
from datetime import datetime
from urllib.parse import quote_plus

# Config
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
JUSTTCG_API_KEY = os.environ["JUSTTCG_API_KEY"]
PRICE_CHECK_CHANNEL = "price-check"

# TCG sports that should use JustTCG
TCG_SPORTS = {"pokemon", "one piece", "onepiece", "one-piece", "magic", "yugioh", "yu-gi-oh"}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

processed_messages = set()


async def identify_card(image_urls: list) -> dict:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    content = [
        {
            "type": "text",
            "text": (
                "You are an expert trading card identifier. Carefully examine all provided card images "
                "(there may be a front and back) and return ONLY a JSON object with no markdown or explanation.\n\n"
                "Rules:\n"
                "- 'player': Full name of player or character on the card\n"
                "- 'year': The year of the card. Check the back carefully.\n"
                "- 'brand': The card manufacturer (Topps, Bowman, Panini, Upper Deck, Pokemon, One Piece, etc)\n"
                "- 'set': The specific set name (Brooklyn Collection, Chrome, Prizm, Base Set, Obsidian Flames, etc). No brand/year.\n"
                "- 'variation': Any parallel (Refractor, Holo, Auto, Autograph, Rookie, Gold, Full Art, etc). Empty string if base.\n"
                "- 'serial': Print run denominator only (e.g. '75' from '54/75'). Empty string if not numbered.\n"
                "- 'card_number': Catalog number (e.g. 'AC-OS' or '025/198'). NOT serial number.\n"
                "- 'sport': Use exactly one of: Baseball, Football, Basketball, Hockey, Pokemon, One Piece, Magic, YuGiOh, Other\n\n"
                "All values single-line strings, no newlines. Empty string if unknown."
            ),
        }
    ]

    for url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})

    payload = {
        "model": "gpt-4o",
        "max_tokens": 500,
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


def build_sport_query(card: dict) -> str:
    """Build a cleaner query for TCG cards — player/character + set + variation."""
    parts = []
    if card.get("player"):    parts.append(card["player"])
    if card.get("set"):       parts.append(card["set"])
    if card.get("variation"): parts.append(card["variation"])
    if card.get("card_number"): parts.append(card["card_number"])
    query = " ".join(parts)
    return re.sub(r"[\r\n\t]+", " ", query).strip()


async def get_justtcg_price(card: dict) -> dict | None:
    """
    Query JustTCG API for Pokémon or One Piece card prices.
    Returns dict with price info or None if not found.
    """
    sport = card.get("sport", "").lower()

    # Map sport to JustTCG game slug
    game_map = {
        "pokemon": "pokemon",
        "one piece": "one-piece-card-game",
        "onepiece": "one-piece-card-game",
        "one-piece": "one-piece-card-game",
        "magic": "magic",
        "yugioh": "yugioh",
        "yu-gi-oh": "yugioh",
    }

    game = None
    for key, value in game_map.items():
        if key in sport:
            game = value
            break

    if not game:
        return None

    # Build search query — character name + set is most accurate
    search_query = build_sport_query(card)
    if not search_query:
        search_query = card.get("player", "")

    print(f"JustTCG search: game={game}, q={search_query}")

    headers = {
        "Authorization": f"Bearer {JUSTTCG_API_KEY}",
        "Content-Type": "application/json",
    }

    params = {
        "q": search_query,
        "game": game,
        "limit": 5,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.justtcg.com/v2/cards",
                headers=headers,
                params=params,
            ) as resp:
                status = resp.status
                data = await resp.json()

        print(f"JustTCG status: {status}")
        print(f"JustTCG response: {json.dumps(data)[:500]}")

        if status != 200 or not data.get("data"):
            return None

        # Find best matching card
        cards = data["data"]
        if not cards:
            return None

        # Try to find exact match by card number if we have it
        card_num = card.get("card_number", "").lower()
        best = None
        if card_num:
            for c in cards:
                if card_num in str(c.get("number", "")).lower():
                    best = c
                    break

        if not best:
            best = cards[0]

        # Extract pricing from variants
        variants = best.get("variants", [])
        if not variants:
            return None

        # Get the first variant's prices (usually the base/normal version)
        variant = variants[0]
        prices = variant.get("prices", {})

        return {
            "name": best.get("name", ""),
            "set": best.get("set_name", ""),
            "number": best.get("number", ""),
            "image": best.get("image_url", ""),
            "market_price": prices.get("market"),
            "low_price": prices.get("low"),
            "mid_price": prices.get("mid"),
            "high_price": prices.get("high"),
            "tcgplayer_url": best.get("tcgplayer_url", ""),
        }

    except Exception as e:
        print(f"JustTCG error: {e}")
        return None


def build_ebay_links(query: str) -> dict:
    encoded = quote_plus(query)
    return {
        "ebay_sold": (
            f"https://www.ebay.com/sch/i.html"
            f"?_nkw={encoded}&LH_Complete=1&LH_Sold=1&_sop=13"
        ),
        "ebay_active": (
            f"https://www.ebay.com/sch/i.html"
            f"?_nkw={encoded}&_sop=13"
        ),
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

    # Card details
    if not manual and card:
        details = []
        if card.get("sport"):       details.append(f"**Game:** {card['sport']}")
        if card.get("set"):         details.append(f"**Set:** {card['set']}")
        if card.get("variation"):   details.append(f"**Variant:** {card['variation']}")
        if card.get("card_number"): details.append(f"**Card #:** {card['card_number']}")
        if details:
            embed.add_field(name="📋 Card Details", value="\n".join(details), inline=False)

    # TCGPlayer prices from JustTCG
    if tcg_data:
        price_lines = []
        if tcg_data.get("low_price"):    price_lines.append(f"**Low:** ${tcg_data['low_price']:.2f}")
        if tcg_data.get("market_price"): price_lines.append(f"**Market:** ${tcg_data['market_price']:.2f}")
        if tcg_data.get("high_price"):   price_lines.append(f"**High:** ${tcg_data['high_price']:.2f}")

        if price_lines:
            value = "\n".join(price_lines)
            if tcg_data.get("tcgplayer_url"):
                value += f"\n[View on TCGPlayer]({tcg_data['tcgplayer_url']})"
            embed.add_field(name="💰 TCGPlayer Prices", value=value, inline=False)

        if tcg_data.get("image"):
            embed.set_thumbnail(url=tcg_data["image"])
    else:
        embed.add_field(
            name="💰 TCGPlayer Prices",
            value="Could not find this card in TCGPlayer database.\nTry adding a caption with the exact card name.",
            inline=False
        )

    # eBay links as backup
    embed.add_field(
        name="🔗 eBay Comps",
        value=(
            f"[💵 Sold Listings]({ebay_links['ebay_sold']})  |  "
            f"[🛒 Active Listings]({ebay_links['ebay_active']})"
        ),
        inline=False
    )

    embed.set_footer(text="The Asylum • Card Comp Bot | Prices from TCGPlayer via JustTCG")
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
                    "or add a caption like:\n`Charizard ex Obsidian Flames Full Art`"
                )
            )
            return

        ebay_links = build_ebay_links(query)

        # Route to TCG or sports handler
        if not manual and is_tcg_card(card):
            print(f"Routing to TCG handler for sport: {card.get('sport')}")
            await thinking.edit(content="🔍 Found the card! Pulling TCGPlayer prices… hang tight!")
            tcg_data = await get_justtcg_price(card)
            embed = format_tcg_response(card, query, tcg_data, ebay_links, manual=manual)
        else:
            print(f"Routing to sports handler")
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

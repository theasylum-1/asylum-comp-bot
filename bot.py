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

# ── Pokemon set code -> full set name ──────────────────────────────────────────
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

# ── One Piece set code -> full set name ────────────────────────────────────────
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
    "EB01": "Extra Booster: Memorial Collection",
    "ME01": "Premium Card Collection",
    "ME02": "Phantasmal Flames",
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
processed_messages = set()


# ── Helpers ────────────────────────────────────────────────────────────────────
def resolve_set_code(set_code: str, sport: str) -> str:
    code = set_code.upper().strip()
    if "pokemon" in sport.lower():
        return POKEMON_SET_CODES.get(code, "")
    elif "one piece" in sport.lower() or "onepiece" in sport.lower():
        return ONE_PIECE_SET_CODES.get(code, "")
    return ""


def is_tcg_card(card: dict) -> bool:
    return any(t in card.get("sport", "").lower() for t in TCG_SPORTS)


def build_search_query(card: dict) -> str:
    parts = []
    if card.get("year"):      parts.append(card["year"])
    if card.get("player"):    parts.append(card["player"])
    if card.get("brand"):     parts.append(card["brand"])
    if card.get("set"):       parts.append(card["set"])
    if card.get("variation"): parts.append(card["variation"])
    if card.get("serial"):    parts.append(f"/{card['serial']}")
    return re.sub(r"[\r\n\t]+", " ", " ".join(parts)).strip()


def build_ebay_links(query: str) -> dict:
    encoded = quote_plus(query)
    return {
        "sold":   f"https://www.ebay.com/sch/i.html?_nkw={encoded}&LH_Complete=1&LH_Sold=1&_sop=13",
        "active": f"https://www.ebay.com/sch/i.html?_nkw={encoded}&_sop=13",
    }


# ── Card identification ────────────────────────────────────────────────────────
async def identify_card(image_urls: list) -> dict:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    content = [
        {
            "type": "text",
            "text": (
                "You are an expert trading card identifier. Examine ALL images carefully and return ONLY a JSON object — no markdown.\n\n"

                "STEP 1 — DETERMINE THE GAME by reading the copyright line and logo:\n"
                "- POKEMON: Copyright says '© Pokémon / Nintendo / Creatures / GAME FREAK'. Has HP in top right, energy symbols.\n"
                "- ONE PIECE: Has ONE PIECE logo. Set codes like OP01-OP10, ST01-ST16, EB01, ME01, ME02.\n"
                "- SPORTS: Real athletes. Topps, Bowman, Panini, Upper Deck branding.\n"
                "- MAGIC: Mana symbols, 'Magic: The Gathering' branding.\n\n"

                "STEP 2 — READ THE SET CODE printed in small text at the bottom of the card (e.g. 'PFL', 'OBF', 'OP07'). "
                "This is the MOST RELIABLE way to identify the exact set. Always read this code directly from the card.\n\n"

                "STEP 3 — IDENTIFY THE VARIATION TYPE:\n"
                "- Pokemon: Illustration Rare (IR), Special Illustration Rare (SIR), Full Art, Alt Art, Rainbow Rare, Gold Rare, Secret Rare, Holo Rare, etc.\n"
                "- One Piece: Leader, Character, Event, Stage, Don!!, Super Rare (SR), Secret Rare (SEC), Alternate Art, Parallel, etc.\n"
                "- Sports: Refractor, Prizm, Auto, Patch, Rookie, Gold, Numbered parallel, etc.\n\n"

                "Return this exact JSON:\n"
                "{'player': 'character or player full name',\n"
                " 'year': 'year from copyright line at bottom',\n"
                " 'brand': 'Pokemon / One Piece / Topps / Bowman / Panini / Upper Deck / etc',\n"
                " 'set': 'FULL set name e.g. Phantasmal Flames or Obsidian Flames',\n"
                " 'set_code': 'short code printed on card e.g. PFL or OP07 — read this directly from the card',\n"
                " 'variation': 'Illustration Rare / Special Illustration Rare / Holo / Full Art / Alt Art / Refractor / Auto / etc — empty if base',\n"
                " 'serial': 'print run denominator only e.g. 75 from 54/75 — empty if not numbered',\n"
                " 'card_number': 'number as printed e.g. 107/094 or L-001',\n"
                " 'sport': 'EXACTLY one of: Baseball Football Basketball Hockey Pokemon One Piece Magic YuGiOh Other'}\n\n"
                "All values single-line strings. Empty string if unknown."
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
    if set_code:
        resolved = resolve_set_code(set_code, card.get("sport", ""))
        if resolved:
            print(f"Resolved set code '{set_code}' -> '{resolved}'")
            card["set"] = resolved

    return card


# ── JustTCG pricing ────────────────────────────────────────────────────────────
async def get_justtcg_price(card: dict) -> dict | None:
    sport = card.get("sport", "").lower()
    game_map = {
        "pokemon":   "pokemon",
        "one piece": "one-piece-card-game",
        "onepiece":  "one-piece-card-game",
        "one-piece": "one-piece-card-game",
        "magic":     "magic",
        "yugioh":    "yugioh",
    }
    game = next((v for k, v in game_map.items() if k in sport), None)
    if not game:
        return None

    headers = {"Authorization": f"Bearer {JUSTTCG_API_KEY}", "Content-Type": "application/json"}
    player      = card.get("player", "")
    set_name    = card.get("set", "")
    card_number = card.get("card_number", "")

    searches = []
    if player and card_number: searches.append(f"{player} {card_number}")
    if player and set_name:    searches.append(f"{player} {set_name}")
    if player:                 searches.append(player)

    for q in searches:
        print(f"JustTCG: game={game}, q={q}")
        result = await _justtcg_fetch(headers, game, q, card_number)
        if result:
            return result
    return None


async def _justtcg_fetch(headers: dict, game: str, query: str, card_number: str = "") -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.justtcg.com/v2/cards",
                headers=headers,
                params={"q": query, "game": game, "limit": 10},
            ) as resp:
                status = resp.status
                data   = await resp.json()

        print(f"JustTCG status={status}, results={len(data.get('data', []))}")
        if status != 200 or not data.get("data"):
            return None

        cards = data["data"]
        best  = None

        if card_number:
            clean = card_number.replace(" ", "").lower()
            for c in cards:
                if clean in str(c.get("number", "")).replace(" ", "").lower():
                    best = c
                    print(f"Matched by number: {c.get('name')} {c.get('number')}")
                    break

        if not best:
            best = cards[0]
            print(f"Using first result: {best.get('name')} {best.get('number')}")

        variants = best.get("variants", [])
        if not variants:
            return None

        prices = variants[0].get("prices", {})
        return {
            "name":          best.get("name", ""),
            "set":           best.get("set_name", ""),
            "number":        best.get("number", ""),
            "image":         best.get("image_url", ""),
            "market_price":  prices.get("market"),
            "low_price":     prices.get("low"),
            "high_price":    prices.get("high"),
            "tcgplayer_url": best.get("tcgplayer_url", ""),
        }
    except Exception as e:
        print(f"JustTCG error: {e}")
        return None


# ── Embed builders ─────────────────────────────────────────────────────────────
def format_tcg_embed(card: dict, query: str, tcg: dict | None, links: dict, manual: bool = False) -> discord.Embed:
    name = (query if manual else card.get("player", "")) or query
    embed = discord.Embed(title=f"🃏 {name}", color=discord.Color.gold(), timestamp=datetime.utcnow())
    embed.add_field(name="🔍 Search", value=f"`{query}`" + (" *(manual)*" if manual else ""), inline=False)

    if not manual:
        details = []
        if card.get("sport"):       details.append(f"**Game:** {card['sport']}")
        if card.get("set"):         details.append(f"**Set:** {card['set']}")
        if card.get("set_code"):    details.append(f"**Set Code:** {card['set_code']}")
        if card.get("variation"):   details.append(f"**Variant:** {card['variation']}")
        if card.get("card_number"): details.append(f"**Card #:** {card['card_number']}")
        if card.get("year"):        details.append(f"**Year:** {card['year']}")
        if details:
            embed.add_field(name="📋 Card Details", value="\n".join(details), inline=False)

    if tcg:
        lines = []
        if tcg.get("low_price"):    lines.append(f"**Low:** ${tcg['low_price']:.2f}")
        if tcg.get("market_price"): lines.append(f"**Market:** ${tcg['market_price']:.2f}")
        if tcg.get("high_price"):   lines.append(f"**High:** ${tcg['high_price']:.2f}")
        val = "\n".join(lines) if lines else "No price data available"
        if tcg.get("tcgplayer_url"):
            val += f"\n[View on TCGPlayer]({tcg['tcgplayer_url']})"
        embed.add_field(name="💰 TCGPlayer Prices", value=val, inline=False)
        if tcg.get("image"):
            embed.set_thumbnail(url=tcg["image"])
    else:
        embed.add_field(
            name="💰 TCGPlayer Prices",
            value="Card not found in database.\nTry adding a caption with the exact card name and set.",
            inline=False
        )

    embed.add_field(
        name="🔗 eBay Comps",
        value=f"[💵 Sold]({links['sold']})  |  [🛒 Active]({links['active']})",
        inline=False
    )
    embed.set_footer(text="The Asylum • Card Comp Bot | TCGPlayer prices via JustTCG")
    return embed


def format_sports_embed(card: dict, query: str, links: dict, manual: bool = False) -> discord.Embed:
    name = " ".join(filter(None, [
        card.get("year"), card.get("player"), card.get("brand"),
        card.get("set"), card.get("variation"),
        f"/{card['serial']}" if card.get("serial") else ""
    ])) or query
    if manual: name = query

    embed = discord.Embed(title=f"🃏 {name}", color=discord.Color.blue(), timestamp=datetime.utcnow())
    embed.add_field(name="🔍 Search", value=f"`{query}`" + (" *(manual)*" if manual else ""), inline=False)

    if not manual:
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
        name="💰 eBay Comps",
        value=f"[💵 Sold Listings]({links['sold']})\n[🛒 Active Listings]({links['active']})",
        inline=False
    )
    embed.set_footer(text="The Asylum • Card Comp Bot")
    return embed


# ── Bot events ─────────────────────────────────────────────────────────────────
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
        for old in list(processed_messages)[:500]:
            processed_messages.discard(old)

    channel_match = (
        message.channel.name == PRICE_CHECK_CHANNEL
        or str(message.channel.id) == PRICE_CHECK_CHANNEL
    )
    if not channel_match:
        await bot.process_commands(message)
        return

    images = [a for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
    if not images:
        await bot.process_commands(message)
        return

    thinking = await message.reply("🔍 Identifying your card… hang tight!")

    try:
        image_urls = [a.url for a in images[:2]]
        caption    = message.content.strip() or None
        manual     = bool(caption)

        if manual:
            query = re.sub(r"[\r\n\t]+", " ", caption).strip()
            card  = {}
            print(f"Manual query: {repr(query)}")
        else:
            card  = await identify_card(image_urls)
            query = build_search_query(card)
            print(f"Card identified: {card}")
            print(f"Search query: {repr(query)}")

        if not query.strip():
            await thinking.edit(content="❌ Couldn't identify the card. Try posting front **and back** together, or add a caption.")
            return

        links = build_ebay_links(query)

        if not manual and is_tcg_card(card):
            await thinking.edit(content="🔍 Found the card! Pulling TCGPlayer prices…")
            tcg_data = await get_justtcg_price(card)
            embed    = format_tcg_embed(card, query, tcg_data, links)
        else:
            embed = format_sports_embed(card, query, links, manual=manual)

        await thinking.delete()
        await message.reply(embed=embed)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        await thinking.edit(content="❌ Something went wrong. Try again or check the logs.")

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)

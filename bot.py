import discord
from discord.ext import commands
import aiohttp
import base64
import os
import json
import re
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.environ["DISCORD_TOKEN"]
OPENAI_API_KEY  = os.environ["OPENAI_API_KEY"]
EBAY_APP_ID     = os.environ["EBAY_APP_ID"]

# The name (or ID) of the channel where members post card photos
PRICE_CHECK_CHANNEL = "price-check"   # change if your channel has a different name
# ──────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ── Step 1 — Ask OpenAI Vision to identify the card ───────────────────────────
async def identify_card(image_url: str) -> dict:
    """
    Send the image to GPT-4o and get back structured card info.
    Returns a dict with keys: player, year, brand, set, variation, card_number
    """
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o",
        "max_tokens": 300,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Look at this trading card image and return ONLY a JSON object "
                            "(no markdown, no explanation) with these keys:\n"
                            "  player      - full name of player or character\n"
                            "  year        - year printed on card (or best guess)\n"
                            "  brand       - manufacturer (Topps, Panini, Pokemon, etc.)\n"
                            "  set         - set name (e.g. Chrome, Prizm, Base Set)\n"
                            "  variation   - parallel/variation (e.g. Refractor, Holo, PSA 10) or empty string\n"
                            "  card_number - card number if visible or empty string\n"
                            "  sport       - sport or game (Baseball, Football, Pokemon, One Piece, etc.)\n"
                            "If any field cannot be determined write an empty string."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            data = await resp.json()

    raw = data["choices"][0]["message"]["content"].strip()
    # Strip possible ```json fences
    raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


# ── Step 2 — Build a clean eBay search query from the card info ───────────────
# 2. Build query
        print(f"Card identified: {card}")
        query = build_ebay_query(card)
        print(f"eBay query: {repr(query)}")
    parts = []
    if card.get("year"):        parts.append(card["year"])
    if card.get("player"):      parts.append(card["player"])
    if card.get("brand"):       parts.append(card["brand"])
    if card.get("set"):         parts.append(card["set"])
    if card.get("variation"):   parts.append(card["variation"])
    if card.get("card_number"): parts.append(f"#{card['card_number']}")
    query = " ".join(parts)
    # Strip any newlines or carriage returns
    query = query.replace("\n", " ").replace("\r", " ").strip()
    return query


# ── Step 3 — Search eBay SOLD listings via Finding API ───────────────────────
async def get_ebay_comps(query: str) -> list[dict]:
    """
    Uses eBay's Finding API (completedItems=true) to get the last 5 sold listings.
    Returns a list of dicts: {title, price, date, url}
    """
    params = {
        "OPERATION-NAME":        "findCompletedItems",
        "SERVICE-VERSION":       "1.0.0",
        "SECURITY-APPNAME":      EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT":  "JSON",
        "REST-PAYLOAD":          "",
        "keywords":              query,
        "itemFilter(0).name":    "SoldItemsOnly",
        "itemFilter(0).value":   "true",
        "sortOrder":             "EndTimeSoonest",
        "paginationInput.entriesPerPage": "5",
    }

    url = "https://svcs.ebay.com/services/search/FindingService/v1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json(content_type=None)

    try:
        items = (
            data["findCompletedItemsResponse"][0]
               ["searchResult"][0]
               ["item"]
        )
    except (KeyError, IndexError):
        return []

    results = []
    for item in items[:5]:
        try:
            price = float(item["sellingStatus"][0]["currentPrice"][0]["__value__"])
            end_time = item["listingInfo"][0]["endTime"][0][:10]   # YYYY-MM-DD
            results.append({
                "title": item["title"][0],
                "price": price,
                "date":  end_time,
                "url":   item["viewItemURL"][0],
            })
        except (KeyError, IndexError, ValueError):
            continue

    return results


# ── Step 4 — Format and send the Discord reply ────────────────────────────────
def format_response(card: dict, query: str, comps: list[dict]) -> discord.Embed:
    if comps:
        prices = [c["price"] for c in comps]
        avg    = sum(prices) / len(prices)
        color  = discord.Color.green()
    else:
        avg   = None
        color = discord.Color.orange()

    # Card title line
    card_name = " ".join(filter(None, [
        card.get("year"), card.get("player"),
        card.get("brand"), card.get("set"), card.get("variation")
    ])) or query

    embed = discord.Embed(
        title=f"📊 eBay Comps — {card_name}",
        color=color,
        timestamp=datetime.utcnow(),
    )

    embed.add_field(name="🔍 Search Used", value=f"`{query}`", inline=False)

    if avg is not None:
        embed.add_field(name="💰 Avg Sold Price", value=f"**${avg:.2f}**", inline=True)
        embed.add_field(name="📦 Sales Found",    value=str(len(comps)),   inline=True)

    if comps:
        sales_lines = "\n".join(
            f"[${c['price']:.2f} — {c['date']}]({c['url']})" for c in comps
        )
        embed.add_field(name="🧾 Last Sales", value=sales_lines, inline=False)
    else:
        embed.add_field(
            name="⚠️ No Sales Found",
            value="Try posting a clearer image or manually search eBay.",
            inline=False,
        )

    embed.set_footer(text="The Asylum • eBay Comp Bot")
    return embed


# ── Bot events ────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Asylum Bot is online as {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    # Ignore the bot's own messages
    if message.author.bot:
        return

    # Only act in the price-check channel
    channel_match = (
        message.channel.name == PRICE_CHECK_CHANNEL
        or str(message.channel.id) == PRICE_CHECK_CHANNEL
    )
    if not channel_match:
        await bot.process_commands(message)
        return

    # Only act if there's an image attachment
    image_attachments = [
        a for a in message.attachments
        if a.content_type and a.content_type.startswith("image/")
    ]
    if not image_attachments:
        await bot.process_commands(message)
        return

    # Acknowledge immediately so the user knows we're working
    thinking = await message.reply("🔍 Identifying your card and pulling eBay comps… hang tight!")

    try:
        image_url = image_attachments[0].url

        # 1. Identify card
        card = await identify_card(image_url)

        # 2. Build query
        query = build_ebay_query(card)
        if not query.strip():
            await thinking.edit(content="❌ Couldn't read the card from that image. Try a clearer photo!")
            return

        # 3. Get comps
        comps = await get_ebay_comps(query)

        # 4. Build and send embed
        embed = format_response(card, query, comps)
        await thinking.delete()
        await message.reply(embed=embed)

    except Exception as e:
        print(f"Error: {e}")
        await thinking.edit(content="❌ Something went wrong pulling comps. Try again or check the logs.")

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)

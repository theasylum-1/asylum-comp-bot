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
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
PRICE_CHECK_CHANNEL = "price-check"

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
                "- 'set': The specific set name (Brooklyn Collection, Chrome, Prizm, Heritage, Base Set, etc). No brand/year.\n"
                "- 'variation': Any parallel (Refractor, Holo, Auto, Autograph, Rookie, Gold, etc). Empty string if base.\n"
                "- 'serial': Print run denominator only (e.g. '75' from '54/75'). Empty string if not numbered.\n"
                "- 'card_number': Catalog number (e.g. 'AC-OS'). NOT serial number.\n"
                "- 'sport': Baseball, Football, Basketball, Pokemon, One Piece, etc\n\n"
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


def build_ebay_query(card: dict) -> str:
    parts = []
    if card.get("year"):      parts.append(card["year"])
    if card.get("player"):    parts.append(card["player"])
    if card.get("brand"):     parts.append(card["brand"])
    if card.get("set"):       parts.append(card["set"])
    if card.get("variation"): parts.append(card["variation"])
    if card.get("serial"):    parts.append(f"/{card['serial']}")
    query = " ".join(parts)
    return re.sub(r"[\r\n\t]+", " ", query).strip()


async def get_ebay_comps(query: str) -> list:
    """
    Give Claude the exact eBay sold listings URL and ask it to extract prices.
    """
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    # Build the exact eBay sold listings URL
    encoded = quote_plus(query)
    ebay_url = (
        f"https://www.ebay.com/sch/i.html"
        f"?_nkw={encoded}"
        f"&LH_Complete=1"
        f"&LH_Sold=1"
        f"&_sop=13"
        f"&_ipg=10"
    )

    print(f"eBay URL: {ebay_url}")

    tools = [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 2,
        }
    ]

    prompt = f"""I need you to find recent sold prices for this trading card: "{query}"

Please search for sold eBay listings using this exact URL: {ebay_url}

Also try searching: site:ebay.com "{query}" sold

Extract the sold listing data and respond with ONLY a JSON array in this exact format:
[
  {{"title": "listing title", "price": 25.00, "date": "Mar 2025", "url": "https://www.ebay.com/itm/..."}}
]

Important:
- price must be a number (no $ sign)  
- Up to 5 results maximum
- Only SOLD/COMPLETED listings with actual sale prices
- Respond with ONLY the JSON array, nothing else
- If no sold listings found, respond with exactly: []"""

    messages = [{"role": "user", "content": prompt}]

    payload = {
        "model": "claude-sonnet-4-5-20250929",
        "max_tokens": 2000,
        "tools": tools,
        "messages": messages,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        ) as resp:
            status = resp.status
            data = await resp.json()

    print(f"API status: {status}, stop_reason: {data.get('stop_reason')}")
    block_types = [b.get("type") for b in data.get("content", [])]
    print(f"Block types: {block_types}")

    if status != 200:
        print(f"API error: {data}")
        return []

    full_text = " ".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    ).strip()

    print(f"Full text: {full_text[:800]}")

    if not full_text:
        return []

    raw = re.sub(r"^```json\s*|^```\s*|```$", "", full_text, flags=re.MULTILINE).strip()
    match = re.search(r'\[.*?\]', raw, re.DOTALL)
    if not match:
        print("No JSON array found")
        return []

    try:
        comps = json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return []

    results = []
    for c in comps:
        try:
            price = float(str(c.get("price", 0)).replace(",", "").replace("$", ""))
            if price <= 0:
                continue
            results.append({
                "title": str(c.get("title", ""))[:60],
                "price": price,
                "date": str(c.get("date", "N/A")),
                "url": str(c.get("url", "")),
            })
        except (ValueError, TypeError):
            continue

    return results


def format_response(card: dict, query: str, comps: list, manual: bool = False) -> discord.Embed:
    if comps:
        prices = [c["price"] for c in comps]
        avg = sum(prices) / len(prices)
        color = discord.Color.green()
    else:
        avg = None
        color = discord.Color.orange()

    if manual:
        card_name = query
    else:
        card_name = " ".join(filter(None, [
            card.get("year"), card.get("player"),
            card.get("brand"), card.get("set"), card.get("variation"),
            f"/{card['serial']}" if card.get("serial") else ""
        ])) or query

    embed = discord.Embed(
        title=f"📊 eBay Comps — {card_name}",
        color=color,
        timestamp=datetime.utcnow(),
    )

    embed.add_field(
        name="🔍 Search Used",
        value=f"`{query}`" + (" *(manual)*" if manual else ""),
        inline=False
    )

    if avg is not None:
        embed.add_field(name="💰 Avg Sold Price", value=f"**${avg:.2f}**", inline=True)
        embed.add_field(name="📦 Sales Found", value=str(len(comps)), inline=True)

    if comps:
        sales_lines = "\n".join(
            f"[${c['price']:.2f} — {c['date']}]({c['url']})" for c in comps
        )
        embed.add_field(name="🧾 Last Sales", value=sales_lines, inline=False)
    else:
        embed.add_field(
            name="⚠️ No Sales Found",
            value=(
                "Try posting clearer front **and back** photos, "
                "or add a caption like:\n`2021 Tarik Skubal Topps Chrome Rookie`"
            ),
            inline=False,
        )

    embed.set_footer(text="The Asylum • eBay Comp Bot")
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

    thinking = await message.reply("🔍 Identifying your card and pulling eBay comps… hang tight!")

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
            query = build_ebay_query(card)
            print(f"eBay query: {repr(query)}")

        if not query.strip():
            await thinking.edit(
                content=(
                    "❌ Couldn't identify the card. Try posting front **and back** together, "
                    "or add a caption like:\n`2021 Tarik Skubal Topps Chrome Rookie`"
                )
            )
            return

        comps = await get_ebay_comps(query)
        print(f"Comps found: {len(comps)}")

        embed = format_response(card, query, comps, manual=manual)
        await thinking.delete()
        await message.reply(embed=embed)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        await thinking.edit(content="❌ Something went wrong. Try again or check the logs.")

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)

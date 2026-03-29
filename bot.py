import discord
from discord.ext import commands
import aiohttp
import os
import json
import re
from datetime import datetime

# Config
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
PRICE_CHECK_CHANNEL = "price-check"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


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
                "- 'year': The year of the card. Check the back carefully — it is often printed there.\n"
                "- 'brand': The card manufacturer (Topps, Bowman, Panini, Upper Deck, Pokemon, One Piece, etc)\n"
                "- 'set': The specific set name (Brooklyn Collection, Chrome, Prizm, Heritage, Base Set, etc). Do NOT include the brand name or year.\n"
                "- 'variation': Any parallel or special version (Refractor, Holo, Auto, Autograph, Rookie, Gold, etc). Leave empty string if base.\n"
                "- 'serial': If the card has a print run like '54/75' or '23/99', put ONLY the denominator (e.g. '75'). Leave empty string if not numbered.\n"
                "- 'card_number': The card's catalog number (e.g. 'AC-OS'). NOT the serial number.\n"
                "- 'sport': Baseball, Football, Basketball, Pokemon, One Piece, etc\n\n"
                "All values must be single-line strings with no newlines. Return empty string for any field you cannot determine."
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
    query = re.sub(r"[\r\n\t]+", " ", query).strip()
    return query


async def get_ebay_comps(query: str) -> list:
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    prompt = (
        f'Search eBay completed/sold listings for this trading card: "{query}"\n\n'
        f'Find recent sold prices on eBay for this card. '
        f'Return ONLY a JSON array (no markdown, no extra text) with up to 5 sold listings. '
        f'Each object must have: '
        f'"title" (string, max 60 chars), '
        f'"price" (number, no $ sign), '
        f'"date" (string), '
        f'"url" (string, full eBay URL). '
        f'If no sold listings found, return []'
    )

    messages = [{"role": "user", "content": prompt}]

    payload = {
        "model": "claude-sonnet-4-5-20251001",
        "max_tokens": 1500,
        "tools": [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }
        ],
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

    print(f"Anthropic API status: {status}")
    print(f"Full API response: {json.dumps(data)[:1000]}")

    if status != 200:
        print(f"API Error: {data}")
        return []

    stop_reason = data.get("stop_reason")
    content_blocks = data.get("content", [])
    print(f"Stop reason: {stop_reason}")
    print(f"Content block types: {[b.get('type') for b in content_blocks]}")

    # If web search was used, continue conversation for final answer
    if stop_reason in ("tool_use", "pause_turn") or any(
        b.get("type") in ("server_tool_use", "web_search_tool_result")
        for b in content_blocks
    ):
        messages.append({"role": "assistant", "content": content_blocks})

        payload2 = {
            "model": "claude-sonnet-4-5-20251001",
            "max_tokens": 1500,
            "tools": [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 3,
                }
            ],
            "messages": messages,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload2,
            ) as resp2:
                status2 = resp2.status
                data = await resp2.json()

        print(f"Second API status: {status2}")
        print(f"Second response: {json.dumps(data)[:1000]}")

    # Extract text
    full_text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            full_text += block.get("text", "")

    print(f"Final text: {full_text[:500]}")

    if not full_text.strip():
        return []

    raw = full_text.strip()
    raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()

    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        return []

    comps = json.loads(match.group())

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
                "or add a caption like:\n`2022 Topps Brooklyn Collection Ozzie Smith Auto /75`"
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
            print(f"Manual query from caption: {repr(query)}")
        else:
            card = await identify_card(image_urls)
            print(f"Card identified: {card}")
            query = build_ebay_query(card)
            print(f"eBay query: {repr(query)}")

        if not query.strip():
            await thinking.edit(
                content=(
                    "❌ Couldn't identify the card from that image.\n"
                    "Try posting the **front and back** together, or add a caption like:\n"
                    "`2022 Topps Brooklyn Collection Ozzie Smith Auto /75`"
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
        await thinking.edit(content="❌ Something went wrong pulling comps. Try again or check the logs.")

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)

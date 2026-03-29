import discord
from discord.ext import commands
import aiohttp
from bs4 import BeautifulSoup
import os
import json
import re
from datetime import datetime
from urllib.parse import quote_plus

# Config
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
PRICE_CHECK_CHANNEL = "price-check"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def identify_card(image_url: str) -> dict:
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
                            "(no markdown, no explanation) with these keys: "
                            "player, year, brand, set, variation, card_number, sport. "
                            "All values must be single-line strings with no newlines. "
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
    raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
    card = json.loads(raw)

    for key in card:
        if isinstance(card[key], str):
            card[key] = card[key].replace("\n", " ").replace("\r", " ").strip()

    return card


def build_ebay_query(card: dict) -> str:
    parts = []
    if card.get("year"):        parts.append(card["year"])
    if card.get("player"):      parts.append(card["player"])
    if card.get("brand"):       parts.append(card["brand"])
    if card.get("set"):         parts.append(card["set"])
    if card.get("variation"):   parts.append(card["variation"])
    if card.get("card_number"): parts.append(f"#{card['card_number']}")
    query = " ".join(parts)
    query = re.sub(r"[\r\n\t]+", " ", query).strip()
    return query


async def get_ebay_comps(query: str) -> list:
    """
    Scrapes eBay completed/sold listings directly — no API key needed.
    Returns list of dicts: {title, price, date, url}
    """
    encoded = quote_plus(query)
    # LH_Complete=1 = completed listings, LH_Sold=1 = sold only, _sop=13 = newest first
    url = (
        f"https://www.ebay.com/sch/i.html"
        f"?_nkw={encoded}"
        f"&LH_Complete=1"
        f"&LH_Sold=1"
        f"&_sop=13"
        f"&_ipg=10"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(".s-item__info")

    results = []
    for item in items:
        try:
            title_el = item.select_one(".s-item__title")
            price_el = item.select_one(".s-item__price")
            date_el  = item.select_one(".s-item__ended-date, .s-item__listingDate")
            link_el  = item.select_one("a.s-item__link")

            if not title_el or not price_el or not link_el:
                continue

            title = title_el.get_text(strip=True)
            if title.lower() == "shop on ebay":
                continue

            # Parse price — handle ranges like "$10.00 to $20.00" by taking first
            price_text = price_el.get_text(strip=True)
            price_match = re.search(r"\$?([\d,]+\.?\d*)", price_text.replace(",", ""))
            if not price_match:
                continue
            price = float(price_match.group(1).replace(",", ""))

            date = date_el.get_text(strip=True) if date_el else "N/A"
            link = link_el["href"].split("?")[0]  # clean URL

            results.append({
                "title": title[:60] + "…" if len(title) > 60 else title,
                "price": price,
                "date": date,
                "url": link,
            })

            if len(results) >= 5:
                break

        except Exception:
            continue

    return results


def format_response(card: dict, query: str, comps: list) -> discord.Embed:
    if comps:
        prices = [c["price"] for c in comps]
        avg = sum(prices) / len(prices)
        color = discord.Color.green()
    else:
        avg = None
        color = discord.Color.orange()

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
        embed.add_field(name="📦 Sales Found", value=str(len(comps)), inline=True)

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
        image_url = image_attachments[0].url
        print(f"Image URL: {repr(image_url)}")

        # 1. Identify card
        card = await identify_card(image_url)
        print(f"Card identified: {card}")

        # 2. Build query
        query = build_ebay_query(card)
        print(f"eBay query: {repr(query)}")

        if not query.strip():
            await thinking.edit(content="❌ Couldn't read the card from that image. Try a clearer photo!")
            return

        # 3. Scrape comps
        comps = await get_ebay_comps(query)
        print(f"Comps found: {len(comps)}")

        # 4. Send embed
        embed = format_response(card, query, comps)
        await thinking.delete()
        await message.reply(embed=embed)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        await thinking.edit(content="❌ Something went wrong pulling comps. Try again or check the logs.")

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)

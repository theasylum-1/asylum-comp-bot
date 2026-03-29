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


async def identify_card(image_urls: list) -> dict:
    """
    Send up to 2 card images (front + back) to GPT-4o for identification.
    """
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # Build content array — text prompt first, then all images
    content = [
        {
            "type": "text",
            "text": (
                "You are an expert trading card identifier. Carefully examine all provided card images "
                "(there may be a front and back) and return ONLY a JSON object with no markdown or explanation.\n\n"
                "Rules:\n"
                "- 'player': Full name of player or character on the card\n"
                "- 'year': The year of the card. Check the back of the card carefully — it is often printed there. Make your best guess based on design era if not visible.\n"
                "- 'brand': The card manufacturer (Topps, Bowman, Panini, Upper Deck, Pokemon, One Piece, etc)\n"
                "- 'set': The specific set name (Brooklyn Collection, Chrome, Prizm, Heritage, Base Set, etc). Do NOT include the brand name or year here. Check the back of the card.\n"
                "- 'variation': Any parallel or special version (Refractor, Holo, Auto, Autograph, Rookie, Gold, etc). Leave empty string if base.\n"
                "- 'serial': If the card has a print run like '54/75' or '23/99', put ONLY the denominator total (e.g. '75' or '99'). Leave empty string if not numbered.\n"
                "- 'card_number': The card's catalog number (e.g. 'AC-OS'). NOT the serial number.\n"
                "- 'sport': Baseball, Football, Basketball, Pokemon, One Piece, etc\n\n"
                "All values must be single-line strings with no newlines. Return empty string for any field you truly cannot determine."
            ),
        }
    ]

    # Add each image
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
    encoded = quote_plus(query)
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

            price_text = price_el.get_text(strip=True)
            price_match = re.search(r"\$?([\d,]+\.?\d*)", price_text.replace(",", ""))
            if not price_match:
                continue
            price = float(price_match.group(1).replace(",", ""))

            date = date_el.get_text(strip=True) if date_el else "N/A"
            link = link_el["href"].split("?")[0]

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

    # Only act if there's at least one image
    image_attachments = [
        a for a in message.attachments
        if a.content_type and a.content_type.startswith("image/")
    ]
    if not image_attachments:
        await bot.process_commands(message)
        return

    thinking = await message.reply("🔍 Identifying your card and pulling eBay comps… hang tight!")

    try:
        # Collect up to 2 image URLs (front + back)
        image_urls = [a.url for a in image_attachments[:2]]
        print(f"Images received: {len(image_urls)}")

        # Check if the member typed a manual search caption
        caption = message.content.strip() if message.content.strip() else None
        manual = False

        if caption:
            # Use the caption directly as the eBay query
            query = re.sub(r"[\r\n\t]+", " ", caption).strip()
            card = {}
            manual = True
            print(f"Manual query from caption: {repr(query)}")
        else:
            # Use AI to identify the card from image(s)
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

        # Scrape comps
        comps = await get_ebay_comps(query)
        print(f"Comps found: {len(comps)}")

        # Send embed
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

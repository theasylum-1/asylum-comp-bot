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


def build_links(query: str) -> dict:
    encoded = quote_plus(query)
    return {
        "130point": f"https://130point.com/sales/?q={encoded}",
        "ebay_sold": (
            f"https://www.ebay.com/sch/i.html"
            f"?_nkw={encoded}"
            f"&LH_Complete=1"
            f"&LH_Sold=1"
            f"&_sop=13"
        ),
    }


def format_response(card: dict, query: str, links: dict, manual: bool = False) -> discord.Embed:
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

    # Card details if identified by AI
    if not manual and card:
        details = []
        if card.get("sport"):     details.append(f"**Sport:** {card['sport']}")
        if card.get("brand"):     details.append(f"**Brand:** {card['brand']}")
        if card.get("set"):       details.append(f"**Set:** {card['set']}")
        if card.get("year"):      details.append(f"**Year:** {card['year']}")
        if card.get("variation"): details.append(f"**Variation:** {card['variation']}")
        if card.get("serial"):    details.append(f"**Print Run:** /{card['serial']}")
        if card.get("card_number"): details.append(f"**Card #:** {card['card_number']}")
        if details:
            embed.add_field(name="📋 Card Details", value="\n".join(details), inline=False)

    # Comp links
    embed.add_field(
        name="💰 Check Sold Comps",
        value=(
            f"[🔗 130point Sold Sales]({links['130point']})\n"
            f"[🔗 eBay Sold Listings]({links['ebay_sold']})"
        ),
        inline=False
    )

    embed.set_footer(text="The Asylum • eBay Comp Bot | Click links above for sold prices")
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
                    "or add a caption like:\n`2022 Topps Brooklyn Collection Ozzie Smith Auto /75`"
                )
            )
            return

        links = build_links(query)
        embed = format_response(card, query, links, manual=manual)
        await thinking.delete()
        await message.reply(embed=embed)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        await thinking.edit(content="❌ Something went wrong. Try again or check the logs.")

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)

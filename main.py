import requests
import os
import re
import random
import sqlite3
import asyncio
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message


# CONFIG
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")
CHAT_IDS = [
    454262931,5429733148,8031949005
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64)",
]

def get_headers():
    return {"User-Agent": random.choice(USER_AGENTS)}
HEADERS=get_headers()


MAX_DESC_LEN = 600
OLX_URL = "https://www.olx.pl/elektronika/telefony/smartfony-telefony-komorkowe/iphone/warszawa/?search%5Bdist%5D=300"


IPHONE_PRICES = {
    "12": {"pro": {128: 400, 256: 500, 512: 600}, "pro max": {128: 600, 256: 650, 512: 700}},
    "13": {"base": {128: 500, 256: 550}, "pro": {128: 700, 256: 800, 512: 1000}, "pro max": {128: 1000, 256: 1100, 512: 1200}},
    "14": {"base": {128: 700, 256: 800}, "pro": {128: 1200, 256: 1400, 512: 1600}, "pro max": {128: 1400, 256: 1700, 512: 1800}},
    "15": {"base": {128: 1200, 256: 1300}, "pro": {128: 1600, 256: 1700}, "pro max": {256: 2000, 512: 2200}},
    "16": {"base": {128: 1600, 256: 1700}, "pro": {128: 2500, 256: 2600, 512: 2800}, "pro max": {256: 3200, 512: 3400}},
}

# DB
db = sqlite3.connect("ads.db")
cur = db.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS sent_ads (url TEXT PRIMARY KEY)")
db.commit()

def is_sent(url: str) -> bool:
    cur.execute("SELECT 1 FROM sent_ads WHERE url=?", (url,))
    return cur.fetchone() is not None

def mark_sent(url: str) -> None:
    cur.execute("INSERT OR IGNORE INTO sent_ads VALUES (?)", (url,))
    db.commit()


def clean_price(text: str) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    if not digits:
        return None
    return int(digits)


def detect_model(title: str):
    t = title.lower()


    if not any(x in t for x in ["iphone", "i phone", "ipone", "айфон", "apple iphone"]):
        if not re.search(r"\b(12|13|14|15|16)\b", t):
            return None, None

    m = re.search(r"\b(12|13|14|15|16)\b", t)
    if not m:
        return None, None
    gen = m.group(1)


    if any(x in t for x in ["pro max", "promax", "pro-max"]):
        model = "pro max"
    elif "pro" in t:
        model = "pro"
    else:
        model = "base"

    return gen, model

def detect_storage(text: str):
    t = text.lower()


    t2 = t.replace(" ", "")
    for gb in [512, 256, 128]:
        if f"{gb}gb" in t2 or f"{gb}g" in t2:
            return gb


    for gb in [512, 256, 128]:
        if re.search(rf"\b{gb}\s*gb\b", t):
            return gb


    m = re.search(r"\b(128|256|512)\b", t)
    return int(m.group(1)) if m else None

def get_img_src(card):
    img = card.find("img")
    if not img:
        return None

    src = img.get("src")
    if not src:
        return None

    src = src.strip()

    if not src.startswith("http"):
        return None

    return src

def parse_list():
    html = requests.get(OLX_URL, headers=get_headers(), timeout=15).text
    soup = BeautifulSoup(html, "lxml")

    ads = []
    cards = soup.find_all("div", {"data-testid": "l-card"})


    for card in cards:
        title_tag = card.find("h4")
        price_tag = card.find("p", {"data-testid": "ad-price"})
        link_tag = card.find("a", href=True)

        if not title_tag or not price_tag or not link_tag:
            continue
        price_val = clean_price(price_tag.text)
        if price_val is None:
            continue

        img_url = get_img_src(card)

        ads.append({
            "title": title_tag.text.strip(),
            "price": price_val,
            "url": "https://www.olx.pl" + link_tag["href"],
            "image": img_url
        })


    return ads


def parse_ad_page(url: str):
    html = requests.get(url, headers=get_headers(), timeout=20).text
    soup = BeautifulSoup(html, "lxml")

    desc = soup.find("div", {"data-testid": "ad_description"})
    desc_text = desc.text.strip() if desc else ""

    seller = soup.find("h4", {"data-testid": "seller-name"}) or soup.find("h4", class_="css-14tb3q5")
    seller = seller.text.strip() if seller else "—"

    location = soup.find("p", {"data-testid": "location-date"})
    location = location.text.split("-")[0].strip() if location else "—"

    storage = detect_storage(desc_text)
    return {"description": desc_text, "seller": seller, "location": location, "storage": storage}

bot = Bot(BOT_TOKEN)


def build_info_kb(deal: dict) -> InlineKeyboardMarkup:
    def noop_btn(text: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data="noop")

    kb = [
        [noop_btn(f"📍 {deal['location']}"), noop_btn(f"💾 {deal['storage']} GB")],
        [noop_btn(f"💰 {deal['price']} PLN"), noop_btn(f"🏆 {deal['market_price']} PLN")],
        [noop_btn(f"🤑 +{deal['profit']} PLN"), noop_btn(f"👤 {deal['seller']}")],
        [InlineKeyboardButton(text="🔗 Открыть объявление", url=deal["url"])]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

router = Router()

@router.callback_query(F.data == "noop")
async def noop_handler(call: CallbackQuery):
    await call.answer()

async def send_deal(deal: dict):
    desc = (deal.get("description") or "")[:MAX_DESC_LEN]

    text = (
        f"📱 <b>{deal['title']}</b>\n"
        f"🔗 {deal['url']}\n\n"
        f"{desc}"
    )

    kb = build_info_kb(deal)
    img = deal.get("image")

    for chat_id in CHAT_IDS:
        try:
            if img:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=img,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=kb
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb
                )
        except Exception as e:
            print(f"Send error to {chat_id}: {e}")

        await asyncio.sleep(0.3)


async def main():
    ads = parse_list()
    print("ADS:", len(ads))

    stats = {"sent": 0, "no_model": 0, "no_storage": 0, "no_market": 0, "not_cheaper": 0}

    for ad in ads:
        if is_sent(ad["url"]):
            continue

        gen, model = detect_model(ad["title"])
        if not gen:
            stats["no_model"] += 1
            continue

        details = parse_ad_page(ad["url"])
        storage = details["storage"] or detect_storage(ad["title"])
        if not storage:
            stats["no_storage"] += 1
            continue

        market_price = IPHONE_PRICES.get(gen, {}).get(model, {}).get(storage)
        if not market_price:
            stats["no_market"] += 1
            continue

        if ad["price"] >= market_price:
            stats["not_cheaper"] += 1
            continue

        deal = {
            **ad, **details,
            "storage": storage,
            "market_price": market_price,
            "profit": market_price - ad["price"],
        }

        mark_sent(ad["url"])
        await send_deal(deal)
        stats["sent"] += 1

        await asyncio.sleep(2)


    print("STATS:", stats)

def normalize_img_url(src: str | None) -> str | None:
    if not src:
        return None
    src = src.strip()
    if not src:
        return None
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return None
    if not src.startswith("http"):
        return None
    return src

async def main_loop():
    while True:
        try:
            print("PARSE START")
            await main()
            print("PARSE DONE")
        except Exception as e:
            print("ERROR:", e)

        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main_loop())




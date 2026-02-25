import requests
import re
import os
import random
import sqlite3
import asyncio
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
import time



# CONFIG
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")
CHAT_IDS = [
    454262931,5429733148,8031949005,6425423245]
ADMINS = {454262931,5429733148,8031949005}


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64)",
]

def get_headers():
    return {"User-Agent": random.choice(USER_AGENTS)}
HEADERS=get_headers()


MAX_DESC_LEN = 600
OLX_URL = "https://www.olx.pl/elektronika/telefony/smartfony-telefony-komorkowe/iphone/warszawa/?search%5Bdist%5D=150&search%5Border%5D=created_at%3Adesc"


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

cur.execute("""
CREATE TABLE IF NOT EXISTS checked_ads (
    url TEXT PRIMARY KEY,
    checked_at INTEGER
)
""")
db.commit()

cur.execute("""
CREATE TABLE IF NOT EXISTS prices (
    gen TEXT NOT NULL,
    model TEXT NOT NULL,
    storage INTEGER NOT NULL,
    price INTEGER NOT NULL,
    PRIMARY KEY (gen, model, storage)
)
""")

db.commit()

def get_market_price(gen: str, model: str, storage: int) -> int | None:
    cur.execute(
        "SELECT price FROM prices WHERE gen=? AND model=? AND storage=?",
        (gen, model, storage)
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def set_market_price(gen: str, model: str, storage: int, price: int) -> None:
    cur.execute("""
        INSERT OR REPLACE INTO prices(gen, model, storage, price)
        VALUES (?, ?, ?, ?)
    """, (gen, model, storage, price))
    db.commit()


def is_checked_recent(url: str, ttl_seconds: int = 1800) -> bool:
    cur.execute("SELECT checked_at FROM checked_ads WHERE url=?", (url,))
    row = cur.fetchone()
    if not row:
        return False
    return (int(time.time()) - row[0]) < ttl_seconds

def mark_checked(url: str) -> None:
    cur.execute(
        "INSERT OR REPLACE INTO checked_ads(url, checked_at) VALUES (?, ?)",
        (url, int(time.time()))
    )
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

        ads.append({
            "title": title_tag.text.strip(),
            "price": price_val,
            "url": "https://www.olx.pl" + link_tag["href"],
        })

    return ads


def parse_parameters(soup: BeautifulSoup) -> dict:
    params = {}

    container = soup.find("div", {"data-testid": "ad-parameters-container"})
    if not container:
        return params


    for p in container.find_all("p"):
        txt = p.get_text(" ", strip=True)
        if not txt:
            continue


        if ":" in txt:
            k, v = txt.split(":", 1)
            params[k.strip().lower()] = v.strip()
        else:
            params[txt.strip().lower()] = True

    return params


def storage_from_params(params: dict) -> int | None:
    for key in ["wbudowana pamięć", "pamięć", "pamięć wbudowana"]:
        val = params.get(key)
        if not val:
            continue
        m = re.search(r"(\d+)\s*gb", val.lower())
        if m:
            return int(m.group(1))
    return None


def model_from_params(params: dict) -> tuple[str | None, str | None]:
    val = params.get("model telefonu")
    if not val:
        return None, None

    t = val.lower()


    m = re.search(r"\b(12|13|14|15|16)\b", t)
    if not m:
        return None, None
    gen = m.group(1)


    if "pro max" in t or "promax" in t or "pro-max" in t:
        model = "pro max"
    elif "pro" in t:
        model = "pro"
    else:
        model = "base"

    return gen, model

def pick_best_from_srcset(srcset: str | None) -> str | None:
    if not srcset:
        return None
    parts = [p.strip() for p in srcset.split(",") if p.strip()]
    if not parts:
        return None
    url = parts[-1].split()[0].strip()
    return url if url.startswith("http") else None


def parse_main_image_from_ad_page(soup: BeautifulSoup) -> str | None:
    img = soup.find("img", {"data-testid": "swiper-image"})
    if img:
        src = (img.get("src") or "").strip()
        if src.startswith("http"):
            return src
        best = pick_best_from_srcset(img.get("srcset"))
        if best:
            return best

    photo_block = soup.find("div", {"data-testid": "ad-photo"})
    if photo_block:
        img2 = photo_block.find("img")
        if img2:
            src = (img2.get("src") or "").strip()
            if src.startswith("http"):
                return src
            best = pick_best_from_srcset(img2.get("srcset"))
            if best:
                return best

    return None


def parse_ad_page(url: str):
    html = requests.get(url, headers=get_headers(), timeout=20).text
    soup = BeautifulSoup(html, "lxml")


    desc = soup.find("div", {"data-testid": "ad-description"})
    desc_text = desc.get_text("\n", strip=True) if desc else ""


    seller = soup.find("h4", {"data-testid": "seller-name"}) or soup.find("h4", class_="css-14tb3q5")
    seller = seller.get_text(strip=True) if seller else "—"


    location = soup.find("p", {"data-testid": "location-date"})
    location = location.get_text(" ", strip=True).split("-")[0].strip() if location else "—"


    params = parse_parameters(soup)

    storage = storage_from_params(params)
    gen, model = model_from_params(params)

    image = parse_main_image_from_ad_page(soup)

    return {
        "description": desc_text,
        "seller": seller,
        "location": location,
        "storage": storage,
        "gen": gen,
        "model": model,
        "params": params,
        "image": image,
    }


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

dp = Dispatcher()
dp.include_router(router)

@router.message(CommandStart())
async def start_cmd(message: Message):
    await message.answer(
        "Команды:\n"
        "/price gen model storage price  — установить цену\n"
        "пример: /price 13 pro 256 850\n"
        "/getprice gen model storage — посмотреть цену\n"
        "пример: /getprice 13 pro 256\n"
    )

@router.message(F.text.startswith("/price"))
async def set_price_cmd(message: Message):
    text = (message.text or "").strip()

    # убираем кавычки, чтобы "pro max" стало pro max
    text = text.replace('"', "").replace("“", "").replace("”", "").replace("'", "")
    parts = text.split()

    # ожидаем: /price gen model storage price
    if len(parts) not in (5, 6):
        return await message.answer(
            "Формат:\n"
            "/price <gen> <model> <storage> <price>\n"
            "Примеры:\n"
            "/price 13 pro 256 850\n"
            "/price 13 promax 256 850\n"
            "/price 13 pro max 256 850\n"
            "/price 13 pro-max 256 850\n"
            "/price 13 base 256 850\n"
        )

    gen = parts[1].lower()

    if len(parts) == 5:
        model_raw = parts[2].lower()
        storage_str = parts[3]
        price_str = parts[4]
    else:
        # len == 6: model = parts[2] + parts[3]
        model_raw = (parts[2] + " " + parts[3]).lower()
        storage_str = parts[4]
        price_str = parts[5]

    # нормализация model
    model_raw = model_raw.replace("-", " ").strip()
    if model_raw in ("promax", "pro max", "pro  max"):
        model = "pro max"
    elif model_raw == "pro":
        model = "pro"
    elif model_raw in ("base", "regular", "standard"):
        model = "base"
    else:
        return await message.answer("model должен быть: base | pro | pro max")

    try:
        storage = int(storage_str)
        price = int(price_str)
    except:
        return await message.answer("storage и price должны быть числами")

    set_market_price(gen, model, storage, price)
    await message.answer(f"✅ Цена сохранена: iPhone {gen} {model} {storage}GB = {price} PLN")



@router.message(F.text.regexp(r"^/getprice\b"))
async def get_price_cmd(message: Message):
    text = (message.text or "").strip()

    # кавычки нахуй, дефисы => пробел
    text = text.replace('"', "").replace("“", "").replace("”", "").replace("'", "")
    parts = text.split()

    # /getprice 13 pro 256  (4)
    # /getprice 13 pro max 256 (5)
    if len(parts) not in (4, 5):
        return await message.answer("Формат: /getprice <gen> <model> <storage>\nПример: /getprice 13 pro 256")

    gen = parts[1].lower()

    if len(parts) == 4:
        model_raw = parts[2].lower()
        storage_str = parts[3]
    else:
        model_raw = (parts[2] + " " + parts[3]).lower()
        storage_str = parts[4]

    model_raw = model_raw.replace("-", " ").strip()

    if model_raw in ("promax", "pro max", "pro  max"):
        model = "pro max"
    elif model_raw == "pro":
        model = "pro"
    elif model_raw in ("base", "regular", "standard"):
        model = "base"
    else:
        return await message.answer("model должен быть: base | pro | pro max")

    try:
        storage = int(storage_str)
    except:
        return await message.answer("storage должно быть числом")

    price = get_market_price(gen, model, storage)
    if price is None:
        return await message.answer("Цена не найдена")

    await message.answer(f"iPhone {gen} {model} {storage}GB = {price} PLN")

def seed_prices_from_dict(d: dict):
    for gen, models in d.items():
        for model, storages in models.items():
            for storage, price in storages.items():
                cur.execute("""
                    INSERT OR REPLACE INTO prices(gen, model, storage, price)
                    VALUES (?, ?, ?, ?)
                """, (gen, model, int(storage), int(price)))
    db.commit()


@router.message(F.text.startswith("/def"))
async def def_cmd(message: Message):
    seed_prices_from_dict(IPHONE_PRICES)


async def main():
    ads = parse_list()
    print("ADS:", len(ads))

    stats = {"sent": 0, "no_model": 0, "no_storage": 0, "no_market": 0, "not_cheaper": 0}

    for ad in ads:
        if is_sent(ad["url"]):
            continue

        if is_checked_recent(ad["url"], ttl_seconds=1800):  # 30 минут
            continue

        details = parse_ad_page(ad["url"])

        mark_checked(ad["url"])

        gen = details.get("gen")
        model = details.get("model")
        storage = details.get("storage")

        if not gen:
            stats["no_model"] += 1
            continue

        if not storage:
            stats["no_storage"] += 1
            continue

        market_price = get_market_price(gen, model, storage)
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
        await asyncio.sleep(0.2)


    print("STATS:", stats)



async def main_loop():
    while True:
        try:
            print("PARSE START")
            await main()
            print("PARSE DONE")
        except Exception as e:
            print("ERROR:", e)

        await asyncio.sleep(10)


async def runner():
    await asyncio.gather(
        main_loop(),
        dp.start_polling(bot),
    )

if __name__ == "__main__":
    asyncio.run(runner())



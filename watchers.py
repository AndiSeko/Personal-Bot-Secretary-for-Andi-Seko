import re
import logging
import asyncio
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

import db
import config

logger = logging.getLogger(__name__)

# Маппинг регионов для BY магазинов
REGION_MAP = {
    "minsk": {"21vek": "minsk", "5element": "minsk", "evroopt": "minsk", "kopeechka": "minsk", "groshyk": "minsk"},
    "brest": {"21vek": "brest", "5element": "brest"},
    "vitebsk": {"21vek": "vitebsk"},
    "gomel": {"21vek": "gomel"},
    "grodno": {"21vek": "grodno"},
    "mogilev": {"21vek": "mogilev"},
}

STORE_SELECTORS = {
    "21vek": ['[data-price]', '.price__value', 'meta[property="product:price:amount"]'],
    "5element": ['[data-price]', '.price', '.product-price__value'],
    "evroopt": ['.price__value', '[data-price]', '.product-price'],
    "kopeechka": ['.price', '[data-price]', '.product__price'],
    "groshyk": ['.price', '[data-price]'],
    "oz": ['.price__value', '.price'],
    "other": ['[data-price]', '.price__value', '.price', 'meta[property="product:price:amount"]'],
}

async def fetch_price(url: str, store: str, region: str = "minsk", selector: str = "") -> tuple[float | None, str]:
    """Пытается вытащить цену с URL с учётом региона и кастомного селектора."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    cookies = {}
    # регион для 21vek
    if "21vek" in url and region and region != "minsk":
        cookies["city"] = region
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, cookies=cookies, headers=headers) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None, f"http {r.status_code}"
            html = r.text[:30000]
            soup = BeautifulSoup(html, "lxml")
            # пробуем кастомный селектор первым
            sels = []
            if selector:
                sels.append(selector)
            sels.extend(STORE_SELECTORS.get(store, STORE_SELECTORS["other"]))
            for sel in sels:
                if not sel:
                    continue
                el = soup.select_one(sel)
                if el:
                    txt = el.get("content") or el.get_text() or ""
                    txt = txt.replace("\xa0", " ").replace(" ", "")
                    m = re.search(r'(\d+[\.,]\d+|\d+)', txt)
                    if m:
                        try:
                            price = float(m.group(1).replace(",", "."))
                            if price > 0 and price < 1000000:
                                return price, "ok"
                        except:
                            pass
            # fallback regex на весь html
            m = re.search(r'(\d+[\.,]\d+)\s*(?:р|BYN|руб|Br)', html)
            if m:
                try:
                    price = float(m.group(1).replace(",", "."))
                    return price, "ok"
                except:
                    pass
            return None, "not_found"
    except Exception as e:
        logger.warning("fetch_price %s failed: %s", url, e)
        return None, f"error:{e}"[:60]

async def check_watchers(bot=None, scheduler=None):
    """Проверка всех активных вотчеров (вызывается каждый час). Только обновляет цены, без пушей."""
    try:
        watchers = await db.get_watchers()
        if not watchers:
            return
        for w in watchers:
            if not w.get("is_active"):
                continue
            # check interval
            # we check hourly for all for MVP; later respect check_interval
            urls = await db.get_watcher_urls(w["id"])
            for u in urls:
                price, status = await fetch_price(u["url"], u.get("store","other"), w.get("region","minsk"), u.get("selector",""))
                if price is not None:
                    await db.update_watcher_url_price(u["id"], price, status)
                    logger.info("Watcher %s %s price %.2f", w["id"], u["store"], price)
                else:
                    # still update status
                    try:
                        await db.update_watcher_url_price(u["id"], 0, status)
                    except:
                        pass
                await asyncio.sleep(0.5)  # не спамим
    except Exception as e:
        logger.error("check_watchers failed: %s", e)

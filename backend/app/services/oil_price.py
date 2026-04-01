"""
Oil market price fetcher.

Supported sources:
  - heizoel-aktuell  (default, free scraping)
  - tankerkoenig     (API key required, Kraftstoff-fokus but also Heizöl via some endpoints)
  - custom           (arbitrary JSON endpoint, expects {"price_per_100l": <float>})
"""
import logging
from datetime import date, datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.config import settings
from app.models import OilMarketPrice, OilPriceSource

logger = logging.getLogger(__name__)


async def fetch_and_store_oil_price(db: Session) -> Optional[OilMarketPrice]:
    source = settings.oil_price_source
    try:
        if source == "heizoel-aktuell":
            price = await _fetch_heizoel_aktuell()
        elif source == "tankerkoenig":
            price = await _fetch_tankerkoenig()
        elif source == "custom":
            price = await _fetch_custom()
        else:
            logger.warning("Unknown oil_price_source: %s", source)
            return None
    except Exception as exc:
        logger.error("Oil price fetch failed (%s): %s", source, exc)
        return None

    if price is None:
        return None

    today = date.today()
    existing = (
        db.query(OilMarketPrice)
        .filter(OilMarketPrice.price_date == today, OilMarketPrice.source == source)
        .first()
    )
    if existing:
        existing.price_per_100l = price
        db.commit()
        db.refresh(existing)
        return existing

    entry = OilMarketPrice(price_date=today, price_per_100l=price, source=source)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


async def _fetch_heizoel_aktuell() -> float:
    """Scrape current Heizöl price from heizoel-aktuell.de."""
    url = "https://www.heizoel-aktuell.de/"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Zaehl-O-Mat/1.0)"})
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    # The page shows current price in an element; try common selectors
    for selector in [
        '[class*="current-price"]',
        '[class*="preis"]',
        '[data-price]',
        "span.price",
    ]:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(strip=True).replace(",", ".").replace("€", "").strip()
            try:
                return float(text.split()[0])
            except (ValueError, IndexError):
                continue

    raise ValueError("Could not parse price from heizoel-aktuell.de")


async def _fetch_tankerkoenig() -> float:
    if not settings.oil_price_api_key:
        raise ValueError("TANKERKOENIG_API_KEY not set")
    # Tankerkoenig is primarily for auto-fuel; use a placeholder endpoint
    url = f"https://creativecommons.tankerkoenig.de/json/list.php?lat=52&lng=10&rad=5&sort=dist&type=all&apikey={settings.oil_price_api_key}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    data = resp.json()
    # Extract Heizöl if available in response
    raise NotImplementedError("Tankerkoenig Heizöl endpoint not yet supported")


async def _fetch_custom() -> float:
    if not settings.oil_price_api_url:
        raise ValueError("OIL_PRICE_API_URL not set for custom source")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(settings.oil_price_api_url)
        resp.raise_for_status()
    data = resp.json()
    return float(data["price_per_100l"])

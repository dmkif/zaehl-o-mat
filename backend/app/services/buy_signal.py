"""
Oil market buy-signal computation.

Compares today's oil price against two benchmarks:
  1. 90-day rolling average  (short-term trend)
  2. Seasonal average        (same month ±15 days across the prior 2 years)

Signal logic:
  "buy"              – current price is ≥5% below BOTH benchmarks
  "avoid"            – current price is ≥5% above BOTH benchmarks
  "wait"             – mixed or within ±5% of either benchmark
  "insufficient_data"– fewer than 30 price records in the database

Confidence levels:
  "high"    – ≥365 data points (roughly 1 full year of daily records)
  "medium"  – 90–364 data points
  "low"     – 30–89 data points
  "insufficient_data" – <30 data points
"""
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import OilMarketPrice

_BUY_THRESHOLD = -5.0    # current must be ≥5% BELOW benchmark to signal "buy"
_AVOID_THRESHOLD = 5.0   # current must be ≥5% ABOVE benchmark to signal "avoid"
_MIN_RECORDS = 30
_SEASONAL_WINDOW_DAYS = 15


def compute_oil_buy_signal(db: Session) -> dict:
    """Return a dict describing the current oil purchase recommendation."""
    records = (
        db.query(OilMarketPrice)
        .order_by(OilMarketPrice.price_date.asc())
        .all()
    )

    if len(records) < _MIN_RECORDS:
        return {
            "signal": "insufficient_data",
            "current_price": None,
            "avg_90d": None,
            "seasonal_avg": None,
            "pct_vs_avg_90d": None,
            "pct_vs_seasonal": None,
            "confidence": "insufficient_data",
        }

    # Build a {date: price} lookup (latest wins if duplicates)
    price_map: dict[date, float] = {}
    for r in records:
        price_map[r.price_date] = float(r.price_per_100l)

    all_dates = sorted(price_map)
    today = all_dates[-1]
    current_price = price_map[today]

    # ── 90-day rolling average ───────────────────────────────────────────────
    cutoff_90 = today - timedelta(days=90)
    prices_90d = [price_map[d] for d in all_dates if d >= cutoff_90]
    avg_90d: Optional[float] = sum(prices_90d) / len(prices_90d) if prices_90d else None

    # ── Seasonal average ─────────────────────────────────────────────────────
    # Same calendar month ±15 days, restricted to records older than 180 days.
    # Using >180d offset avoids including recent price movements in the "baseline".
    seasonal_prices: list[float] = []
    for d in all_dates:
        if d >= today - timedelta(days=179):
            continue  # only use data from last year or older as seasonal reference
        # day-of-year distance (ignoring year)
        ref_doy = today.timetuple().tm_yday
        d_doy = d.timetuple().tm_yday
        diff = abs(ref_doy - d_doy)
        # Wrap-around at year boundary (e.g. Jan 5 vs Dec 28 = 8 days apart)
        diff = min(diff, 365 - diff)
        if diff <= _SEASONAL_WINDOW_DAYS:
            seasonal_prices.append(price_map[d])

    seasonal_avg: Optional[float] = (
        sum(seasonal_prices) / len(seasonal_prices) if seasonal_prices else None
    )

    # ── Percentage deltas ────────────────────────────────────────────────────
    def pct(current: float, benchmark: Optional[float]) -> Optional[float]:
        if benchmark is None or benchmark == 0:
            return None
        return round((current - benchmark) / benchmark * 100, 2)

    pct_90d = pct(current_price, avg_90d)
    pct_seasonal = pct(current_price, seasonal_avg)

    # ── Signal ───────────────────────────────────────────────────────────────
    signal: str
    if pct_90d is not None and pct_seasonal is not None:
        if pct_90d <= _BUY_THRESHOLD and pct_seasonal <= _BUY_THRESHOLD:
            signal = "buy"
        elif pct_90d >= _AVOID_THRESHOLD and pct_seasonal >= _AVOID_THRESHOLD:
            signal = "avoid"
        else:
            signal = "wait"
    elif pct_90d is not None:
        # Only 90d benchmark available
        if pct_90d <= _BUY_THRESHOLD:
            signal = "buy"
        elif pct_90d >= _AVOID_THRESHOLD:
            signal = "avoid"
        else:
            signal = "wait"
    else:
        signal = "wait"

    # ── Confidence ───────────────────────────────────────────────────────────
    n = len(price_map)
    if n >= 365:
        confidence = "high"
    elif n >= 90:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "signal": signal,
        "current_price": round(current_price, 2),
        "avg_90d": round(avg_90d, 2) if avg_90d is not None else None,
        "seasonal_avg": round(seasonal_avg, 2) if seasonal_avg is not None else None,
        "pct_vs_avg_90d": pct_90d,
        "pct_vs_seasonal": pct_seasonal,
        "confidence": confidence,
    }

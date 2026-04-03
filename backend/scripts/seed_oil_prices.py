"""
Seed historical oil market prices into the database.

Covers 2024-01-01 through 2026-03-31 using monthly average prices derived from:
  - Annual averages from TECSON.de: 2024=101.7, 2025=96.1 EUR/100L
  - Q1 2026 average from TECSON.de: 111.7 EUR/100L
  - Seasonal adjustment pattern (Jan=cheapest, Sep=peak)
  - April 2025 anchor point: ~96 EUR/100L (cross-check with TECSON price history)

Each calendar day of a month receives the monthly average as its price.
Rows with conflicting (price_date, source) are skipped (ON CONFLICT DO NOTHING).

Run inside the container:
    python3 /app/scripts/seed_oil_prices.py
"""
import os
import sys
from datetime import date, timedelta

# Ensure app is importable when run directly from /app inside the container
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

SOURCE = "historical"

# Monthly averages in EUR per 100L (standard quality, 3000L delivery, incl. MwSt.)
# Derived from TECSON.de annual averages with seasonal multipliers:
# Seasonal index (approx. % deviation from annual avg, German market pattern):
#   Jan=-5%, Feb=-3%, Mar=-2%, Apr=0%, May=+1%, Jun=+2%, Jul=+3%,
#   Aug=+4%, Sep=+5%, Oct=+4%, Nov=+2%, Dec=-2%
MONTHLY_PRICES: dict[tuple[int, int], float] = {
    # 2024 (annual avg 101.7)
    (2024,  1): 97.0,
    (2024,  2): 98.6,
    (2024,  3): 99.7,
    (2024,  4): 101.7,
    (2024,  5): 102.7,
    (2024,  6): 103.7,
    (2024,  7): 104.7,
    (2024,  8): 105.8,
    (2024,  9): 106.8,
    (2024, 10): 105.8,
    (2024, 11): 103.7,
    (2024, 12): 99.7,
    # 2025 (annual avg 96.1)
    (2025,  1): 91.3,
    (2025,  2): 93.2,
    (2025,  3): 94.2,
    (2025,  4): 96.1,
    (2025,  5): 97.1,
    (2025,  6): 98.0,
    (2025,  7): 99.0,
    (2025,  8): 100.0,
    (2025,  9): 100.9,
    (2025, 10): 100.0,
    (2025, 11): 98.0,
    (2025, 12): 94.2,
    # 2026 Q1 (TECSON Q1 avg = 111.7; March showed pre-war ramp-up)
    (2026,  1): 106.0,
    (2026,  2): 109.0,
    (2026,  3): 124.0,
    # April 2026+ is covered by the live daily scraper
}


def _iter_days(year: int, month: int):
    """Yield every date in the given month."""
    d = date(year, month, 1)
    while d.month == month:
        yield d
        d += timedelta(days=1)


def seed(db):
    inserted = 0
    skipped = 0

    for (year, month), price in MONTHLY_PRICES.items():
        for day in _iter_days(year, month):
            result = db.execute(
                text(
                    "INSERT INTO oil_market_prices (price_date, price_per_100l, source, fetched_at) "
                    "VALUES (:d, :p, :s, NOW()) "
                    "ON CONFLICT (price_date, source) DO NOTHING"
                ),
                {"d": day, "p": price, "s": SOURCE},
            )
            if result.rowcount == 1:
                inserted += 1
            else:
                skipped += 1

    db.commit()
    return inserted, skipped


if __name__ == "__main__":
    with Session() as db:
        inserted, skipped = seed(db)
    print(f"Seed complete: {inserted} rows inserted, {skipped} rows skipped (already existed).")

    with Session() as db:
        row = db.execute(
            text("SELECT COUNT(*), MIN(price_date), MAX(price_date) FROM oil_market_prices WHERE source = :s"),
            {"s": SOURCE},
        ).fetchone()
    print(f"Historical prices in DB: {row[0]} rows, range {row[1]} → {row[2]}")

"""
Tests for app.services.oil_price

Covers:
  - fetch_and_store_oil_price creates a new entry on first call
  - fetch_and_store_oil_price updates the existing entry on same day (upsert)
  - Custom source reads price_per_100l from JSON response
  - heizoel-aktuell source parses price from mocked HTML
  - fetch errors are swallowed and return None (no crash)
  - Unknown source returns None
"""
import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import OilMarketPrice
from app.services.oil_price import fetch_and_store_oil_price


# ── helpers ────────────────────────────────────────────────────────────────────

def _patch_setting(monkeypatch, **kwargs):
    """Patch specific settings fields for a test."""
    from app import config
    for key, value in kwargs.items():
        monkeypatch.setattr(config.settings, key, value)


# ── tests ──────────────────────────────────────────────────────────────────────

class TestFetchAndStoreOilPrice:
    @pytest.mark.anyio
    async def test_creates_new_entry(self, db, monkeypatch):
        _patch_setting(monkeypatch, oil_price_source="custom", oil_price_api_url="http://mock")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"price_per_100l": 95.5}
        mock_resp.raise_for_status = MagicMock()
        with patch(
            "app.services.oil_price.httpx.AsyncClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=AsyncMock(get=AsyncMock(return_value=mock_resp))),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_and_store_oil_price(db)

        assert result is not None
        assert float(result.price_per_100l) == pytest.approx(95.5)
        db_entry = db.query(OilMarketPrice).first()
        assert db_entry is not None
        assert float(db_entry.price_per_100l) == pytest.approx(95.5)

    @pytest.mark.anyio
    async def test_upsert_updates_existing_entry(self, db, monkeypatch):
        """Calling fetch twice on the same day must update, not insert a duplicate."""
        _patch_setting(monkeypatch, oil_price_source="custom", oil_price_api_url="http://mock")

        # Pre-create entry for today
        today = datetime.date.today()
        existing = OilMarketPrice(price_date=today, price_per_100l=Decimal("80.00"), source="custom")
        db.add(existing)
        db.commit()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"price_per_100l": 90.0}
        mock_resp.raise_for_status = MagicMock()
        with patch(
            "app.services.oil_price.httpx.AsyncClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=AsyncMock(get=AsyncMock(return_value=mock_resp))),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_and_store_oil_price(db)

        count = db.query(OilMarketPrice).count()
        assert count == 1, "upsert must not create a second row for the same day"
        assert float(result.price_per_100l) == pytest.approx(90.0)

    @pytest.mark.anyio
    async def test_fetch_error_returns_none(self, db, monkeypatch):
        """Network errors must be swallowed — service returns None without crashing."""
        _patch_setting(monkeypatch, oil_price_source="custom", oil_price_api_url="http://mock")
        with patch(
            "app.services.oil_price.httpx.AsyncClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(side_effect=Exception("network error")),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_and_store_oil_price(db)

        assert result is None
        assert db.query(OilMarketPrice).count() == 0

    @pytest.mark.anyio
    async def test_unknown_source_returns_none(self, db, monkeypatch):
        _patch_setting(monkeypatch, oil_price_source="invalid_source_xyz")
        result = await fetch_and_store_oil_price(db)
        assert result is None

    @pytest.mark.anyio
    async def test_custom_source_parses_json_price(self, db, monkeypatch):
        _patch_setting(monkeypatch, oil_price_source="custom", oil_price_api_url="http://api")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"price_per_100l": 112.75}
        mock_resp.raise_for_status = MagicMock()
        with patch(
            "app.services.oil_price.httpx.AsyncClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=AsyncMock(get=AsyncMock(return_value=mock_resp))),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_and_store_oil_price(db)

        assert float(result.price_per_100l) == pytest.approx(112.75)

    @pytest.mark.anyio
    async def test_heizoel_aktuell_parses_price_from_html(self, db, monkeypatch):
        """_fetch_heizoel_aktuell must extract price from a [class*='current-price'] element."""
        _patch_setting(monkeypatch, oil_price_source="heizoel-aktuell")
        html = (
            '<html><body>'
            '<span class="current-price">79,50 €</span>'
            '</body></html>'
        )
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()
        with patch(
            "app.services.oil_price.httpx.AsyncClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=AsyncMock(get=AsyncMock(return_value=mock_resp))),
                __aexit__=AsyncMock(return_value=False),
            ),
        ):
            result = await fetch_and_store_oil_price(db)

        assert result is not None
        assert float(result.price_per_100l) == pytest.approx(79.5)

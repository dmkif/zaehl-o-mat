"""
Tests for _extract_numeric() in app.routers.ocr.

Scoring rules under test:
  - 4-8 digits  → +0.3 bonus (typical meter display range)
  - decimal     → +0.2 bonus
  - >8 digits   → -0.4 penalty (serial number territory)
  - date-like   → discarded (multiple dots)
  - bbox height → +0.4 / +0.1 bonus for larger text
"""
import pytest

from app.routers.ocr import _extract_numeric


def _r(text: str, conf: float = 0.9, bbox=None):
    """Build a minimal EasyOCR result tuple."""
    if bbox is None:
        bbox = [[0, 0], [100, 0], [100, 10], [0, 10]]
    return (bbox, text, conf)


class TestBasicExtraction:
    def test_single_numeric_candidate(self):
        assert _extract_numeric([_r("12345")]) == "12345"

    def test_returns_none_for_empty(self):
        assert _extract_numeric([]) is None

    def test_returns_none_for_non_numeric(self):
        assert _extract_numeric([_r("ABCDE")]) is None

    def test_strips_to_digits(self):
        result = _extract_numeric([_r("01234 kWh")])
        assert result == "01234"

    def test_comma_normalized_to_dot(self):
        result = _extract_numeric([_r("1234,5")])
        assert result == "1234.5"


class TestScoringBonus:
    def test_four_to_eight_digits_wins_over_three(self):
        """4-8 digit reading should outscore 3-digit candidate at same confidence."""
        three_digit = _r("123", conf=0.95)
        five_digit = _r("12345", conf=0.90)
        assert _extract_numeric([three_digit, five_digit]) == "12345"

    def test_decimal_bonus(self):
        """Reading with one decimal should outscore same-digit-count integer at same conf."""
        integer = _r("1234", conf=0.90)
        decimal = _r("123.4", conf=0.85)
        assert _extract_numeric([integer, decimal]) == "123.4"

    def test_serial_number_penalty(self):
        """9+ digit string should lose to 5-digit reading at same confidence."""
        meter_reading = _r("12345", conf=0.85)
        serial = _r("123456789", conf=0.90)
        assert _extract_numeric([meter_reading, serial]) == "12345"


class TestDateRejection:
    def test_date_like_multi_dot_discarded(self):
        """Strings with multiple dots (e.g. dates) must be discarded outright."""
        assert _extract_numeric([_r("25.7.18")]) is None

    def test_date_alongside_reading(self):
        date = _r("25.7.18", conf=0.99)
        reading = _r("01234", conf=0.50)
        assert _extract_numeric([date, reading]) == "01234"


class TestBboxHeightBonus:
    def _tall_bbox(self, img_h: int, ratio: float):
        h = ratio * img_h
        return [[0, 0], [100, 0], [100, h], [0, h]]

    def test_large_text_wins_over_small(self):
        """Large display digits (>6 % of image height) should get +0.4 bonus."""
        img_h = 1000
        small_bbox = self._tall_bbox(img_h, 0.01)  # 1 % → no bonus
        large_bbox = self._tall_bbox(img_h, 0.08)  # 8 % → +0.4 bonus

        small = (small_bbox, "99999", 0.95)
        large = (large_bbox, "12345", 0.60)
        assert _extract_numeric([small, large], img_size=(500, img_h)) == "12345"

    def test_medium_text_bonus(self):
        """Medium text (2.5-6 % of image height) gets +0.1 bonus."""
        img_h = 1000
        no_bonus_bbox = self._tall_bbox(img_h, 0.01)   # 1 % → 0 bonus
        medium_bbox = self._tall_bbox(img_h, 0.03)     # 3 % → +0.1 bonus

        no_bonus = (no_bonus_bbox, "54321", 0.90)
        medium = (medium_bbox, "12345", 0.82)
        assert _extract_numeric([no_bonus, medium], img_size=(500, img_h)) == "12345"


class TestMinimumDigitFilter:
    def test_two_digit_string_rejected(self):
        assert _extract_numeric([_r("12")]) is None

    def test_three_digit_accepted_with_penalty(self):
        # 3 digits → score = conf - 0.2; result is still returned if it's the only candidate
        result = _extract_numeric([_r("123", conf=0.9)])
        assert result == "123"

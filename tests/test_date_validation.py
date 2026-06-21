"""
tests/test_date_validation.py — Unit tests for invoice date validation
======================================================================
Tests the automated date validation layer that catches OCR date errors
(e.g. 2018 instead of 2026) before invoices are saved.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from validate import (
    parse_invoice_date,
    validate_invoice_date,
    DATE_MAX_AGE_DAYS,
    DATE_MAX_FUTURE_DAYS,
)
from exceptions import SuspiciousDateError


# ═══════════════════════════════════════════════════════════
# parse_invoice_date
# ═══════════════════════════════════════════════════════════

class TestParseInvoiceDate:
    def test_iso_format(self):
        result = parse_invoice_date("2026-06-15")
        assert result == datetime(2026, 6, 15)

    def test_indian_format_dashes(self):
        result = parse_invoice_date("15-06-2026")
        assert result == datetime(2026, 6, 15)

    def test_indian_format_slashes(self):
        result = parse_invoice_date("15/06/2026")
        assert result == datetime(2026, 6, 15)

    def test_dot_format(self):
        result = parse_invoice_date("15.06.2026")
        assert result == datetime(2026, 6, 15)

    def test_long_month_format(self):
        result = parse_invoice_date("June 15, 2026")
        assert result == datetime(2026, 6, 15)

    def test_short_month_format(self):
        result = parse_invoice_date("15 Jun 2026")
        assert result == datetime(2026, 6, 15)

    def test_empty_string(self):
        assert parse_invoice_date("") is None

    def test_none(self):
        assert parse_invoice_date(None) is None

    def test_garbage(self):
        assert parse_invoice_date("not-a-date") is None

    def test_whitespace_stripped(self):
        result = parse_invoice_date("  2026-06-15  ")
        assert result == datetime(2026, 6, 15)


# ═══════════════════════════════════════════════════════════
# validate_invoice_date
# ═══════════════════════════════════════════════════════════

class TestValidateInvoiceDate:
    """Uses a fixed 'now' of 2026-06-15 for deterministic tests."""

    MOCK_NOW = datetime(2026, 6, 15)

    @patch("validate.datetime")
    def test_recent_date_passes(self, mock_dt):
        """A date from last month should pass validation."""
        mock_dt.utcnow.return_value = self.MOCK_NOW
        mock_dt.strptime = datetime.strptime
        # No exception expected
        validate_invoice_date({"invoice_date": "2026-05-20"})

    @patch("validate.datetime")
    def test_today_passes(self, mock_dt):
        """Today's date should pass."""
        mock_dt.utcnow.return_value = self.MOCK_NOW
        mock_dt.strptime = datetime.strptime
        validate_invoice_date({"invoice_date": "2026-06-15"})

    @patch("validate.datetime")
    def test_ocr_error_2018_flagged(self, mock_dt):
        """The exact scenario: OCR reads 2018 instead of 2026."""
        mock_dt.utcnow.return_value = self.MOCK_NOW
        mock_dt.strptime = datetime.strptime
        with pytest.raises(SuspiciousDateError) as exc_info:
            validate_invoice_date({"invoice_date": "2018-06-06"})
        assert "2018-06-06" in str(exc_info.value)
        assert exc_info.value.extracted_date == "2018-06-06"

    @patch("validate.datetime")
    def test_date_over_1_year_old_flagged(self, mock_dt):
        """A date more than 365 days ago should be flagged."""
        mock_dt.utcnow.return_value = self.MOCK_NOW
        mock_dt.strptime = datetime.strptime
        old_date = (self.MOCK_NOW - timedelta(days=DATE_MAX_AGE_DAYS + 30)).strftime("%Y-%m-%d")
        with pytest.raises(SuspiciousDateError):
            validate_invoice_date({"invoice_date": old_date})

    @patch("validate.datetime")
    def test_date_just_under_1_year_passes(self, mock_dt):
        """A date 11 months ago should pass."""
        mock_dt.utcnow.return_value = self.MOCK_NOW
        mock_dt.strptime = datetime.strptime
        ok_date = (self.MOCK_NOW - timedelta(days=330)).strftime("%Y-%m-%d")
        validate_invoice_date({"invoice_date": ok_date})

    @patch("validate.datetime")
    def test_future_date_flagged(self, mock_dt):
        """A date 3 months in the future should be flagged."""
        mock_dt.utcnow.return_value = self.MOCK_NOW
        mock_dt.strptime = datetime.strptime
        future_date = (self.MOCK_NOW + timedelta(days=90)).strftime("%Y-%m-%d")
        with pytest.raises(SuspiciousDateError):
            validate_invoice_date({"invoice_date": future_date})

    @patch("validate.datetime")
    def test_near_future_passes(self, mock_dt):
        """A date a few days in the future (within 30 days) should pass."""
        mock_dt.utcnow.return_value = self.MOCK_NOW
        mock_dt.strptime = datetime.strptime
        near_future = (self.MOCK_NOW + timedelta(days=15)).strftime("%Y-%m-%d")
        validate_invoice_date({"invoice_date": near_future})

    def test_no_date_skips(self):
        """Missing invoice_date should not raise."""
        validate_invoice_date({"invoice_date": ""})
        validate_invoice_date({})

    def test_unparseable_date_skips(self):
        """An unparseable date string should not block the pipeline."""
        validate_invoice_date({"invoice_date": "garbled-text-xyz"})

    @patch("validate.datetime")
    def test_indian_format_validated(self, mock_dt):
        """Indian date format (DD/MM/YYYY) should be parsed and validated."""
        mock_dt.utcnow.return_value = self.MOCK_NOW
        mock_dt.strptime = datetime.strptime
        # 19/04/2026 = valid date (within 1 year)
        validate_invoice_date({"invoice_date": "19/04/2026"})

    @patch("validate.datetime")
    def test_old_indian_format_flagged(self, mock_dt):
        """An old Indian format date should be flagged."""
        mock_dt.utcnow.return_value = self.MOCK_NOW
        mock_dt.strptime = datetime.strptime
        with pytest.raises(SuspiciousDateError):
            validate_invoice_date({"invoice_date": "06/06/2018"})

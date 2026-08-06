"""
tests/test_duplicate_detector.py -- Unit tests for fuzzy duplicate detection
============================================================================
Tests the three-signal matching logic (invoice number + amount + vendor name)
that makes duplicate detection resilient to OCR errors.

Matching rules (ALL three must pass):
  1. Invoice number  -- normalized exact match
  2. Total amount    -- exact match (no tolerance)
  3. Vendor name     -- difflib similarity > 80%

check_fuzzy_duplicate() returns (bill_id, invoice_date) on a match, or None.
"""

import pytest
from unittest.mock import MagicMock

from duplicate_detector import (
    normalize_invoice_number,
    vendor_name_similarity,
    amounts_match_exact,
    check_fuzzy_duplicate,
    VENDOR_NAME_THRESHOLD,
)


# ═══════════════════════════════════════════════════════════
# normalize_invoice_number
# ═══════════════════════════════════════════════════════════

class TestNormalizeInvoiceNumber:
    def test_strips_slashes_and_dashes(self):
        assert normalize_invoice_number("PDP/26-27/026") == "PDP2627026"

    def test_strips_spaces(self):
        assert normalize_invoice_number("PDP 26 27 026") == "PDP2627026"

    def test_case_insensitive(self):
        assert normalize_invoice_number("pdp26-27-026") == "PDP2627026"

    def test_equivalent_formats(self):
        """All common OCR variations of the same invoice number should normalize equally."""
        variants = ["PDP26-27-026", "PDP/26-27/026", "PDP26-27/026", "pdp-26-27-026"]
        normalized = [normalize_invoice_number(v) for v in variants]
        assert len(set(normalized)) == 1

    def test_empty_string(self):
        assert normalize_invoice_number("") == ""

    def test_none(self):
        assert normalize_invoice_number(None) == ""


# ═══════════════════════════════════════════════════════════
# vendor_name_similarity
# ═══════════════════════════════════════════════════════════

class TestVendorNameSimilarity:
    def test_identical_names(self):
        assert vendor_name_similarity("Pooja Decorative Plywoods", "Pooja Decorative Plywoods") == 1.0

    def test_ocr_error_p_to_f(self):
        """The exact scenario: OCR misreads 'P' as 'F' in Pooja -> Fooja."""
        sim = vendor_name_similarity("Pooja Decorative Plywoods", "Fooja Decorative Plywoods")
        assert sim > VENDOR_NAME_THRESHOLD, f"Expected >80%, got {sim:.0%}"

    def test_completely_different(self):
        sim = vendor_name_similarity("Pooja Decorative Plywoods", "Tata Steel Industries")
        assert sim < VENDOR_NAME_THRESHOLD

    def test_case_insensitive(self):
        sim = vendor_name_similarity("POOJA DECORATIVE", "pooja decorative")
        assert sim == 1.0

    def test_extra_whitespace(self):
        sim = vendor_name_similarity("Pooja  Decorative", "Pooja Decorative")
        assert sim == 1.0

    def test_empty_names(self):
        assert vendor_name_similarity("", "Pooja") == 0.0
        assert vendor_name_similarity("Pooja", "") == 0.0
        assert vendor_name_similarity("", "") == 0.0


# ═══════════════════════════════════════════════════════════
# amounts_match_exact
# ═══════════════════════════════════════════════════════════

class TestAmountsMatchExact:
    def test_exact_match(self):
        assert amounts_match_exact(15420.0, 15420.0) is True

    def test_one_rupee_difference_rejected(self):
        """Unlike the old ±1 tolerance, even Rs.1 difference is now rejected."""
        assert amounts_match_exact(15420.0, 15421.0) is False

    def test_half_rupee_difference_rejected(self):
        assert amounts_match_exact(15420.0, 15420.50) is False

    def test_large_difference_rejected(self):
        assert amounts_match_exact(15420.0, 15422.0) is False

    def test_zero_amounts(self):
        assert amounts_match_exact(0.0, 0.0) is True

    def test_different_zeros(self):
        assert amounts_match_exact(0.0, 0.01) is False


# ═══════════════════════════════════════════════════════════
# check_fuzzy_duplicate (integration with mock Supabase)
# ═══════════════════════════════════════════════════════════

def _make_mock_client(existing_bills: list) -> MagicMock:
    """Creates a mock Supabase client that returns the given bills."""
    client = MagicMock()
    execute_result = MagicMock()
    execute_result.data = existing_bills

    eq_mock = MagicMock()
    eq_mock.eq.return_value = eq_mock
    eq_mock.execute.return_value = execute_result

    client.table.return_value.select.return_value.eq.return_value = eq_mock
    return client


# Helper: build a typical existing bill row
def _bill(id, invoice_number, total_amount, vendor_name, invoice_date="2026-05-10"):
    return {
        "id": id,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "total_amount": total_amount,
        "vendor_name": vendor_name,
    }


class TestCheckFuzzyDuplicate:
    def test_exact_duplicate_flagged(self):
        """Same invoice number, same amount, same vendor -> duplicate."""
        client = _make_mock_client([
            _bill("bill-001", "PDP/26-27/026", 15420.0, "Pooja Decorative Plywoods", "2026-05-10")
        ])
        result = check_fuzzy_duplicate(
            client, "PDP26-27-026", 15420.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is not None
        bill_id, bill_date = result
        assert bill_id == "bill-001"
        assert bill_date == "2026-05-10"

    def test_ocr_vendor_name_error_flagged(self):
        """
        The Pooja Decorative Plywoods scenario:
        OCR misreads 'P' as 'F', causing vendor name to differ slightly.
        Same invoice number & exact amount -> should still be flagged.
        """
        client = _make_mock_client([
            _bill("bill-002", "PDP/26-27/026", 15420.0, "Pooja Decorative Plywoods", "2026-04-01")
        ])
        result = check_fuzzy_duplicate(
            client, "PDP26-27-026", 15420.0, "Fooja Decorative Plywoods", "+919999999999"
        )
        assert result is not None
        bill_id, bill_date = result
        assert bill_id == "bill-002"
        assert bill_date == "2026-04-01"

    def test_different_invoice_allowed(self):
        """Same vendor, different invoice number & amount -> not a duplicate."""
        client = _make_mock_client([
            _bill("bill-003", "PDP/26-27/026", 15420.0, "Pooja Decorative Plywoods")
        ])
        result = check_fuzzy_duplicate(
            client, "PDP26-27-027", 8900.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is None

    def test_similar_name_different_invoice_allowed(self):
        """82% similar name but different invoice number -> not a duplicate."""
        client = _make_mock_client([
            _bill("bill-004", "INV-001", 5000.0, "Kumar Hardware Store")
        ])
        result = check_fuzzy_duplicate(
            client, "INV-002", 5000.0, "Kumar Hardware Stores", "+919999999999"
        )
        assert result is None

    def test_name_below_threshold_allowed(self):
        """Same invoice & amount, but vendor name similarity < 80% -> allowed."""
        client = _make_mock_client([
            _bill("bill-005", "INV-100", 10000.0, "Pooja Decorative Plywoods")
        ])
        result = check_fuzzy_duplicate(
            client, "INV-100", 10000.0, "Raj Steel Traders", "+919999999999"
        )
        assert result is None

    def test_amount_exact_mismatch_allowed(self):
        """Same invoice number & vendor, but amount differs by Rs.1 -> allowed (exact match required)."""
        client = _make_mock_client([
            _bill("bill-006", "INV-100", 10000.0, "Pooja Decorative Plywoods")
        ])
        result = check_fuzzy_duplicate(
            client, "INV-100", 10001.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is None

    def test_amount_large_mismatch_allowed(self):
        """Same invoice number & vendor, but amount differs by Rs.5000 -> allowed."""
        client = _make_mock_client([
            _bill("bill-006b", "INV-100", 10000.0, "Pooja Decorative Plywoods")
        ])
        result = check_fuzzy_duplicate(
            client, "INV-100", 15000.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is None

    def test_no_existing_bills(self):
        """Empty database -> no duplicate."""
        client = _make_mock_client([])
        result = check_fuzzy_duplicate(
            client, "INV-100", 10000.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is None

    def test_empty_invoice_number_skips(self):
        """No invoice number on the new bill -> skip detection entirely."""
        client = _make_mock_client([
            _bill("bill-007", "INV-100", 10000.0, "Pooja Decorative Plywoods")
        ])
        result = check_fuzzy_duplicate(
            client, "", 10000.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is None

    def test_invoice_date_missing_returns_na(self):
        """If the existing bill has no invoice_date, the date field returns 'N/A'."""
        bill = _bill("bill-008", "INV-200", 5000.0, "Pooja Decorative Plywoods")
        del bill["invoice_date"]  # simulate missing date
        client = _make_mock_client([bill])
        result = check_fuzzy_duplicate(
            client, "INV-200", 5000.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is not None
        bill_id, bill_date = result
        assert bill_id == "bill-008"
        assert bill_date == "N/A"

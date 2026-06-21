"""
tests/test_duplicate_detector.py — Unit tests for fuzzy duplicate detection
============================================================================
Tests the three-signal matching logic (invoice number + amount + vendor name)
that makes duplicate detection resilient to OCR errors.
"""

import pytest
from unittest.mock import MagicMock

from duplicate_detector import (
    normalize_invoice_number,
    vendor_name_similarity,
    amounts_match,
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
        """The exact scenario: OCR misreads 'P' as 'F' in Pooja → Fooja."""
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
# amounts_match
# ═══════════════════════════════════════════════════════════

class TestAmountsMatch:
    def test_exact_match(self):
        assert amounts_match(15420.0, 15420.0) is True

    def test_within_tolerance(self):
        assert amounts_match(15420.0, 15420.50) is True

    def test_at_boundary(self):
        assert amounts_match(15420.0, 15421.0) is True

    def test_beyond_tolerance(self):
        assert amounts_match(15420.0, 15422.0) is False

    def test_zero_amounts(self):
        assert amounts_match(0.0, 0.0) is True


# ═══════════════════════════════════════════════════════════
# check_fuzzy_duplicate (integration with mock Supabase)
# ═══════════════════════════════════════════════════════════

def _make_mock_client(existing_bills: list) -> MagicMock:
    """Creates a mock Supabase client that returns the given bills."""
    client = MagicMock()
    execute_result = MagicMock()
    execute_result.data = existing_bills

    client.table.return_value.select.return_value.eq.return_value.execute.return_value = execute_result
    return client


class TestCheckFuzzyDuplicate:
    def test_exact_duplicate_flagged(self):
        """Same invoice number, same amount, same vendor → duplicate."""
        client = _make_mock_client([{
            "id": "bill-001",
            "invoice_number": "PDP/26-27/026",
            "total_amount": 15420.0,
            "vendor_name": "Pooja Decorative Plywoods",
        }])
        result = check_fuzzy_duplicate(
            client, "PDP26-27-026", 15420.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result == "bill-001"

    def test_ocr_gstin_error_flagged(self):
        """
        The Pooja Decorative Plywoods scenario:
        OCR misreads GSTIN 'P' as 'F', causing vendor name to be slightly different.
        Same invoice number & amount → should still be flagged.
        """
        client = _make_mock_client([{
            "id": "bill-002",
            "invoice_number": "PDP/26-27/026",
            "total_amount": 15420.0,
            "vendor_name": "Pooja Decorative Plywoods",
        }])
        result = check_fuzzy_duplicate(
            client, "PDP26-27-026", 15420.0, "Fooja Decorative Plywoods", "+919999999999"
        )
        assert result == "bill-002"

    def test_different_invoice_allowed(self):
        """Same vendor, different invoice number & amount → not a duplicate."""
        client = _make_mock_client([{
            "id": "bill-003",
            "invoice_number": "PDP/26-27/026",
            "total_amount": 15420.0,
            "vendor_name": "Pooja Decorative Plywoods",
        }])
        result = check_fuzzy_duplicate(
            client, "PDP26-27-027", 8900.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is None

    def test_similar_name_different_invoice_allowed(self):
        """82% similar name but different invoice number → not a duplicate."""
        client = _make_mock_client([{
            "id": "bill-004",
            "invoice_number": "INV-001",
            "total_amount": 5000.0,
            "vendor_name": "Kumar Hardware Store",
        }])
        result = check_fuzzy_duplicate(
            client, "INV-002", 5000.0, "Kumar Hardware Stores", "+919999999999"
        )
        assert result is None

    def test_name_below_threshold_allowed(self):
        """Same invoice & amount, but vendor name similarity < 80% → allowed."""
        client = _make_mock_client([{
            "id": "bill-005",
            "invoice_number": "INV-100",
            "total_amount": 10000.0,
            "vendor_name": "Pooja Decorative Plywoods",
        }])
        result = check_fuzzy_duplicate(
            client, "INV-100", 10000.0, "Raj Steel Traders", "+919999999999"
        )
        assert result is None

    def test_amount_mismatch_allowed(self):
        """Same invoice number & vendor, but amount differs → allowed."""
        client = _make_mock_client([{
            "id": "bill-006",
            "invoice_number": "INV-100",
            "total_amount": 10000.0,
            "vendor_name": "Pooja Decorative Plywoods",
        }])
        result = check_fuzzy_duplicate(
            client, "INV-100", 15000.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is None

    def test_no_existing_bills(self):
        """Empty database → no duplicate."""
        client = _make_mock_client([])
        result = check_fuzzy_duplicate(
            client, "INV-100", 10000.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is None

    def test_empty_invoice_number_skips(self):
        """No invoice number on the new bill → skip detection entirely."""
        client = _make_mock_client([{
            "id": "bill-007",
            "invoice_number": "INV-100",
            "total_amount": 10000.0,
            "vendor_name": "Pooja Decorative Plywoods",
        }])
        result = check_fuzzy_duplicate(
            client, "", 10000.0, "Pooja Decorative Plywoods", "+919999999999"
        )
        assert result is None

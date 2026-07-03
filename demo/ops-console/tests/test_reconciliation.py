#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ops-console reconciliation seed assertions."""
from seed import compute_expected_reconciliation, init_db


def test_expected_reconciliation_discrepancies():
    init_db(force=True)
    discrepancies = compute_expected_reconciliation()
    types = {d["type"] for d in discrepancies}
    assert "MISSING_SETTLEMENT" in types
    assert "AMOUNT_MISMATCH" in types
    assert "DUPLICATE_REFUND" in types
    assert "ORPHAN_SETTLEMENT" in types
    assert len(discrepancies) == 4

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Seed SQLite database for OpsConsole demo (tickets + settlement ledger)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "ops_console.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    customer TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    assignee TEXT NOT NULL DEFAULT 'unassigned',
    priority TEXT NOT NULL DEFAULT 'normal',
    amount REAL NOT NULL DEFAULT 0,
    refund_amount REAL NOT NULL DEFAULT 0,
    refund_status TEXT NOT NULL DEFAULT 'none',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settlement_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_no TEXT NOT NULL UNIQUE,
    order_no TEXT NOT NULL,
    amount REAL NOT NULL,
    channel TEXT NOT NULL DEFAULT 'bank',
    direction TEXT NOT NULL DEFAULT 'refund',
    settled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'settled'
);
"""

# (order_no, title, customer, status, assignee, priority, amount, refund_amount, refund_status, notes, created_at, updated_at)
TICKETS = [
    (
        "ORD-1001",
        "Refund completed — order #1001",
        "Alice Chen",
        "refunded",
        "agent",
        "normal",
        100.0,
        100.0,
        "refunded",
        "Customer refund processed successfully.",
        "2026-06-28T09:00:00",
        "2026-06-28T10:00:00",
    ),
    (
        "ORD-1002",
        "Refund request — missing settlement",
        "Bob Wang",
        "refunded",
        "agent",
        "high",
        200.0,
        200.0,
        "refunded",
        "Refund issued but settlement not yet recorded in ledger.",
        "2026-06-29T10:30:00",
        "2026-06-29T11:00:00",
    ),
    (
        "ORD-1003",
        "Refund amount mismatch",
        "Carol Li",
        "refunded",
        "agent",
        "normal",
        300.0,
        300.0,
        "refunded",
        "Customer expected 300 refund; ledger shows 250.",
        "2026-06-30T14:00:00",
        "2026-07-01T08:00:00",
    ),
    (
        "ORD-1004",
        "Duplicate shipment refund",
        "David Wu",
        "refunded",
        "supervisor",
        "high",
        150.0,
        150.0,
        "refunded",
        "Duplicate charge refund — verify ledger for duplicates.",
        "2026-07-01T16:20:00",
        "2026-07-01T17:00:00",
    ),
    (
        "ORD-1005",
        "Duplicate shipment complaint",
        "Eve Zhang",
        "pending",
        "agent",
        "high",
        599.0,
        0.0,
        "none",
        "Shipped twice; settlement exists but ticket not marked refunded.",
        "2026-07-02T09:45:00",
        "2026-07-02T10:00:00",
    ),
]

# (settlement_no, order_no, amount, channel, direction, settled_at, status)
SETTLEMENTS = [
    ("STL-1001-A", "ORD-1001", 100.0, "alipay", "refund", "2026-06-28T10:05:00", "settled"),
    ("STL-1003-A", "ORD-1003", 250.0, "wechat", "refund", "2026-07-01T08:10:00", "settled"),
    ("STL-1004-A", "ORD-1004", 150.0, "bank", "refund", "2026-07-01T17:05:00", "settled"),
    ("STL-1004-B", "ORD-1004", 150.0, "bank", "refund", "2026-07-01T17:06:00", "settled"),
    ("STL-1005-A", "ORD-1005", 599.0, "alipay", "refund", "2026-07-02T10:30:00", "settled"),
]


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(force: bool = False) -> None:
    if force and DB_PATH.exists():
        DB_PATH.unlink()
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if existing == 0:
            conn.execute(
                "INSERT INTO users (username, password, display_name) VALUES (?, ?, ?)",
                ("agent", "agent123", "Demo Agent"),
            )
            conn.execute(
                "INSERT INTO users (username, password, display_name) VALUES (?, ?, ?)",
                ("supervisor", "super123", "Demo Supervisor"),
            )
            conn.executemany(
                """INSERT INTO tickets
                   (order_no, title, customer, status, assignee, priority, amount,
                    refund_amount, refund_status, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                TICKETS,
            )
            conn.executemany(
                """INSERT INTO settlement_records
                   (settlement_no, order_no, amount, channel, direction, settled_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                SETTLEMENTS,
            )
            conn.commit()
    finally:
        conn.close()


def compute_expected_reconciliation() -> list[dict]:
    """Return expected discrepancy classes for e2e assertions."""
    conn = get_connection()
    try:
        tickets = conn.execute(
            "SELECT order_no, refund_amount, refund_status FROM tickets"
        ).fetchall()
        settlements = conn.execute(
            "SELECT order_no, amount, status FROM settlement_records WHERE status = 'settled'"
        ).fetchall()
    finally:
        conn.close()

    by_order: dict[str, list[float]] = {}
    for row in settlements:
        by_order.setdefault(row["order_no"], []).append(float(row["amount"]))

    discrepancies: list[dict] = []
    for ticket in tickets:
        order_no = ticket["order_no"]
        refund_status = ticket["refund_status"]
        refund_amount = float(ticket["refund_amount"] or 0)
        settled = by_order.get(order_no, [])

        if refund_status == "refunded" and not settled:
            discrepancies.append(
                {"order_no": order_no, "type": "MISSING_SETTLEMENT", "ticket_refund": refund_amount}
            )
        elif refund_status == "refunded" and len(settled) > 1:
            discrepancies.append(
                {
                    "order_no": order_no,
                    "type": "DUPLICATE_REFUND",
                    "ticket_refund": refund_amount,
                    "settlement_total": sum(settled),
                }
            )
        elif refund_status == "refunded" and settled and abs(sum(settled) - refund_amount) > 0.01:
            discrepancies.append(
                {
                    "order_no": order_no,
                    "type": "AMOUNT_MISMATCH",
                    "ticket_refund": refund_amount,
                    "settlement_total": sum(settled),
                }
            )
        elif refund_status != "refunded" and settled:
            discrepancies.append(
                {
                    "order_no": order_no,
                    "type": "ORPHAN_SETTLEMENT",
                    "settlement_total": sum(settled),
                }
            )

    return discrepancies


if __name__ == "__main__":
    init_db(force=True)
    print(f"Seeded database at {DB_PATH}")
    print("Expected discrepancies:", compute_expected_reconciliation())

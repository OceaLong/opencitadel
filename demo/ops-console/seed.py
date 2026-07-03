#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Seed SQLite database for OpsConsole demo."""
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
    title TEXT NOT NULL,
    customer TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    assignee TEXT NOT NULL DEFAULT 'unassigned',
    priority TEXT NOT NULL DEFAULT 'normal',
    amount REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

TICKETS = [
    ("Login issue on mobile app", "Alice Chen", "open", "unassigned", "high", 0.0, "User cannot login after update.", "2026-06-28T09:00:00", "2026-06-28T09:00:00"),
    ("Refund request for order #8821", "Bob Wang", "pending", "agent", "normal", 299.0, "Customer wants refund for duplicate charge.", "2026-06-29T10:30:00", "2026-06-29T11:00:00"),
    ("Billing address update", "Carol Li", "in_progress", "agent", "normal", 0.0, "Need to update billing address before invoice.", "2026-06-30T14:00:00", "2026-07-01T08:00:00"),
    ("VIP account downgrade", "David Wu", "open", "supervisor", "low", 0.0, "Customer requested downgrade from VIP plan.", "2026-07-01T16:20:00", "2026-07-01T16:20:00"),
    ("Duplicate shipment complaint", "Eve Zhang", "pending", "agent", "high", 599.0, "Shipped twice, customer wants refund on second shipment.", "2026-07-02T09:45:00", "2026-07-02T10:00:00"),
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
                   (title, customer, status, assignee, priority, amount, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                TICKETS,
            )
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db(force=True)
    print(f"Seeded database at {DB_PATH}")

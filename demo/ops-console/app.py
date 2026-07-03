#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpsConsole — enterprise ticket & settlement operations backend (web + read-only API)."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from seed import compute_expected_reconciliation, get_connection, init_db

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="OpsConsole", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
SESSION_COOKIE = "ops_console_session"
_sessions: dict[str, str] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _current_user(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return _sessions.get(token)


def _require_user(request: Request) -> Optional[str]:
    return _current_user(request)


def _redirect_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ---------- Read-only REST API (no write endpoints) ----------


@app.get("/api/tickets")
async def api_tickets():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM tickets ORDER BY id ASC").fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


@app.get("/api/settlements")
async def api_settlements():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM settlement_records ORDER BY settled_at ASC"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


@app.get("/api/reconciliation/expected")
async def api_reconciliation_expected():
    """Expected discrepancy result for e2e assertions only."""
    return {
        "generated_at": _now_iso(),
        "discrepancies": compute_expected_reconciliation(),
    }


@app.post("/api/_seed/reset")
async def api_seed_reset():
    init_db(force=True)
    return {"status": "ok", "message": "Database re-seeded"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ops-console"}


# ---------- Web UI ----------


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if _current_user(request):
        return RedirectResponse(url="/tickets", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": error},
    )


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT username FROM users WHERE username = ? AND password = ?",
            (username.strip(), password),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password"},
            status_code=401,
        )
    token = secrets.token_urlsafe(32)
    _sessions[token] = row["username"]
    response = RedirectResponse(url="/tickets", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    return response


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _sessions.pop(token, None)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/tickets", response_class=HTMLResponse)
async def ticket_list(
    request: Request,
    status: Optional[str] = None,
    assignee: Optional[str] = None,
    refund_status: Optional[str] = None,
):
    if not _require_user(request):
        return _redirect_login()
    query = "SELECT * FROM tickets WHERE 1=1"
    params: list[str] = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if assignee:
        query += " AND assignee = ?"
        params.append(assignee)
    if refund_status:
        query += " AND refund_status = ?"
        params.append(refund_status)
    query += " ORDER BY id ASC"
    conn = get_connection()
    try:
        tickets = conn.execute(query, params).fetchall()
        statuses = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT status FROM tickets ORDER BY status"
            ).fetchall()
        ]
        assignees = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT assignee FROM tickets ORDER BY assignee"
            ).fetchall()
        ]
        refund_statuses = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT refund_status FROM tickets ORDER BY refund_status"
            ).fetchall()
        ]
    finally:
        conn.close()
    return templates.TemplateResponse(
        "tickets.html",
        {
            "request": request,
            "user": _current_user(request),
            "tickets": tickets,
            "status_filter": status or "",
            "assignee_filter": assignee or "",
            "refund_status_filter": refund_status or "",
            "statuses": statuses,
            "assignees": assignees,
            "refund_statuses": refund_statuses,
        },
    )


@app.get("/settlements", response_class=HTMLResponse)
async def settlement_list(request: Request):
    if not _require_user(request):
        return _redirect_login()
    conn = get_connection()
    try:
        settlements = conn.execute(
            "SELECT * FROM settlement_records ORDER BY settled_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return templates.TemplateResponse(
        "settlements.html",
        {
            "request": request,
            "user": _current_user(request),
            "settlements": settlements,
        },
    )


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail(request: Request, ticket_id: int, message: Optional[str] = None):
    if not _require_user(request):
        return _redirect_login()
    conn = get_connection()
    try:
        ticket = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
    finally:
        conn.close()
    if not ticket:
        return HTMLResponse("Ticket not found", status_code=404)
    return templates.TemplateResponse(
        "ticket_detail.html",
        {
            "request": request,
            "user": _current_user(request),
            "ticket": ticket,
            "message": message,
        },
    )


@app.post("/tickets/{ticket_id}/update")
async def ticket_update(
    request: Request,
    ticket_id: int,
    status: str = Form(...),
    assignee: str = Form(...),
    note: str = Form(""),
):
    if not _require_user(request):
        return _redirect_login()
    conn = get_connection()
    try:
        ticket = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if not ticket:
            return HTMLResponse("Ticket not found", status_code=404)
        notes = ticket["notes"]
        if note.strip():
            notes = (notes + "\n" if notes else "") + f"[{_now_iso()}] {note.strip()}"
        conn.execute(
            """UPDATE tickets SET status = ?, assignee = ?, notes = ?, updated_at = ?
               WHERE id = ?""",
            (status, assignee, notes, _now_iso(), ticket_id),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        url=f"/tickets/{ticket_id}?message=updated", status_code=303
    )


@app.get("/tickets/{ticket_id}/close", response_class=HTMLResponse)
async def ticket_close_confirm(request: Request, ticket_id: int):
    if not _require_user(request):
        return _redirect_login()
    conn = get_connection()
    try:
        ticket = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
    finally:
        conn.close()
    if not ticket:
        return HTMLResponse("Ticket not found", status_code=404)
    return templates.TemplateResponse(
        "confirm_close.html",
        {"request": request, "user": _current_user(request), "ticket": ticket},
    )


@app.post("/tickets/{ticket_id}/close")
async def ticket_close_submit(
    request: Request,
    ticket_id: int,
    confirm: str = Form(...),
):
    if not _require_user(request):
        return _redirect_login()
    if confirm.strip().lower() != "close":
        return RedirectResponse(
            url=f"/tickets/{ticket_id}/close?error=confirm", status_code=303
        )
    conn = get_connection()
    try:
        ticket = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if not ticket:
            return HTMLResponse("Ticket not found", status_code=404)
        notes = (ticket["notes"] + "\n" if ticket["notes"] else "") + (
            f"[{_now_iso()}] Ticket closed by {_current_user(request)}"
        )
        conn.execute(
            "UPDATE tickets SET status = 'closed', notes = ?, updated_at = ? WHERE id = ?",
            (notes, _now_iso(), ticket_id),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        url=f"/tickets/{ticket_id}?message=closed", status_code=303
    )


@app.get("/tickets/{ticket_id}/refund", response_class=HTMLResponse)
async def ticket_refund_confirm(request: Request, ticket_id: int):
    if not _require_user(request):
        return _redirect_login()
    conn = get_connection()
    try:
        ticket = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
    finally:
        conn.close()
    if not ticket:
        return HTMLResponse("Ticket not found", status_code=404)
    return templates.TemplateResponse(
        "confirm_refund.html",
        {"request": request, "user": _current_user(request), "ticket": ticket},
    )


@app.post("/tickets/{ticket_id}/refund")
async def ticket_refund_submit(
    request: Request,
    ticket_id: int,
    confirm: str = Form(...),
):
    if not _require_user(request):
        return _redirect_login()
    if confirm.strip().lower() != "refund":
        return RedirectResponse(
            url=f"/tickets/{ticket_id}/refund?error=confirm", status_code=303
        )
    conn = get_connection()
    try:
        ticket = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if not ticket:
            return HTMLResponse("Ticket not found", status_code=404)
        refund_amt = float(ticket["refund_amount"] or ticket["amount"] or 0)
        notes = (ticket["notes"] + "\n" if ticket["notes"] else "") + (
            f"[{_now_iso()}] Refund processed (${refund_amt:.2f}) by {_current_user(request)}"
        )
        conn.execute(
            """UPDATE tickets SET status = 'refunded', refund_status = 'refunded',
               refund_amount = ?, notes = ?, updated_at = ? WHERE id = ?""",
            (refund_amt, notes, _now_iso(), ticket_id),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        url=f"/tickets/{ticket_id}?message=refunded", status_code=303
    )

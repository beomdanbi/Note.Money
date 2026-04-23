"""계좌 조회, 납입/출금/이동. 종목 매수는 월말 종가 기준 좌수 환산."""
from __future__ import annotations

from datetime import date

from .db import get_conn
from .prices import resolve_price

# 내부 code → 사용자에게 보여줄 짧은 한글 라벨
DISPLAY_NAMES = {
    "SAVINGS":  "적금",   # legacy (단일 적금 구조 호환)
    "SAVINGS1": "적금1",
    "SAVINGS2": "적금2",
    "ISA":      "ISA",
    "PENSION1": "연금저축1",
    "PENSION2": "연금저축2",
    "PENSION3": "연금저축3",
    "IRP":      "IRP",
    "CASH":     "현금",
}


def display_name(code: str | None) -> str:
    if not code:
        return "-"
    return DISPLAY_NAMES.get(code, code)


def get_account(conn, code: str):
    row = conn.execute("SELECT * FROM accounts WHERE code = ? AND active = 1", (code,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown/inactive account: {code}")
    return row


def list_accounts(active_only: bool = True) -> list[dict]:
    with get_conn() as conn:
        q = "SELECT * FROM accounts"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY id"
        return [dict(r) for r in conn.execute(q).fetchall()]


def account_principal(account_id: int) -> int:
    """원금 = SEED + DEPOSIT - WITHDRAW (OPENING_GAIN, INTEREST, BUY/SELL 제외)."""
    with get_conn() as conn:
        inflow = conn.execute(
            """SELECT COALESCE(SUM(amount),0) s FROM transactions
               WHERE to_account_id = ? AND type IN ('SEED','DEPOSIT','TRANSFER_IN')""",
            (account_id,),
        ).fetchone()["s"]
        outflow = conn.execute(
            """SELECT COALESCE(SUM(amount),0) s FROM transactions
               WHERE from_account_id = ? AND type IN ('WITHDRAW','TRANSFER_OUT')""",
            (account_id,),
        ).fetchone()["s"]
        return int(inflow) - int(outflow)


def deposit(account_code: str, amount: int, d: date, note: str = "월 납입",
            allocate: bool = True) -> None:
    """현금 투입. allocate=True면 target_allocations 비중대로 종목 매수까지 수행."""
    with get_conn() as conn:
        acc = get_account(conn, account_code)
        conn.execute(
            """INSERT INTO transactions(date, type, to_account_id, amount, note)
               VALUES (?, 'DEPOSIT', ?, ?, ?)""",
            (d.isoformat(), acc["id"], int(amount), note),
        )
        kind = acc["kind"]
        if kind in ("SAVINGS", "CASH"):
            virt = "__SAVINGS__" if kind == "SAVINGS" else "__CASH__"
            _add_to_holding(conn, acc["id"], virt, shares_delta=amount, cost_delta=amount)
            return

    if allocate:
        _buy_by_allocation(account_code, amount, d, note=f"{note}/매수")


def withdraw(account_code: str, amount: int, d: date, note: str = "출금",
             liquidate: bool = True) -> None:
    """현금 인출. liquidate=True면 보유 종목을 비중대로 매도해 현금화."""
    with get_conn() as conn:
        acc = get_account(conn, account_code)
        conn.execute(
            """INSERT INTO transactions(date, type, from_account_id, amount, note)
               VALUES (?, 'WITHDRAW', ?, ?, ?)""",
            (d.isoformat(), acc["id"], int(amount), note),
        )
        kind = acc["kind"]
        if kind in ("SAVINGS", "CASH"):
            virt = "__SAVINGS__" if kind == "SAVINGS" else "__CASH__"
            _add_to_holding(conn, acc["id"], virt, shares_delta=-amount, cost_delta=-amount)
            return

    if liquidate:
        _sell_by_holdings(account_code, amount, d, note=f"{note}/매도")


def transfer(from_code: str, to_code: str, amount: int, d: date, note: str = "이동") -> None:
    """계좌간 이동. from에서는 매도/차감, to에서는 매수/증가."""
    withdraw(from_code, amount, d, note=f"→{to_code} {note}")
    deposit(to_code, amount, d, note=f"←{from_code} {note}")

    # transfer 쌍 레코드 추가 (audit)
    with get_conn() as conn:
        fa = get_account(conn, from_code)
        ta = get_account(conn, to_code)
        conn.execute(
            """INSERT INTO transactions(date, type, from_account_id, to_account_id, amount, note)
               VALUES (?, 'TRANSFER', ?, ?, ?, ?)""",
            (d.isoformat(), fa["id"], ta["id"], int(amount), note),
        )


def _add_to_holding(conn, account_id: int, ticker: str, shares_delta: float, cost_delta: int) -> None:
    row = conn.execute(
        "SELECT shares, cost_basis FROM holdings WHERE account_id = ? AND ticker = ?",
        (account_id, ticker),
    ).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO holdings(account_id, ticker, shares, cost_basis)
               VALUES (?, ?, ?, ?)""",
            (account_id, ticker, max(shares_delta, 0), max(cost_delta, 0)),
        )
    else:
        new_shares = float(row["shares"]) + float(shares_delta)
        new_cost = int(row["cost_basis"]) + int(cost_delta)
        if new_shares < 0:
            new_shares = 0.0
        if new_cost < 0:
            new_cost = 0
        conn.execute(
            "UPDATE holdings SET shares = ?, cost_basis = ? WHERE account_id = ? AND ticker = ?",
            (new_shares, new_cost, account_id, ticker),
        )


def _buy_by_allocation(account_code: str, amount: int, d: date, note: str) -> None:
    """amount 만큼을 target_allocations 비중대로 매수."""
    with get_conn() as conn:
        acc = get_account(conn, account_code)
        allocs = conn.execute(
            "SELECT ticker, target_ratio FROM target_allocations WHERE account_id = ?",
            (acc["id"],),
        ).fetchall()
    if not allocs:
        # 비중 정의 없으면 cash로 대기 (현금 holding 생성)
        with get_conn() as conn:
            _add_to_holding(conn, acc["id"], "__CASH__", shares_delta=amount, cost_delta=amount)
        return

    for r in allocs:
        ticker = r["ticker"]
        ratio = r["target_ratio"]
        slice_amt = int(round(amount * ratio))
        if slice_amt <= 0:
            continue
        px, src = resolve_price(ticker, d)
        if px is None or px <= 0:
            # 가격 없으면 매수 보류 → 계좌 내 현금으로 임시 보관
            with get_conn() as conn:
                _add_to_holding(conn, acc["id"], "__CASH__", shares_delta=slice_amt, cost_delta=slice_amt)
                conn.execute(
                    """INSERT INTO transactions(date, type, to_account_id, ticker, amount, note)
                       VALUES (?, 'BUY_PENDING', ?, ?, ?, ?)""",
                    (d.isoformat(), acc["id"], ticker, slice_amt, f"{note}: 가격 미확인"),
                )
            continue
        shares = slice_amt / px
        with get_conn() as conn:
            _add_to_holding(conn, acc["id"], ticker, shares_delta=shares, cost_delta=slice_amt)
            conn.execute(
                """INSERT INTO transactions(date, type, to_account_id, ticker, amount, shares, price, note)
                   VALUES (?, 'BUY', ?, ?, ?, ?, ?, ?)""",
                (d.isoformat(), acc["id"], ticker, slice_amt, shares, px, note),
            )


def _sell_by_holdings(account_code: str, amount: int, d: date, note: str) -> None:
    """amount 만큼을 현재 보유 비중대로 매도."""
    with get_conn() as conn:
        acc = get_account(conn, account_code)
        holdings = conn.execute(
            "SELECT ticker, shares, cost_basis FROM holdings WHERE account_id = ? AND shares > 0",
            (acc["id"],),
        ).fetchall()

    # 각 보유 종목의 평가액 기준 비중
    vals = []
    total_mv = 0.0
    for h in holdings:
        ticker = h["ticker"]
        px, _ = resolve_price(ticker, d)
        if px is None:
            continue
        mv = float(h["shares"]) * px
        vals.append((ticker, h["shares"], h["cost_basis"], px, mv))
        total_mv += mv
    if total_mv <= 0:
        return

    for ticker, shares, cost_basis, px, mv in vals:
        slice_amt = int(round(amount * (mv / total_mv)))
        if slice_amt <= 0:
            continue
        shares_sold = slice_amt / px
        cost_portion = int(round(cost_basis * (shares_sold / shares))) if shares > 0 else 0
        with get_conn() as conn:
            _add_to_holding(conn, acc["id"], ticker, shares_delta=-shares_sold, cost_delta=-cost_portion)
            conn.execute(
                """INSERT INTO transactions(date, type, from_account_id, ticker, amount, shares, price, note)
                   VALUES (?, 'SELL', ?, ?, ?, ?, ?, ?)""",
                (d.isoformat(), acc["id"], ticker, slice_amt, shares_sold, px, note),
            )


def accrue_savings_interest(d: date) -> int:
    """모든 활성 적금 계좌에 월 이자 가산 (연이율/12, 전월 말 잔액 기준).
    리턴: 가산된 이자 합계."""
    total = 0
    with get_conn() as conn:
        accs = conn.execute(
            "SELECT * FROM accounts WHERE kind = 'SAVINGS' AND active = 1"
        ).fetchall()
        for acc in accs:
            row = conn.execute(
                "SELECT shares FROM holdings WHERE account_id = ? AND ticker = '__SAVINGS__'",
                (acc["id"],),
            ).fetchone()
            if row is None:
                continue
            balance = float(row["shares"])
            rate = float(acc["interest_rate"])
            monthly_interest = int(round(balance * rate / 12))
            if monthly_interest <= 0:
                continue
            _add_to_holding(conn, acc["id"], "__SAVINGS__",
                            shares_delta=monthly_interest, cost_delta=0)
            conn.execute(
                """INSERT INTO transactions(date, type, to_account_id, amount, note)
                   VALUES (?, 'INTEREST', ?, ?, ?)""",
                (d.isoformat(), acc["id"], monthly_interest,
                 f"{acc['name']} 이자"),
            )
            total += monthly_interest
    return total

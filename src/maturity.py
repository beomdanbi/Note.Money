"""적금 / ISA 만기 처리."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from .db import get_conn
from .accounts import (
    get_account, deposit, withdraw, _add_to_holding, _sell_by_holdings,
)
from .valuation import account_market_value
from .prices import month_end


def _next_month_day(d: date, day: int) -> date:
    """다음 달의 지정된 일자(존재하지 않으면 말일)."""
    y, m = (d.year + (d.month // 12)), (d.month % 12 + 1)
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(day, last))


def due_maturities(on_date: date) -> list[tuple[str, date]]:
    """해당 일자 <= 만기일 인 계좌 리스트."""
    out: list[tuple[str, date]] = []
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT code, matures_at FROM accounts
               WHERE active = 1 AND matures_at IS NOT NULL
               AND matures_at <= ?""",
            (on_date.isoformat(),),
        ).fetchall()
    for r in rows:
        out.append((r["code"], date.fromisoformat(r["matures_at"])))
    return out


def mature_savings(isa_move_amount: int, on_date: date,
                   code: str = "SAVINGS") -> dict:
    """적금 만기: 평가총액 산출 → isa_move_amount 만큼 ISA 이동, 잔액 현금 이동.
    이후 동일 스펙(월 납입·1년) 적금 신규 개설. 여러 적금이 있을 때는 code로 지정."""
    with get_conn() as conn:
        acc = conn.execute(
            "SELECT * FROM accounts WHERE code = ? AND kind = 'SAVINGS' AND active = 1",
            (code,),
        ).fetchone()
        if acc is None:
            raise RuntimeError(f"활성 적금({code}) 없음")
        row = conn.execute(
            "SELECT shares FROM holdings WHERE account_id = ? AND ticker = '__SAVINGS__'",
            (acc["id"],),
        ).fetchone()
        balance = int(round(float(row["shares"]))) if row else 0

    if isa_move_amount > balance:
        raise ValueError(f"이동 요청 {isa_move_amount:,} > 잔액 {balance:,}")

    cash_amount = balance - isa_move_amount

    # 이동 기록 (from=savings)
    if isa_move_amount > 0:
        _transfer_from_cash_like(code, "ISA", isa_move_amount, on_date,
                                 note=f"{acc['name']} 만기→ISA")
    if cash_amount > 0:
        _transfer_from_cash_like(code, "CASH", cash_amount, on_date,
                                 note=f"{acc['name']} 만기 잔액")

    # 기존 적금 만기 처리 (비활성)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO transactions(date, type, from_account_id, amount, note)
               VALUES (?, 'MATURITY', ?, ?, ?)""",
            (on_date.isoformat(), acc["id"], balance,
             f"{acc['name']} 만기 해지"),
        )
        conn.execute("UPDATE accounts SET active = 0 WHERE id = ?", (acc["id"],))

        # 새 적금 개설 (1년 만기)
        new_mat = date(on_date.year + 1, on_date.month, min(on_date.day,
                       calendar.monthrange(on_date.year + 1, on_date.month)[1]))
        next_gen = int(acc["generation"]) + 1
        base_name = str(acc["name"]).split(" (gen ")[0]
        conn.execute(
            """INSERT INTO accounts
               (code, name, kind, monthly_deposit, opened_at, matures_at,
                interest_rate, cycle_months, tax_deductible, active, generation)
               VALUES (?, ?, 'SAVINGS', ?, ?, ?, ?, 12, 0, 1, ?)""",
            (code, f"{base_name} (gen {next_gen})", int(acc["monthly_deposit"]),
             on_date.isoformat(), new_mat.isoformat(),
             float(acc["interest_rate"]), next_gen),
        )
        new_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        conn.execute(
            """INSERT INTO holdings(account_id, ticker, shares, cost_basis)
               VALUES (?, '__SAVINGS__', 0, 0)""",
            (new_id,),
        )

    return {"code": code, "balance": balance,
            "to_isa": isa_move_amount, "to_cash": cash_amount,
            "new_maturity": new_mat.isoformat()}


def mature_isa(on_date: date) -> dict:
    """ISA 만기: 평가총액의 10% → 연금저축1 (세액공제 신고용), 90% → 연금저축3.
    기존 보유 전량 매도 → 현금화 → 이동 → ISA 신규 개설."""
    mv = account_market_value("ISA", on_date)
    principal_at_maturity = _principal_of("ISA")

    to_pension1 = int(round(mv * 0.10))
    to_pension3 = mv - to_pension1

    # ISA 전액 매도
    with get_conn() as conn:
        isa = conn.execute(
            "SELECT * FROM accounts WHERE code = 'ISA' AND active = 1"
        ).fetchone()
        if isa is None:
            raise RuntimeError("활성 ISA 없음")

    _liquidate_all("ISA", on_date)

    # 이동
    if to_pension1 > 0:
        _cash_transfer("ISA", "PENSION1", to_pension1, on_date, note="ISA 만기 10%→연금1(세공)")
    if to_pension3 > 0:
        _cash_transfer("ISA", "PENSION3", to_pension3, on_date, note="ISA 만기 90%→연금3")

    # 기존 ISA 비활성 + 신규 개설
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO transactions(date, type, from_account_id, amount, note)
               VALUES (?, 'MATURITY', ?, ?, ?)""",
            (on_date.isoformat(), isa["id"], mv,
             f"ISA 만기 (원금 {principal_at_maturity:,}, 평가 {mv:,})"),
        )
        conn.execute("UPDATE accounts SET active = 0 WHERE id = ?", (isa["id"],))

        # 신규 ISA: 3년 뒤 말일
        y, m = on_date.year + 3, on_date.month
        new_mat = date(y, m, calendar.monthrange(y, m)[1])
        next_gen = int(isa["generation"]) + 1
        conn.execute(
            """INSERT INTO accounts
               (code, name, kind, monthly_deposit, opened_at, matures_at,
                interest_rate, cycle_months, tax_deductible, active, generation)
               VALUES ('ISA', ?, 'ISA', ?, ?, ?, 0, 36, 0, 1, ?)""",
            (f"ISA (gen {next_gen})", int(isa["monthly_deposit"]),
             on_date.isoformat(), new_mat.isoformat(), next_gen),
        )
        new_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

        # 기존 target_allocations 복제
        conn.execute(
            """INSERT INTO target_allocations (account_id, ticker, name, target_ratio)
               SELECT ?, ticker, name, target_ratio FROM target_allocations WHERE account_id = ?""",
            (new_id, isa["id"]),
        )

    return {"market_value": mv, "principal": principal_at_maturity,
            "to_pension1": to_pension1, "to_pension3": to_pension3,
            "new_maturity": new_mat.isoformat()}


def _principal_of(code: str) -> int:
    from .accounts import account_principal, get_account
    with get_conn() as conn:
        acc = get_account(conn, code)
    return account_principal(acc["id"])


def _liquidate_all(code: str, d: date) -> int:
    """해당 계좌 보유 종목 전량 매도. 반환: 매도 총액(원)."""
    from .prices import resolve_price
    with get_conn() as conn:
        acc = get_account(conn, code)
        holdings = conn.execute(
            "SELECT ticker, shares, cost_basis FROM holdings WHERE account_id = ? AND shares > 0",
            (acc["id"],),
        ).fetchall()

    total = 0
    for h in holdings:
        ticker = h["ticker"]
        shares = float(h["shares"])
        cost_basis = int(h["cost_basis"])
        if ticker in ("__CASH__", "__SAVINGS__"):
            px = 1.0
        else:
            px, _ = resolve_price(ticker, d)
            if px is None:
                continue
        amt = int(round(shares * px))
        total += amt
        with get_conn() as conn:
            conn.execute(
                "UPDATE holdings SET shares = 0, cost_basis = 0 WHERE account_id = ? AND ticker = ?",
                (acc["id"], ticker),
            )
            conn.execute(
                """INSERT INTO transactions(date, type, from_account_id, ticker, amount, shares, price, note)
                   VALUES (?, 'SELL', ?, ?, ?, ?, ?, '만기 전량매도')""",
                (d.isoformat(), acc["id"], ticker, amt, shares, px),
            )
    return total


def _transfer_from_cash_like(from_code: str, to_code: str, amount: int,
                              d: date, note: str) -> None:
    """SAVINGS/CASH 계좌에서 다른 계좌로 자금 이동."""
    with get_conn() as conn:
        fa = get_account(conn, from_code)
        kind = fa["kind"]
        virt = "__SAVINGS__" if kind == "SAVINGS" else "__CASH__"
        _add_to_holding(conn, fa["id"], virt, shares_delta=-amount, cost_delta=-amount)
        conn.execute(
            """INSERT INTO transactions(date, type, from_account_id, amount, note)
               VALUES (?, 'WITHDRAW', ?, ?, ?)""",
            (d.isoformat(), fa["id"], amount, f"→{to_code} {note}"),
        )
    deposit(to_code, amount, d, note=f"←{from_code} {note}", allocate=True)
    with get_conn() as conn:
        fa = get_account(conn, from_code)
        ta = conn.execute("SELECT id FROM accounts WHERE code = ? AND active = 1",
                          (to_code,)).fetchone()
        conn.execute(
            """INSERT INTO transactions(date, type, from_account_id, to_account_id, amount, note)
               VALUES (?, 'TRANSFER', ?, ?, ?, ?)""",
            (d.isoformat(), fa["id"], ta["id"], amount, note),
        )


def _cash_transfer(from_code: str, to_code: str, amount: int, d: date, note: str) -> None:
    """이미 매도된 ISA(현금 상태) → 타 계좌 이동."""
    # ISA는 매도 후 현금으로는 추적되지 않음(보유좌수 0). 여기서는 단순히 DEPOSIT만 기록.
    with get_conn() as conn:
        fa = get_account(conn, from_code)
        conn.execute(
            """INSERT INTO transactions(date, type, from_account_id, amount, note)
               VALUES (?, 'WITHDRAW', ?, ?, ?)""",
            (d.isoformat(), fa["id"], amount, f"→{to_code} {note}"),
        )
    deposit(to_code, amount, d, note=f"←{from_code} {note}", allocate=True)
    with get_conn() as conn:
        fa = get_account(conn, from_code)
        ta = conn.execute("SELECT id FROM accounts WHERE code = ? AND active = 1",
                          (to_code,)).fetchone()
        conn.execute(
            """INSERT INTO transactions(date, type, from_account_id, to_account_id, amount, note)
               VALUES (?, 'TRANSFER', ?, ?, ?, ?)""",
            (d.isoformat(), fa["id"], ta["id"], amount, note),
        )

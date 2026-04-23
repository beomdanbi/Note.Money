"""월말 종가 × 보유좌수 기준 평가, 수익률 계산, 스냅샷 저장."""
from __future__ import annotations

from datetime import date

from .db import get_conn
from .accounts import account_principal, list_accounts
from .prices import resolve_price, month_end


def account_market_value(code: str, on_date: date) -> int:
    with get_conn() as conn:
        acc = conn.execute(
            "SELECT * FROM accounts WHERE code = ? AND active = 1", (code,)
        ).fetchone()
        if acc is None:
            return 0
        holdings = conn.execute(
            "SELECT ticker, shares FROM holdings WHERE account_id = ?", (acc["id"],)
        ).fetchall()
    total = 0.0
    for h in holdings:
        ticker = h["ticker"]
        shares = float(h["shares"])
        if shares <= 0:
            continue
        if ticker in ("__CASH__", "__SAVINGS__"):
            px = 1.0
        else:
            px, _ = resolve_price(ticker, on_date)
            if px is None:
                continue
        total += shares * px

    # Opening gain: 시드 시점 평가이익이 원금과 별개로 기록됨. 매수가 아직 실현되지 않은
    # 구간에서도 평가에 반영되어야 하므로 더한다 (단, 이미 market_value에 내재되어
    # 있으면 더블카운트 주의). 본 구현은 SEED 시 shares = market_value/price 로 주입하므로
    # opening_gain은 이미 shares에 포함됨. 여기서는 원금 계정 연산에만 영향.
    return int(round(total))


def account_summary(code: str, on_date: date) -> dict:
    with get_conn() as conn:
        acc = conn.execute(
            "SELECT * FROM accounts WHERE code = ? AND active = 1", (code,)
        ).fetchone()
    if acc is None:
        return {"code": code, "active": False}
    p = account_principal(acc["id"])
    mv = account_market_value(code, on_date)
    ret_amt = mv - p
    ret_pct = (ret_amt / p * 100) if p > 0 else 0.0
    return {
        "code": code,
        "name": acc["name"],
        "kind": acc["kind"],
        "principal": p,
        "market_value": mv,
        "return_amount": ret_amt,
        "return_pct": ret_pct,
        "matures_at": acc["matures_at"],
    }


def portfolio_summary(on_date: date) -> dict:
    rows = []
    total_p = 0
    total_mv = 0
    for a in list_accounts():
        s = account_summary(a["code"], on_date)
        rows.append(s)
        total_p += s.get("principal", 0)
        total_mv += s.get("market_value", 0)
    ret = total_mv - total_p
    ret_pct = (ret / total_p * 100) if total_p > 0 else 0.0
    return {
        "on_date": on_date.isoformat(),
        "accounts": rows,
        "total_principal": total_p,
        "total_market_value": total_mv,
        "total_return": ret,
        "total_return_pct": ret_pct,
    }


def save_monthly_snapshot(on_date: date) -> None:
    d = month_end(on_date)
    with get_conn() as conn:
        accs = conn.execute("SELECT id, code FROM accounts WHERE active = 1").fetchall()
        for a in accs:
            p = account_principal(a["id"])
            mv = account_market_value(a["code"], d)
            ret = mv - p
            pct = (ret / p * 100) if p > 0 else 0.0
            conn.execute(
                """INSERT OR REPLACE INTO monthly_snapshots
                   (account_id, date, principal, market_value, return_amount, return_pct)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (a["id"], d.isoformat(), p, mv, ret, pct),
            )

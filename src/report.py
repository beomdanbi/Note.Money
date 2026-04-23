"""rich 기반 표 출력."""
from __future__ import annotations

from datetime import date

from rich.console import Console
from rich.table import Table

from .accounts import display_name
from .db import get_conn
from .valuation import portfolio_summary

console = Console()


def _fmt(n: int) -> str:
    return f"{n:,}원"


def _fmt_pct(p: float) -> str:
    return f"{p:+.2f}%"


def print_status(on_date: date) -> None:
    s = portfolio_summary(on_date)
    t = Table(title=f"포트폴리오 현황 ({s['on_date']})", show_lines=False)
    t.add_column("계좌", style="cyan")
    t.add_column("종류")
    t.add_column("원금", justify="right")
    t.add_column("평가액", justify="right")
    t.add_column("수익금", justify="right")
    t.add_column("수익률", justify="right")
    t.add_column("만기", justify="center")
    for r in s["accounts"]:
        if not r.get("market_value") and not r.get("principal"):
            continue
        t.add_row(
            display_name(r["code"]), r["kind"],
            _fmt(r["principal"]), _fmt(r["market_value"]),
            _fmt(r["return_amount"]), _fmt_pct(r["return_pct"]),
            r.get("matures_at") or "-",
        )
    t.add_section()
    t.add_row("합계", "",
              _fmt(s["total_principal"]),
              _fmt(s["total_market_value"]),
              _fmt(s["total_return"]),
              _fmt_pct(s["total_return_pct"]), "")
    console.print(t)


def print_holdings(code: str, on_date: date) -> None:
    from .prices import resolve_price
    with get_conn() as conn:
        acc = conn.execute(
            "SELECT * FROM accounts WHERE code = ? AND active = 1", (code,)
        ).fetchone()
        if acc is None:
            console.print(f"[red]{code} 계좌 없음[/red]")
            return
        holdings = conn.execute(
            """SELECT h.ticker, h.shares, h.cost_basis, a.name
               FROM holdings h
               LEFT JOIN target_allocations a
                 ON a.account_id = h.account_id AND a.ticker = h.ticker
               WHERE h.account_id = ?""",
            (acc["id"],),
        ).fetchall()

    t = Table(title=f"{display_name(code)} 보유 종목")
    t.add_column("티커")
    t.add_column("종목명")
    t.add_column("좌수", justify="right")
    t.add_column("평단가", justify="right")
    t.add_column("현재가", justify="right")
    t.add_column("평가액", justify="right")
    t.add_column("수익률", justify="right")
    total_mv = 0
    total_cb = 0
    for h in holdings:
        ticker = h["ticker"]
        shares = float(h["shares"])
        cb = int(h["cost_basis"])
        if shares <= 0 and cb <= 0:
            continue
        if ticker in ("__CASH__", "__SAVINGS__"):
            px = 1.0
        else:
            px, _ = resolve_price(ticker, on_date)
            px = px or 0
        mv = int(round(shares * px))
        avg = (cb / shares) if shares > 0 else 0.0
        ret_pct = ((mv - cb) / cb * 100) if cb > 0 else 0.0
        total_mv += mv
        total_cb += cb
        t.add_row(ticker, h["name"] or ticker,
                  f"{shares:,.4f}", f"{avg:,.0f}",
                  f"{px:,.0f}", _fmt(mv), _fmt_pct(ret_pct))
    t.add_section()
    tot_ret = (total_mv - total_cb) / total_cb * 100 if total_cb > 0 else 0.0
    t.add_row("합계", "", "", "", "", _fmt(total_mv), _fmt_pct(tot_ret))
    console.print(t)


def print_history(limit: int = 30) -> None:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.date, t.type, t.amount, t.ticker,
                      fa.code AS from_code, ta.code AS to_code, t.note
               FROM transactions t
               LEFT JOIN accounts fa ON fa.id = t.from_account_id
               LEFT JOIN accounts ta ON ta.id = t.to_account_id
               ORDER BY t.date DESC, t.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    t = Table(title=f"최근 거래 {limit}건")
    t.add_column("일자")
    t.add_column("유형")
    t.add_column("from")
    t.add_column("to")
    t.add_column("종목")
    t.add_column("금액", justify="right")
    t.add_column("메모")
    for r in rows:
        t.add_row(r["date"], r["type"],
                  display_name(r["from_code"]), display_name(r["to_code"]),
                  r["ticker"] or "-",
                  _fmt(r["amount"] or 0), r["note"] or "")
    console.print(t)


def print_monthly_history(months: int = 12) -> None:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.date, a.code, s.principal, s.market_value,
                      s.return_amount, s.return_pct
               FROM monthly_snapshots s
               JOIN accounts a ON a.id = s.account_id
               WHERE s.date >= date('now', ? || ' months')
               ORDER BY s.date DESC, a.id""",
            (f"-{months}",),
        ).fetchall()

    t = Table(title=f"월별 스냅샷 (최근 {months}개월)")
    t.add_column("월말"); t.add_column("계좌")
    t.add_column("원금", justify="right")
    t.add_column("평가액", justify="right")
    t.add_column("수익", justify="right")
    t.add_column("수익률", justify="right")
    for r in rows:
        t.add_row(r["date"], display_name(r["code"]),
                  _fmt(r["principal"]), _fmt(r["market_value"]),
                  _fmt(r["return_amount"]), _fmt_pct(r["return_pct"]))
    console.print(t)

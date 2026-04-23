"""대화형 CLI - 월말 납입 / 조회 / 만기 / 시뮬레이션."""
from __future__ import annotations

import sys
from datetime import date, datetime

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt

from .accounts import (
    accrue_savings_interest, deposit, display_name, get_account,
    list_accounts, transfer, withdraw,
)
from .db import get_conn, init_db
from .maturity import due_maturities, mature_isa, mature_savings
from .prices import fetch_prices_for_date, month_end, set_manual_price
from .projection import print_projection
from .report import print_history, print_holdings, print_monthly_history, print_status
from .seed import ALLOCATIONS, SEED_DATE, all_tickers, apply_opening_balances, seed
from .valuation import save_monthly_snapshot

console = Console()


def _parse_date(s: str | None, default: date) -> date:
    if not s:
        return default
    return datetime.strptime(s, "%Y-%m-%d").date()


def cmd_init(args: list[str]) -> None:
    reset = "--reset" in args
    console.rule("[bold]초기화[/bold]")
    if reset and not Confirm.ask("[red]DB 초기화 (모든 데이터 삭제)?[/red]", default=False):
        return
    seed(reset=reset)
    console.print(f"[green]계좌·비중 시딩 완료 (기준일 {SEED_DATE})[/green]")

    console.print("초기 가격 조회 중...")
    tickers = all_tickers()
    prices = fetch_prices_for_date(tickers, date.fromisoformat(SEED_DATE))
    missing = [t for t in tickers if t not in prices]
    if missing:
        console.print(f"[yellow]가격 미수신 티커 {len(missing)}개: {', '.join(missing)}[/yellow]")
        console.print("수동 입력 원하면 'python main.py price <ticker> <price>' 실행")

    skipped = apply_opening_balances(prices)
    if skipped:
        console.print(f"[yellow]초기 보유 좌수 계산 스킵: {len(skipped)}건[/yellow]")
        for s in skipped[:10]:
            console.print(f"  - {s}")

    print_status(date.fromisoformat(SEED_DATE))


def cmd_status(args: list[str]) -> None:
    d = _parse_date(args[0] if args else None, date.today())
    print_status(d)


def cmd_holdings(args: list[str]) -> None:
    if not args:
        console.print("사용: holdings <ACCOUNT_CODE> [date]")
        return
    d = _parse_date(args[1] if len(args) > 1 else None, date.today())
    print_holdings(args[0].upper(), d)


def cmd_history(args: list[str]) -> None:
    limit = int(args[0]) if args else 30
    print_history(limit)


def cmd_snapshots(args: list[str]) -> None:
    months = int(args[0]) if args else 12
    print_monthly_history(months)


def _ask_monthly_deposits() -> dict[str, int]:
    """각 활성 계좌별 납입액 입력. 빈 값 = 기본 월 정기납입액 사용."""
    out: dict[str, int] = {}
    for a in list_accounts():
        if a["monthly_deposit"] <= 0 and a["kind"] not in ("PENSION",):
            continue
        default = int(a["monthly_deposit"])
        prompt = f"{display_name(a['code'])} 납입액 [{default:,}]"
        raw = Prompt.ask(prompt, default=str(default))
        try:
            amt = int(raw.replace(",", ""))
        except ValueError:
            amt = default
        out[a["code"]] = amt
    return out


def cmd_deposit_monthly(args: list[str]) -> None:
    """월말 일괄 납입. 진행 순서: 만기 체크 → 납입 → 이자 → 스냅샷."""
    today = date.today()
    d = _parse_date(args[0] if args else None, month_end(today))
    console.rule(f"[bold]월 납입 ({d})[/bold]")

    # 만기 먼저
    mats = due_maturities(d)
    for code, mdate in mats:
        _handle_maturity_interactive(code, mdate)

    # 납입 입력
    deposits = _ask_monthly_deposits()
    for code, amt in deposits.items():
        if amt <= 0:
            continue
        deposit(code, amt, d, note="월 정기 납입")
        console.print(f"  {display_name(code)}: {amt:,}원 납입")

    # 적금 이자
    interest = accrue_savings_interest(d)
    if interest > 0:
        console.print(f"  적금 이자 {interest:,}원 반영")

    # 스냅샷
    save_monthly_snapshot(d)
    console.print("[green]월말 스냅샷 저장 완료[/green]")
    print_status(d)


def _handle_maturity_interactive(code: str, mdate: date) -> None:
    console.rule(f"[bold yellow]{display_name(code)} 만기 ({mdate})[/bold yellow]")
    with get_conn() as conn:
        acc = get_account(conn, code)
    if acc["kind"] == "SAVINGS":
        with get_conn() as conn:
            row = conn.execute(
                "SELECT shares FROM holdings WHERE account_id=? AND ticker='__SAVINGS__'",
                (acc["id"],),
            ).fetchone()
        bal = int(round(float(row["shares"]))) if row else 0
        console.print(f"{display_name(code)} 잔액: {bal:,}원")
        suggested = min(10_000_000, bal)
        amt = IntPrompt.ask(f"ISA로 이동할 금액 [{suggested:,}]", default=suggested)
        r = mature_savings(amt, mdate, code=code)
        console.print(f"[green]ISA {r['to_isa']:,} / 현금 {r['to_cash']:,} 이동. "
                      f"신규 {display_name(code)} 만기 {r['new_maturity']}[/green]")
    elif acc["kind"] == "ISA":
        from .valuation import account_market_value
        mv = account_market_value("ISA", mdate)
        console.print(f"ISA 평가액: {mv:,}원")
        console.print("룰 적용: 10% → 연금저축1 / 90% → 연금저축3")
        if Confirm.ask("진행?", default=True):
            r = mature_isa(mdate)
            console.print(f"[green]연금1 {r['to_pension1']:,} / "
                          f"연금3 {r['to_pension3']:,} 이동. "
                          f"신규 ISA 만기 {r['new_maturity']}[/green]")


def cmd_deposit(args: list[str]) -> None:
    if len(args) < 2:
        console.print("사용: deposit <CODE> <AMOUNT> [date] [note]")
        return
    code = args[0].upper()
    amt = int(args[1].replace(",", ""))
    d = _parse_date(args[2] if len(args) > 2 else None, date.today())
    note = args[3] if len(args) > 3 else "추가 납입"
    deposit(code, amt, d, note=note)
    console.print(f"[green]{display_name(code)}에 {amt:,} 납입[/green]")


def cmd_withdraw(args: list[str]) -> None:
    if len(args) < 2:
        console.print("사용: withdraw <CODE> <AMOUNT> [date] [note]")
        return
    code = args[0].upper()
    amt = int(args[1].replace(",", ""))
    d = _parse_date(args[2] if len(args) > 2 else None, date.today())
    note = args[3] if len(args) > 3 else "출금"
    withdraw(code, amt, d, note=note)
    console.print(f"[green]{display_name(code)}에서 {amt:,} 출금[/green]")


def cmd_transfer(args: list[str]) -> None:
    if len(args) < 3:
        console.print("사용: transfer <FROM> <TO> <AMOUNT> [date] [note]")
        return
    f = args[0].upper()
    to = args[1].upper()
    amt = int(args[2].replace(",", ""))
    d = _parse_date(args[3] if len(args) > 3 else None, date.today())
    note = args[4] if len(args) > 4 else "이동"
    transfer(f, to, amt, d, note=note)
    console.print(f"[green]{display_name(f)} → {display_name(to)} {amt:,} 이동[/green]")


def cmd_project(args: list[str]) -> None:
    years = int(args[0]) if args else 10
    spread = float(args[1]) if len(args) > 1 else 0.02
    start = _parse_date(args[2] if len(args) > 2 else None, date.today())
    savings_move = int(args[3].replace(",", "")) if len(args) > 3 else 10_000_000
    print_projection(years=years, start_date=start, spread=spread,
                     savings_isa_move=savings_move)


def cmd_price(args: list[str]) -> None:
    if len(args) < 2:
        console.print("사용: price <ticker> <close> [date]")
        return
    t = args[0]
    c = float(args[1])
    d = _parse_date(args[2] if len(args) > 2 else None, date.today())
    set_manual_price(t, d, c)
    console.print(f"[green]{t} @ {d} = {c} 저장[/green]")


def cmd_mature(args: list[str]) -> None:
    if not args:
        console.print("사용: mature <SAVINGS1|SAVINGS2|ISA> [date] [isa_move_amount]")
        return
    code = args[0].upper()
    d = _parse_date(args[1] if len(args) > 1 else None, date.today())
    with get_conn() as conn:
        acc = get_account(conn, code)
    if acc["kind"] == "SAVINGS":
        amt = int(args[2].replace(",", "")) if len(args) > 2 else 10_000_000
        r = mature_savings(amt, d, code=code)
        console.print(r)
    elif acc["kind"] == "ISA":
        r = mature_isa(d)
        console.print(r)


COMMANDS = {
    "init":      (cmd_init,      "DB 초기화 및 시드 (--reset 로 완전 리셋)"),
    "status":    (cmd_status,    "포트폴리오 현황 [date]"),
    "holdings":  (cmd_holdings,  "<계좌> 보유 종목 상세"),
    "history":   (cmd_history,   "[N] 최근 거래 내역"),
    "snapshots": (cmd_snapshots, "[months] 월별 스냅샷"),
    "月":        (cmd_deposit_monthly, "월말 일괄 납입 [date] (만기체크+납입+이자+스냅샷)"),
    "monthly":   (cmd_deposit_monthly, "월말 일괄 납입 [date]"),
    "deposit":   (cmd_deposit,   "<CODE> <AMOUNT> [date] [note] - 추가 납입"),
    "withdraw":  (cmd_withdraw,  "<CODE> <AMOUNT> [date] [note] - 출금"),
    "transfer":  (cmd_transfer,  "<FROM> <TO> <AMOUNT> [date] [note]"),
    "project":   (cmd_project,   "<years> [spread=0.02] [date] [savings_move=10M] - 미래가치 시뮬"),
    "price":     (cmd_price,     "<ticker> <close> [date] - 수동 가격 저장"),
    "mature":    (cmd_mature,    "<SAVINGS|ISA> [date] - 만기 수동 처리"),
}


def _print_help() -> None:
    console.print("[bold]Note.Money 명령[/bold]")
    for name, (_, desc) in COMMANDS.items():
        console.print(f"  [cyan]{name:10}[/cyan] {desc}")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        _print_help()
        return
    cmd, *rest = args
    if cmd not in COMMANDS:
        console.print(f"[red]알 수 없는 명령: {cmd}[/red]")
        _print_help()
        sys.exit(1)
    init_db()  # idempotent
    COMMANDS[cmd][0](rest)


if __name__ == "__main__":
    main()

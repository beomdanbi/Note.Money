"""미래 가치 시뮬레이션 - N년 후 누적 납입액 / 평가액 / 수익.

각 계좌의 목표비중 × 종목별 과거 CAGR로 가중 기대수익률 산출.
월복리로 전개하되 적금·ISA 만기 이벤트는 타임라인에 반영.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from .db import get_conn
from .prices import _proxy_ticker, month_end, resolve_price

# 자산군별 기본 CAGR (과거 데이터 조회 실패 시 폴백)
DEFAULT_CAGR = {
    "equity_us":   0.10,
    "equity_kr":   0.05,
    "equity_em":   0.07,
    "bond_us":     0.02,
    "commodity":   0.04,
    "mmf":         0.035,
    "blended":     0.06,
}

# 티커 → 자산군 (폴백 매핑)
TICKER_ASSET_CLASS = {
    "379800": "equity_us", "379810": "equity_us", "486450": "equity_us",
    "458730": "equity_us", "251350": "equity_us", "472720": "equity_us",
    "453850": "bond_us",   "365780": "bond_us",
    "453870": "equity_em", "465580": "equity_em", "371160": "equity_em",
    "283580": "equity_em", "241180": "equity_us",
    "468620": "blended",   "329650": "blended",
    "473980": "mmf",
    "132030": "commodity", "144600": "commodity",
    "160580": "commodity", "261220": "commodity",
}


def _price_source(ticker: str, d: date) -> str | None:
    """DB에 저장된 원본 source 문자열. resolve_price의 'cache' 마스킹을 우회."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT source FROM prices WHERE ticker = ? AND date = ?",
            (ticker, d.isoformat()),
        ).fetchone()
        return str(row["source"]) if row else None


def _proxy_series_cagr(ticker: str, start_me: date, end_me: date) -> float | None:
    """proxy 티커로 양 끝 가격을 일관되게 조회해 CAGR 산출. 단위 혼용 방지용."""
    proxy = _proxy_ticker(ticker)
    if proxy is None:
        return None
    s_px, _ = resolve_price(proxy, start_me, use_proxy=False)
    e_px, _ = resolve_price(proxy, end_me, use_proxy=False)
    if s_px is None or e_px is None or s_px <= 0 or e_px <= 0:
        return None
    years = (end_me - start_me).days / 365.25
    if years <= 0:
        return None
    return (e_px / s_px) ** (1 / years) - 1


def historical_cagr(ticker: str, lookback_years: int = 5,
                    end_date: date | None = None) -> float | None:
    """시작/끝 월말 종가만 조회하여 CAGR 계산. 데이터 부족 또는 단위 혼용 시 None."""
    end = end_date or date.today()
    end_me = month_end(end - timedelta(days=1))
    start_candidate = date(end_me.year - lookback_years, end_me.month, 1)
    start_me = month_end(start_candidate)

    start_px, start_src = resolve_price(ticker, start_me)
    end_px, end_src = resolve_price(ticker, end_me)
    if start_px is None or end_px is None or start_px <= 0 or end_px <= 0:
        return None

    # resolve_price가 캐시 히트 시 'cache'로 뭉개므로 DB에서 실제 source 재확인
    if start_src == "cache":
        start_src = _price_source(ticker, start_me) or start_src
    if end_src == "cache":
        end_src = _price_source(ticker, end_me) or end_src

    # 한쪽이 proxy(보통 USD)이고 다른쪽이 native(KRW)이면 통화 단위가 달라 CAGR이
    # 폭주함 (예: KRX 미상장 구간은 USD proxy로 폴백). 프록시 시리즈로 재계산 시도,
    # 실패 시 None 반환하여 자산군 폴백 CAGR 사용.
    start_is_proxy = start_src.startswith("proxy:")
    end_is_proxy = end_src.startswith("proxy:")
    if start_is_proxy != end_is_proxy:
        return _proxy_series_cagr(ticker, start_me, end_me)

    years = (end_me - start_me).days / 365.25
    if years <= 0:
        return None
    return (end_px / start_px) ** (1 / years) - 1


def _asset_class_cagr(ticker: str) -> float:
    cls = TICKER_ASSET_CLASS.get(ticker, "blended")
    return DEFAULT_CAGR[cls]


def expected_cagr_table(end_date: date | None = None,
                        lookback_years: int = 5) -> dict[str, float]:
    """모든 티커에 대해 과거 CAGR (또는 폴백) 반환."""
    with get_conn() as conn:
        tickers = [r["ticker"] for r in conn.execute(
            "SELECT DISTINCT ticker FROM target_allocations"
        ).fetchall()]
    out: dict[str, float] = {}
    for t in tickers:
        c = historical_cagr(t, lookback_years, end_date)
        if c is None:
            c = _asset_class_cagr(t)
        out[t] = c
    return out


def weighted_cagr(account_code: str, cagr_table: dict[str, float]) -> float:
    with get_conn() as conn:
        acc = conn.execute(
            "SELECT id FROM accounts WHERE code = ? AND active = 1",
            (account_code,),
        ).fetchone()
        if acc is None:
            return 0.0
        rows = conn.execute(
            "SELECT ticker, target_ratio FROM target_allocations WHERE account_id = ?",
            (acc["id"],),
        ).fetchall()
    total_w = sum(r["target_ratio"] for r in rows) or 1.0
    s = 0.0
    for r in rows:
        c = cagr_table.get(r["ticker"], _asset_class_cagr(r["ticker"]))
        s += r["target_ratio"] * c
    return s / total_w


@dataclass
class SimAccount:
    code: str
    kind: str
    principal: float
    market_value: float
    monthly_deposit: float
    cagr: float
    interest_rate: float = 0.0
    months_to_maturity: int | None = None
    cycle_months: int = 0
    extra_every_3y_from_isa: bool = False  # 참고용 플래그


@dataclass
class SimResult:
    scenario: str
    annual_adjustment: float
    yearly: list[dict] = field(default_factory=list)  # per-year totals
    by_account_final: dict = field(default_factory=dict)


def _init_states(start_date: date, adjustment: float,
                 cagr_table: dict[str, float],
                 monthly_override: dict[str, int] | None = None) -> dict[str, SimAccount]:
    from .valuation import account_market_value
    from .accounts import account_principal

    states: dict[str, SimAccount] = {}
    with get_conn() as conn:
        accs = conn.execute("SELECT * FROM accounts WHERE active = 1").fetchall()

    for a in accs:
        code = a["code"]
        kind = a["kind"]
        p = account_principal(a["id"])
        mv = account_market_value(code, start_date)
        cagr = weighted_cagr(code, cagr_table)
        if kind in ("CASH",):
            cagr = 0.0
        if kind == "SAVINGS":
            cagr = 0.0
        monthly = int(a["monthly_deposit"])
        if monthly_override and code in monthly_override:
            monthly = int(monthly_override[code])

        mm: int | None = None
        if a["matures_at"]:
            mat = date.fromisoformat(a["matures_at"])
            mm = (mat.year - start_date.year) * 12 + (mat.month - start_date.month)
            if mm < 1:
                mm = 1

        states[code] = SimAccount(
            code=code, kind=kind,
            principal=float(p), market_value=float(mv),
            monthly_deposit=float(monthly),
            cagr=cagr + adjustment,
            interest_rate=float(a["interest_rate"]),
            months_to_maturity=mm,
            cycle_months=int(a["cycle_months"]),
            extra_every_3y_from_isa=(code in ("PENSION1", "PENSION3")),
        )
    return states


def _step_month(states: dict[str, SimAccount]) -> None:
    for s in states.values():
        # 납입
        if s.monthly_deposit > 0:
            s.principal += s.monthly_deposit
            s.market_value += s.monthly_deposit
        # 수익
        if s.kind == "SAVINGS":
            # 적금은 원금에 이자만 붙음 (평가액 = 원금 + 이자)
            interest = s.market_value * (s.interest_rate / 12)
            s.market_value += interest
        elif s.kind == "CASH":
            pass
        else:
            s.market_value *= (1 + s.cagr / 12)
        # 만기 카운트다운
        if s.months_to_maturity is not None:
            s.months_to_maturity -= 1


def _handle_maturities(states: dict[str, SimAccount],
                       savings_isa_move: int) -> list[str]:
    """월 말 이후 만기 도달 계좌 처리. 반환: 이벤트 로그."""
    from .accounts import display_name
    events: list[str] = []
    for s in list(states.values()):
        if s.months_to_maturity is None or s.months_to_maturity > 0:
            continue
        if s.kind == "SAVINGS":
            bal = s.market_value
            move = min(savings_isa_move, bal)
            rest = bal - move
            events.append(
                f"{display_name(s.code)} 만기: 잔액 {bal:,.0f}원 → "
                f"ISA {move:,.0f}원 / 현금 {rest:,.0f}원"
            )
            # 이동
            isa = states.get("ISA")
            cash = states.get("CASH")
            if isa:
                isa.principal += move
                isa.market_value += move
            if cash:
                cash.principal += rest
                cash.market_value += rest
            # 적금 리셋 (원금 0, 1년 후 재만기)
            s.principal = 0
            s.market_value = 0
            s.months_to_maturity = s.cycle_months or 12
        elif s.kind == "ISA":
            mv = s.market_value
            to_p1 = mv * 0.10
            to_p3 = mv - to_p1
            events.append(
                f"ISA 만기: 평가 {mv:,.0f}원 → "
                f"연금저축1 {to_p1:,.0f}원 / 연금저축3 {to_p3:,.0f}원"
            )
            p1 = states.get("PENSION1")
            p3 = states.get("PENSION3")
            if p1:
                p1.principal += to_p1
                p1.market_value += to_p1
            if p3:
                p3.principal += to_p3
                p3.market_value += to_p3
            s.principal = 0
            s.market_value = 0
            s.months_to_maturity = s.cycle_months or 36
    return events


def _snapshot(states: dict[str, SimAccount], year: int, on_date: date) -> dict:
    per_acc = {}
    tp, tm = 0.0, 0.0
    for code, s in states.items():
        per_acc[code] = {
            "principal": int(round(s.principal)),
            "market_value": int(round(s.market_value)),
            "return": int(round(s.market_value - s.principal)),
        }
        tp += s.principal
        tm += s.market_value
    return {
        "year": year,
        "date": on_date.isoformat(),
        "total_principal": int(round(tp)),
        "total_market_value": int(round(tm)),
        "total_return": int(round(tm - tp)),
        "total_return_pct": ((tm - tp) / tp * 100) if tp > 0 else 0.0,
        "by_account": per_acc,
    }


def simulate(years: int,
             start_date: date,
             adjustment: float = 0.0,
             savings_isa_move: int = 10_000_000,
             monthly_override: dict[str, int] | None = None,
             scenario: str = "neutral") -> SimResult:
    cagr_table = expected_cagr_table(end_date=start_date)
    states = _init_states(start_date, adjustment, cagr_table, monthly_override)

    result = SimResult(scenario=scenario, annual_adjustment=adjustment)
    cur = start_date
    for m in range(1, years * 12 + 1):
        # 다음 달 말로 이동
        y = cur.year + (cur.month // 12)
        mo = cur.month % 12 + 1
        # 해당 달의 말일
        import calendar as _cal
        cur = date(y, mo, _cal.monthrange(y, mo)[1])

        _step_month(states)
        events = _handle_maturities(states, savings_isa_move)

        if m % 12 == 0:
            snap = _snapshot(states, year=m // 12, on_date=cur)
            snap["events"] = events
            result.yearly.append(snap)

    result.by_account_final = {c: {
        "principal": int(round(s.principal)),
        "market_value": int(round(s.market_value)),
        "cagr": s.cagr,
    } for c, s in states.items()}
    return result


def simulate_scenarios(years: int, start_date: date,
                       savings_isa_move: int = 10_000_000,
                       spread: float = 0.02,
                       monthly_override: dict[str, int] | None = None) -> dict[str, SimResult]:
    return {
        "비관": simulate(years, start_date, adjustment=-spread,
                         savings_isa_move=savings_isa_move,
                         monthly_override=monthly_override, scenario="pessimistic"),
        "중립": simulate(years, start_date, adjustment=0.0,
                         savings_isa_move=savings_isa_move,
                         monthly_override=monthly_override, scenario="neutral"),
        "낙관": simulate(years, start_date, adjustment=spread,
                         savings_isa_move=savings_isa_move,
                         monthly_override=monthly_override, scenario="optimistic"),
    }


def print_projection(years: int, start_date: date, spread: float = 0.02,
                     savings_isa_move: int = 10_000_000,
                     milestones: tuple[int, ...] = (1, 3, 5, 10, 20, 30),
                     monthly_override: dict[str, int] | None = None) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    results = simulate_scenarios(years, start_date, savings_isa_move=savings_isa_move,
                                 spread=spread, monthly_override=monthly_override)

    # 연도별 요약
    t = Table(title=f"향후 {years}년 미래가치 시뮬레이션 (기대수익률 ±{spread*100:.0f}%p)")
    t.add_column("연차")
    t.add_column("비관 평가", justify="right")
    t.add_column("중립 평가", justify="right")
    t.add_column("낙관 평가", justify="right")
    t.add_column("중립 원금", justify="right")
    t.add_column("중립 수익", justify="right")
    t.add_column("중립 수익률", justify="right")

    neu = results["중립"].yearly
    pes = results["비관"].yearly
    opt = results["낙관"].yearly

    for i, y in enumerate(neu):
        yr = y["year"]
        show = (yr in milestones) or (i == len(neu) - 1) or (yr <= 5)
        if not show:
            continue
        t.add_row(
            f"{yr}년차",
            f"{pes[i]['total_market_value']:,}원",
            f"{y['total_market_value']:,}원",
            f"{opt[i]['total_market_value']:,}원",
            f"{y['total_principal']:,}원",
            f"{y['total_return']:+,}원",
            f"{y['total_return_pct']:+.1f}%",
        )
    console.print(t)

    # 최종 계좌별
    from .accounts import display_name
    t2 = Table(title=f"{years}년 후 계좌별 (중립 시나리오)")
    t2.add_column("계좌"); t2.add_column("기대수익률(연)", justify="right")
    t2.add_column("원금", justify="right"); t2.add_column("평가액", justify="right")
    for code, r in results["중립"].by_account_final.items():
        t2.add_row(display_name(code), f"{r['cagr']*100:.2f}%",
                   f"{r['principal']:,}원", f"{r['market_value']:,}원")
    console.print(t2)

    # 만기 이벤트 로그 (중립 기준)
    events = []
    for y in neu:
        for e in y.get("events", []):
            events.append(f"[{y['date']}] {e}")
    if events:
        console.print("[bold]이벤트 로그 (중립):[/bold]")
        for e in events[:40]:
            console.print(f"  {e}")
        if len(events) > 40:
            console.print(f"  ... (생략 {len(events) - 40}건)")

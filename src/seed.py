"""Initial state as of 2026-04-22 and static account/allocation definitions."""
from __future__ import annotations

from datetime import date

from .db import get_conn, init_db, reset_db

SEED_DATE = "2026-04-22"

# (code, name, kind, monthly_deposit, matures_at, cycle_months, interest_rate, tax_deductible)
ACCOUNTS = [
    ("SAVINGS1", "적금1",                   "SAVINGS",   500_000, "2027-03-02", 12, 0.049, 0),
    ("SAVINGS2", "적금2",                   "SAVINGS",   500_000, "2027-03-02", 12, 0.049, 0),
    ("ISA",      "ISA",                     "ISA",       500_000, "2027-05-31", 36, 0.000, 0),
    ("PENSION1", "연금저축1(세액공제)",      "PENSION",   500_000, None,          0, 0.000, 1),
    ("PENSION2", "연금저축2(공제X)",         "PENSION",   300_000, None,          0, 0.000, 0),
    ("PENSION3", "연금저축3(공제X-ISA연계)", "PENSION",         0, None,          0, 0.000, 0),
    ("IRP",      "IRP(세액공제)",            "IRP",       250_000, None,          0, 0.000, 1),
    ("CASH",     "현금",                    "CASH",            0, None,          0, 0.000, 0),
]


def _p(market_value: int, gain_pct: float) -> tuple[int, int]:
    """(평가액, 상승률) → (원금, 평가액)."""
    principal = int(round(market_value / (1 + gain_pct)))
    return principal, market_value


# 계좌별 초기 보유 종목 (SEED_DATE 시점 실제 포트폴리오).
# 형식: (ticker, name, market_value, gain). cost_basis = market_value - gain.
# 여기 등록된 계좌는 target_allocations 비중도 자동으로 mv 비례로 계산됨.
INITIAL_HOLDINGS: dict[str, list[tuple[str, str, int, int]]] = {
    "PENSION1": [
        ("379800", "KODEX 미국S&P500",              859_680,   36_790),
        ("489250", "KODEX 미국배당다우존스",          830_280,   72_170),
        ("0144L0", "KODEX 미국성장커버드콜",          240_240,   16_390),
        ("379810", "KODEX 미국나스닥100",            237_330,   20_660),
        ("0052D0", "TIGER 코리아배당다우존스",        213_135,   39_687),
        ("484790", "KODEX 미국30년국채액티브",        130_650,   -2_865),
        ("465580", "TIGER 차이나과창판STAR50",         63_100,    3_610),
        ("371160", "TIGER 차이나항셍테크",             47_520,       30),
        ("0000H0", "KODEX 인도NIFTY미드캡100",         43_480,      860),
    ],
    "ISA": [
        ("379810", "KODEX 미국나스닥100",          1_845_200,  133_820),
        ("379800", "KODEX 미국S&P500",            1_790_625,  126_625),
        ("484790", "KODEX 미국30년국채액티브",       818_270,  -19_965),
    ],
    "PENSION2": [
        ("0144L0", "KODEX 미국성장커버드콜",           80_040,    5_900),
        ("379800", "KODEX 미국S&P500",               71_625,    4_420),
        ("365780", "ACE 미국10년국채액티브",          64_830,     -680),
        ("283580", "KODEX 차이나CSI300",              33_190,    1_230),
        ("241180", "TIGER 일본니케이225(합성H)",      32_965,    3_555),
    ],
    "IRP": [
        ("379810", "KODEX 미국나스닥100",            369_110,   31_650),
        ("438100", "ACE 미국나스닥100미국국채혼합",   169_840,    8_855),
    ],
}

# 티커 단위 명세가 없는 계좌: (principal, market_value) 직접 지정.
INITIAL_STATE: dict[str, tuple[int, int]] = {
    "SAVINGS1": _p(2_426_000, 0.000),   # 적금1 잔액
    "SAVINGS2": _p(2_426_000, 0.000),   # 적금2 잔액
}

# 계좌별 목표 비중 (미래 납입 시 이 비율대로 매수). INITIAL_HOLDINGS 와 독립적임.
# 현재 보유 구성과 다를 수 있음 — 초기값은 초기값, 앞으로의 납입은 이 비중대로.
ALLOCATIONS: dict[str, list[tuple[str, str, float]]] = {
    "ISA": [
        ("379800", "KODEX 미국S&P500",              0.40),
        ("379810", "KODEX 미국나스닥100",            0.40),
        ("484790", "KODEX 미국30년국채액티브",       0.20),
    ],
    "PENSION1": [
        ("379800", "KODEX 미국S&P500",              0.20),
        ("379810", "KODEX 미국나스닥100",            0.20),
        ("0144L0", "KODEX 미국성장커버드콜",         0.20),
        ("489250", "KODEX 미국배당다우존스",         0.10),
        ("484790", "KODEX 미국30년국채액티브",       0.10),
        ("0000H0", "KODEX 인도NIFTY미드캡100",       0.10),
        ("465580", "TIGER 차이나과창판STAR50",       0.05),
        ("371160", "TIGER 차이나항셍테크",           0.05),
    ],
    "IRP": [
        ("379810", "KODEX 미국나스닥100",            0.70),
        ("438100", "ACE 미국나스닥100미국국채혼합",   0.30),
    ],
    "PENSION2": [
        ("0144L0", "KODEX 미국성장커버드콜",         0.25),
        ("379800", "KODEX 미국S&P500",              0.25),
        ("365780", "ACE 미국10년국채액티브",         0.20),
        ("283580", "KODEX 차이나CSI300",            0.10),
        ("0000H0", "KODEX 인도NIFTY미드캡100",       0.10),
        ("241180", "TIGER 일본니케이225(합성H)",     0.10),
    ],
    "PENSION3": [
        ("251350", "KODEX 선진국MSCI World",        0.15),
        ("472720", "TIGER 토탈월드스탁액티브",        0.15),
        ("329650", "KODEX TRF3070",                0.20),
        ("473980", "KODEX 미국머니마켓액티브",        0.30),
        ("132030", "KODEX 골드선물(H)",             0.05),
        ("144600", "KODEX 은선물(H)",               0.05),
        ("160580", "TIGER 구리실물",                0.05),
        ("261220", "KODEX WTI원유선물(H)",          0.05),
    ],
}

# 내부 가상 종목 (적금/현금) - 가격은 항상 1원
INTERNAL_TICKERS = {
    "__SAVINGS__": "적금 잔액",
    "__CASH__":    "현금 잔액",
}

# 상장 이전 구간 대체 지수 (yfinance 심볼)
PROXY_TICKERS = {
    # 기존 티커
    "379800": "^GSPC",
    "379810": "^NDX",
    "365780": "IEF",
    "465580": "000688.SS",
    "371160": "^HSTECH",
    "283580": "ASHR",
    "241180": "^N225",
    "251350": "URTH",
    "472720": "VT",
    "329650": "AOR",
    "473980": "SHV",
    "132030": "GC=F",
    "144600": "SI=F",
    "160580": "HG=F",
    "261220": "CL=F",
    # 신규 추가 티커
    "0000H0": "^NSEI",   # KODEX 인도NIFTY미드캡100 → NIFTY50 근사
    "0052D0": "^KS11",   # TIGER 코리아배당다우존스 → KOSPI 근사
    "0144L0": "QYLD",    # KODEX 미국성장커버드콜 → QYLD 근사
    "484790": "TLT",     # KODEX 미국30년국채액티브
    "489250": "SCHD",    # KODEX 미국배당다우존스
    "438100": "^NDX",    # ACE 미국나스닥100미국국채혼합 근사
    # 더 이상 사용되지 않지만 호환성 위해 유지
    "486450": "^NDX",
    "453850": "TLT",
    "458730": "SCHD",
    "453870": "^NSEI",
    "468620": "^NDX",
}


def all_tickers() -> list[str]:
    """ALLOCATIONS(목표)와 INITIAL_HOLDINGS(현재 보유) 양쪽 티커를 합친 유니크 리스트."""
    s: set[str] = set()
    for rows in ALLOCATIONS.values():
        for t, _, _ in rows:
            s.add(t)
    for rows in INITIAL_HOLDINGS.values():
        for t, *_ in rows:
            s.add(t)
    return sorted(s)


def seed(reset: bool = False) -> None:
    if reset:
        reset_db()
    else:
        init_db()

    with get_conn() as conn:
        # 계좌
        for code, name, kind, monthly, mat, cyc, rate, td in ACCOUNTS:
            conn.execute(
                """INSERT OR IGNORE INTO accounts
                   (code, name, kind, monthly_deposit, opened_at, matures_at,
                    interest_rate, cycle_months, tax_deductible, active, generation)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)""",
                (code, name, kind, monthly, SEED_DATE, mat, rate, cyc, td),
            )

        # 종목 비중
        for acc_code, rows in ALLOCATIONS.items():
            acc_id = _account_id(conn, acc_code)
            for ticker, name, ratio in rows:
                conn.execute(
                    """INSERT OR REPLACE INTO target_allocations
                       (account_id, ticker, name, target_ratio) VALUES (?, ?, ?, ?)""",
                    (acc_id, ticker, name, ratio),
                )

        # 대체 지수
        for t, p in PROXY_TICKERS.items():
            conn.execute(
                "INSERT OR REPLACE INTO proxy_tickers(ticker, proxy_ticker) VALUES (?, ?)",
                (t, p),
            )

        # meta
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('seed_date', ?)",
            (SEED_DATE,),
        )


def _account_id(conn, code: str) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown account code: {code}")
    return row["id"]


def _seed_txns(conn, acc_id: int, principal: int, gain: int) -> None:
    """SEED 트랜잭션 + OPENING_GAIN 기록."""
    conn.execute(
        """INSERT INTO transactions
           (date, type, to_account_id, amount, note)
           VALUES (?, 'SEED', ?, ?, '초기 원금')""",
        (SEED_DATE, acc_id, principal),
    )
    if gain > 0:
        conn.execute(
            """INSERT INTO transactions
               (date, type, to_account_id, amount, note)
               VALUES (?, 'OPENING_GAIN', ?, ?, '초기 평가이익')""",
            (SEED_DATE, acc_id, gain),
        )


def apply_opening_balances(prices: dict[str, float]) -> list[str]:
    """각 계좌의 오픈 잔액 → 보유 좌수 환산. prices 미제공 티커는 skip.

    - INITIAL_HOLDINGS 에 명세된 계좌: 티커별로 (mv, gain) 정확히 반영
    - INITIAL_STATE 에 명세된 계좌(적금): (principal, mv) 기준
    - 나머지(PENSION3, CASH): 잔액 0으로 초기화

    Returns: 스킵된 티커 목록 (가격 누락)"""
    skipped: list[str] = []

    with get_conn() as conn:
        # ---- 1) INITIAL_HOLDINGS: 티커별 정확한 매입 내역 ----
        for acc_code, rows in INITIAL_HOLDINGS.items():
            acc_id = _account_id(conn, acc_code)
            exists = conn.execute(
                "SELECT 1 FROM transactions WHERE to_account_id = ? AND type = 'SEED'",
                (acc_id,),
            ).fetchone()
            if exists:
                continue

            total_mv = sum(mv for _, _, mv, _ in rows)
            total_gain = sum(g for _, _, _, g in rows)
            principal = total_mv - total_gain
            _seed_txns(conn, acc_id, principal, total_gain)

            for ticker, _name, mv, gain in rows:
                cost = mv - gain
                price = prices.get(ticker)
                if price is None or price <= 0:
                    skipped.append(f"{acc_code}:{ticker}")
                    continue
                shares = mv / price
                conn.execute(
                    """INSERT OR REPLACE INTO holdings
                       (account_id, ticker, shares, cost_basis)
                       VALUES (?, ?, ?, ?)""",
                    (acc_id, ticker, shares, int(round(cost))),
                )

        # ---- 2) INITIAL_STATE: 적금 계좌 ----
        for acc_code, (principal, market_value) in INITIAL_STATE.items():
            acc_id = _account_id(conn, acc_code)
            kind = conn.execute(
                "SELECT kind FROM accounts WHERE id = ?", (acc_id,)
            ).fetchone()["kind"]

            exists = conn.execute(
                "SELECT 1 FROM transactions WHERE to_account_id = ? AND type = 'SEED'",
                (acc_id,),
            ).fetchone()
            if exists:
                continue

            gain = market_value - principal
            _seed_txns(conn, acc_id, principal, gain)

            if kind in ("CASH", "SAVINGS"):
                virt = "__SAVINGS__" if kind == "SAVINGS" else "__CASH__"
                conn.execute(
                    """INSERT OR REPLACE INTO holdings
                       (account_id, ticker, shares, cost_basis)
                       VALUES (?, ?, ?, ?)""",
                    (acc_id, virt, market_value, principal),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO prices(ticker, date, close, source) VALUES (?, ?, 1, 'internal')",
                    (virt, SEED_DATE),
                )

        # ---- 3) CASH 계좌 빈 holding 보장 ----
        cash_id = _account_id(conn, "CASH")
        exists = conn.execute(
            "SELECT 1 FROM holdings WHERE account_id = ? AND ticker = '__CASH__'",
            (cash_id,),
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT OR REPLACE INTO holdings
                   (account_id, ticker, shares, cost_basis) VALUES (?, '__CASH__', 0, 0)""",
                (cash_id,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO prices(ticker, date, close, source) VALUES ('__CASH__', ?, 1, 'internal')",
                (SEED_DATE,),
            )

    return skipped

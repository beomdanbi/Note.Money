import contextlib
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "portfolio.db"

SCHEMA = """

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,              -- SAVINGS / ISA / PENSION / IRP / CASH
    monthly_deposit INTEGER DEFAULT 0,
    opened_at DATE,
    matures_at DATE,
    interest_rate REAL DEFAULT 0,
    cycle_months INTEGER DEFAULT 0,  -- 0 = 만기 없음
    tax_deductible INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    generation INTEGER DEFAULT 1     -- 만기 후 재생성시 증가
);

CREATE TABLE IF NOT EXISTS target_allocations (
    account_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    target_ratio REAL NOT NULL,
    PRIMARY KEY (account_id, ticker),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS holdings (
    account_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    shares REAL NOT NULL DEFAULT 0,
    cost_basis INTEGER NOT NULL DEFAULT 0,   -- 누적 매입 원가(원)
    PRIMARY KEY (account_id, ticker),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    close REAL NOT NULL,
    source TEXT,                     -- pykrx / yfinance / proxy / manual
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    type TEXT NOT NULL,              -- DEPOSIT / WITHDRAW / TRANSFER / MATURITY / INTEREST / BUY / SELL
    from_account_id INTEGER,
    to_account_id INTEGER,
    ticker TEXT,
    amount INTEGER NOT NULL DEFAULT 0,
    shares REAL DEFAULT 0,
    price REAL DEFAULT 0,
    note TEXT,
    FOREIGN KEY (from_account_id) REFERENCES accounts(id),
    FOREIGN KEY (to_account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS monthly_snapshots (
    account_id INTEGER NOT NULL,
    date DATE NOT NULL,
    principal INTEGER NOT NULL,      -- 누적 원금(납입액 - 출금액)
    market_value INTEGER NOT NULL,
    return_amount INTEGER NOT NULL,
    return_pct REAL NOT NULL,
    PRIMARY KEY (account_id, date),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS proxy_tickers (
    ticker TEXT PRIMARY KEY,         -- 상장 전 구간 대체 지수 매핑
    proxy_ticker TEXT NOT NULL,
    listed_at DATE
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextlib.contextmanager
def get_conn():
    """sqlite 연결 + 자동 commit/rollback + close.
    Windows에서 파일 핸들이 누적되어 DB 파일 조작이 막히는 문제 방지."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def reset_db() -> None:
    """전 테이블 DROP 후 스키마 재생성. DB 파일 unlink 방식이 아니라서
    다른 프로세스/연결이 파일 핸들을 잡고 있어도 동작."""
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for r in rows:
            conn.execute(f'DROP TABLE IF EXISTS "{r[0]}"')
    init_db()

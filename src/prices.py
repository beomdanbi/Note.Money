"""월말 종가 조회 및 캐시. pykrx(KR) + yfinance(해외/proxy) 사용."""
from __future__ import annotations

import calendar
import contextlib
import io
from datetime import date, datetime, timedelta
from typing import Iterable

from .db import get_conn


@contextlib.contextmanager
def _silence_pykrx():
    """pykrx가 stdout/stderr로 직접 print하는 에러 메시지를 억제."""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        yield


_ETF_SET: set[str] | None = None


def month_end(d: date) -> date:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)


def as_date(v) -> date:
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v), "%Y-%m-%d").date()


def get_cached_price(ticker: str, d: date) -> float | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT close FROM prices WHERE ticker = ? AND date = ?",
            (ticker, d.isoformat()),
        ).fetchone()
        return float(row["close"]) if row else None


def latest_cached_price(ticker: str, on_or_before: date) -> float | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT close FROM prices WHERE ticker = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            (ticker, on_or_before.isoformat()),
        ).fetchone()
        return float(row["close"]) if row else None


def cache_price(ticker: str, d: date, close: float, source: str = "pykrx") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO prices(ticker, date, close, source) VALUES (?, ?, ?, ?)",
            (ticker, d.isoformat(), float(close), source),
        )


def _proxy_ticker(ticker: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT proxy_ticker FROM proxy_tickers WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row["proxy_ticker"] if row else None


def _is_etf_ticker(ticker: str) -> bool | None:
    """ETF 여부를 pykrx로 확인 (리스트 캐시). 실패시 None."""
    global _ETF_SET
    if _ETF_SET is None:
        try:
            from pykrx import stock
            with _silence_pykrx():
                tickers = stock.get_etf_ticker_list()
            _ETF_SET = set(tickers) if tickers else set()
        except Exception:
            return None
    return ticker in _ETF_SET


def fetch_krx_close(ticker: str, d: date) -> float | None:
    """pykrx로 해당 일자 또는 그 이전 최근 영업일 종가 조회."""
    try:
        from pykrx import stock
    except ImportError:
        return None
    is_etf = _is_etf_ticker(ticker)  # True/False/None(알수없음)
    # 역방향 10일 탐색
    cur = d
    for _ in range(10):
        s = cur.strftime("%Y%m%d")
        # ETF면 ETF API만, 주식이면 stock API만, 불확실하면 stock 먼저 시도
        if is_etf is True:
            try:
                with _silence_pykrx():
                    df = stock.get_etf_ohlcv_by_date(s, s, ticker)
                if df is not None and not df.empty:
                    return float(df["종가"].iloc[-1])
            except Exception:
                pass
        else:
            try:
                with _silence_pykrx():
                    df = stock.get_market_ohlcv_by_date(s, s, ticker)
                if df is not None and not df.empty:
                    return float(df["종가"].iloc[-1])
            except Exception:
                pass
            # 주식 조회 실패 + ETF 여부 불확실일 때만 ETF로 폴백
            if is_etf is None:
                try:
                    with _silence_pykrx():
                        df = stock.get_etf_ohlcv_by_date(s, s, ticker)
                    if df is not None and not df.empty:
                        return float(df["종가"].iloc[-1])
                except Exception:
                    pass
        cur = cur - timedelta(days=1)
    return None


def fetch_yf_close(ticker: str, d: date) -> float | None:
    try:
        import math

        import yfinance as yf
    except ImportError:
        return None
    start = (d - timedelta(days=10)).isoformat()
    end = (d + timedelta(days=1)).isoformat()
    try:
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
        if hist is None or hist.empty:
            return None
        px = float(hist["Close"].iloc[-1])
        if math.isnan(px) or px <= 0:
            return None
        return px
    except Exception:
        return None


def resolve_price(ticker: str, d: date, use_proxy: bool = True) -> tuple[float | None, str]:
    """티커 + 일자에 대한 종가. source 문자열 포함."""
    cached = get_cached_price(ticker, d)
    if cached is not None:
        return cached, "cache"

    # 내부 가상 티커
    if ticker in ("__CASH__", "__SAVINGS__"):
        return 1.0, "internal"

    # KR 티커(6자리, 숫자 또는 알파벳 혼용) 우선 pykrx
    if len(ticker) == 6 and not ticker.startswith("__"):
        px = fetch_krx_close(ticker, d)
        if px is not None:
            cache_price(ticker, d, px, "pykrx")
            return px, "pykrx"

    # yfinance 직접 (해외 심볼이거나 pykrx 실패 시)
    px = fetch_yf_close(ticker, d)
    if px is not None:
        cache_price(ticker, d, px, "yfinance")
        return px, "yfinance"

    # proxy 폴백
    if use_proxy:
        proxy = _proxy_ticker(ticker)
        if proxy:
            px = fetch_yf_close(proxy, d) or (
                fetch_krx_close(proxy, d) if proxy.isdigit() else None
            )
            if px is not None:
                cache_price(ticker, d, px, f"proxy:{proxy}")
                return px, f"proxy:{proxy}"

    # 최후: 이전 캐시값
    px = latest_cached_price(ticker, d)
    if px is not None:
        return px, "stale-cache"
    return None, "missing"


def fetch_prices_for_date(tickers: Iterable[str], d: date, use_proxy: bool = True) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in tickers:
        px, _ = resolve_price(t, d, use_proxy=use_proxy)
        if px is not None:
            out[t] = px
    return out


def set_manual_price(ticker: str, d: date, close: float) -> None:
    cache_price(ticker, d, close, "manual")


def fetch_monthly_series(ticker: str, start: date, end: date) -> dict[date, float]:
    """월말 종가 시계열 (상장 이후 캐시)."""
    out: dict[date, float] = {}
    cur = month_end(start)
    while cur <= end:
        px, _ = resolve_price(ticker, cur)
        if px is not None:
            out[cur] = px
        # 다음 달 말일
        if cur.month == 12:
            nxt = date(cur.year + 1, 1, 1)
        else:
            nxt = date(cur.year, cur.month + 1, 1)
        cur = month_end(nxt)
    return out

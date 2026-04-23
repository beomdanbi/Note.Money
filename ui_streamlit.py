"""Streamlit 기반 Note.Money 웹 UI. 실행: `streamlit run ui_streamlit.py`."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from src.accounts import (
    accrue_savings_interest, deposit, display_name, get_account,
    list_accounts, transfer, withdraw,
)
from src.db import get_conn, init_db
from src.maturity import due_maturities, mature_isa, mature_savings
from src.prices import (
    fetch_prices_for_date, month_end, resolve_price, set_manual_price,
)
from src.projection import simulate_scenarios
from src.seed import SEED_DATE, all_tickers, apply_opening_balances, seed
from src.valuation import (
    account_market_value, portfolio_summary, save_monthly_snapshot,
)


st.set_page_config(page_title="Note.Money", layout="wide")
init_db()

st.markdown(
    """
    <style>
    /* 사이드바 전체 패딩 */
    section[data-testid="stSidebar"] > div:first-child {
        padding-top: 1rem;
    }
    /* 앱 타이틀 */
    section[data-testid="stSidebar"] h1 {
        font-size: 1.4rem;
        margin-bottom: 0.25rem;
    }
    /* 라디오 그룹 항목 간격 */
    section[data-testid="stSidebar"] div[role="radiogroup"] {
        gap: 0.35rem;
    }
    /* 개별 메뉴 항목: 패딩·둥근 모서리·hover 배경 */
    section[data-testid="stSidebar"] div[role="radiogroup"] > label {
        padding: 0.55rem 0.75rem;
        border-radius: 0.5rem;
        transition: background-color 0.15s ease;
        cursor: pointer;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
        background-color: rgba(128, 128, 128, 0.12);
    }
    /* 선택된 항목 강조 */
    section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {
        background-color: rgba(100, 149, 237, 0.18);
        font-weight: 600;
    }
    /* 메뉴 글자 크기 살짝 키움 */
    section[data-testid="stSidebar"] div[role="radiogroup"] label p {
        font-size: 0.95rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _fmt_won(n) -> str:
    return f"{int(n):,}원"


def _fmt_pct(p) -> str:
    return f"{p:+.2f}%"


def _won_col(series: pd.Series) -> pd.Series:
    """DataFrame의 금액 컬럼을 '10,000원' 문자열로 변환."""
    def f(v):
        if pd.isna(v):
            return "-"
        try:
            return f"{int(v):,}원"
        except (TypeError, ValueError):
            return "-"
    return series.map(f)


def _money_input(label: str, default: int, *, min_value: int = 0,
                 max_value: int | None = None, key: str | None = None) -> int:
    """콤마 포맷 금액 입력. '500,000' 형태로 표시하고 int로 반환.
    입력 중 쉼표/공백/'원' 등은 자동 제거."""
    raw = st.text_input(label, value=f"{int(default):,}", key=key)
    cleaned = (raw or "").replace(",", "").replace("원", "").replace(" ", "").strip()
    try:
        v = int(cleaned) if cleaned else 0
    except ValueError:
        st.warning(f"숫자만 입력 가능합니다: {raw!r}")
        v = int(default)
    if v < min_value:
        v = min_value
    if max_value is not None and v > max_value:
        v = max_value
    return v


with st.sidebar:
    st.title("Note.Money")
    st.caption(f"오늘 {date.today()}")
    page = st.radio(
        "메뉴",
        ["대시보드", "월 납입", "보유 종목", "거래 내역", "미래가치 시뮬", "설정"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption(f"시드일: {SEED_DATE}")


# -------------------------------------------------------------------
# 대시보드
# -------------------------------------------------------------------
def page_dashboard():
    st.subheader("포트폴리오 대시보드")
    on_date = st.date_input("기준일", value=date.today())

    s = portfolio_summary(on_date)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 원금", _fmt_won(s["total_principal"]))
    c2.metric("총 평가액", _fmt_won(s["total_market_value"]))
    c3.metric("누적 수익", _fmt_won(s["total_return"]))
    c4.metric("수익률", _fmt_pct(s["total_return_pct"]))

    st.markdown("#### 계좌별")
    df = pd.DataFrame(s["accounts"])
    df = df[(df["market_value"] > 0) | (df["principal"] > 0)]
    if df.empty:
        st.info("데이터 없음. '설정 > 초기화'에서 시드 실행하세요.")
        return
    df["name"] = df["code"].map(display_name)
    disp = df[["name", "kind", "principal", "market_value",
               "return_amount", "return_pct", "matures_at"]].copy()
    for c in ("principal", "market_value", "return_amount"):
        disp[c] = _won_col(disp[c])

    st.dataframe(
        disp,
        column_config={
            "name": "계좌",
            "kind": "종류",
            "principal": "원금",
            "market_value": "평가액",
            "return_amount": "수익금",
            "return_pct": st.column_config.NumberColumn("수익률", format="%.2f%%"),
            "matures_at": "만기",
        },
        hide_index=True, use_container_width=True,
    )

    st.markdown("#### 계좌 구성 (평가액)")
    chart_df = df.set_index("name")["market_value"]
    st.bar_chart(chart_df)

    st.markdown("#### 월별 추이")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT date, SUM(principal) principal, SUM(market_value) market_value
               FROM monthly_snapshots GROUP BY date ORDER BY date"""
        ).fetchall()
    if rows:
        tdf = pd.DataFrame([dict(r) for r in rows])
        tdf["date"] = pd.to_datetime(tdf["date"])
        tdf = tdf.set_index("date")
        tdf.columns = ["원금", "평가액"]
        st.line_chart(tdf)
    else:
        st.caption("월별 스냅샷이 아직 없습니다. '월 납입' 메뉴에서 월말 처리 시 누적됩니다.")


# -------------------------------------------------------------------
# 월 납입
# -------------------------------------------------------------------
def page_monthly():
    st.subheader("월말 납입")

    on_date = st.date_input("월말 일자", value=month_end(date.today()))
    on_date = month_end(on_date)

    mats = due_maturities(on_date)
    maturity_state: dict[str, int] = {}
    if mats:
        st.warning(f"만기 도래 계좌 {len(mats)}건")
        for code, mdate in mats:
            with st.expander(f"{display_name(code)} 만기 ({mdate}) 처리", expanded=True):
                with get_conn() as conn:
                    acc = get_account(conn, code)
                if acc["kind"] == "SAVINGS":
                    with get_conn() as conn:
                        row = conn.execute(
                            "SELECT shares FROM holdings WHERE account_id=? AND ticker='__SAVINGS__'",
                            (acc["id"],),
                        ).fetchone()
                    bal = int(round(float(row["shares"]))) if row else 0
                    st.info(f"{display_name(code)} 잔액: {bal:,}원")
                    move = _money_input(
                        "ISA로 이동할 금액",
                        default=min(10_000_000, bal),
                        min_value=0, max_value=bal,
                        key=f"savings_move_{code}_{mdate}",
                    )
                    maturity_state[code] = move
                elif acc["kind"] == "ISA":
                    mv = account_market_value("ISA", mdate)
                    st.info(
                        f"ISA 평가액: {mv:,}원 → 연금저축1 10% ({int(mv*0.1):,}원) / "
                        f"연금저축3 90% ({int(mv*0.9):,}원)"
                    )
                    maturity_state["ISA"] = 1

    st.markdown("#### 월 납입액")
    accs = list_accounts()
    deposit_inputs: dict[str, int] = {}
    cols = st.columns(2)
    idx = 0
    for a in accs:
        if a["kind"] == "CASH":
            continue
        default = int(a["monthly_deposit"])
        if default <= 0:
            continue
        with cols[idx % 2]:
            amt = _money_input(
                display_name(a["code"]),
                default=default, min_value=0,
                key=f"deposit_{a['code']}",
            )
            deposit_inputs[a["code"]] = amt
        idx += 1

    if st.button("월말 일괄 처리", type="primary"):
        for code, mdate in mats:
            with get_conn() as conn:
                acc = get_account(conn, code)
            if acc["kind"] == "SAVINGS":
                r = mature_savings(maturity_state.get(code, 0), mdate, code=code)
                st.success(
                    f"{display_name(code)} 만기: ISA {r['to_isa']:,}원 / "
                    f"현금 {r['to_cash']:,}원, "
                    f"신규 {display_name(code)} 만기 {r['new_maturity']}"
                )
            elif acc["kind"] == "ISA":
                r = mature_isa(mdate)
                st.success(
                    f"ISA 만기: 연금저축1 {r['to_pension1']:,}원 / "
                    f"연금저축3 {r['to_pension3']:,}원, 신규 ISA 만기 {r['new_maturity']}"
                )

        for code, amt in deposit_inputs.items():
            if amt > 0:
                deposit(code, amt, on_date, note="월 정기 납입")
        interest = accrue_savings_interest(on_date)
        if interest > 0:
            st.info(f"적금 이자 {interest:,}원 반영")
        save_monthly_snapshot(on_date)
        st.success(f"{on_date} 월말 처리 완료")
        st.rerun()

    st.divider()
    st.markdown("#### 임시 조정")
    codes = [a["code"] for a in accs]

    with st.expander("추가 납입"):
        c1, c2 = st.columns(2)
        with c1:
            code_x = st.selectbox("계좌", codes, format_func=display_name, key="x_dep_code")
            amt_x = _money_input("금액", default=0, min_value=0, key="x_dep_amt")
        with c2:
            date_x = st.date_input("일자", value=date.today(), key="x_dep_date")
            note_x = st.text_input("메모", value="추가 납입", key="x_dep_note")
        if st.button("납입 실행", key="x_dep_btn") and amt_x > 0:
            deposit(code_x, amt_x, date_x, note=note_x)
            st.success("완료")
            st.rerun()

    with st.expander("출금"):
        c1, c2 = st.columns(2)
        with c1:
            code_w = st.selectbox("계좌", codes, format_func=display_name, key="x_wd_code")
            amt_w = _money_input("금액", default=0, min_value=0, key="x_wd_amt")
        with c2:
            date_w = st.date_input("일자", value=date.today(), key="x_wd_date")
            note_w = st.text_input("메모", value="출금", key="x_wd_note")
        if st.button("출금 실행", key="x_wd_btn") and amt_w > 0:
            withdraw(code_w, amt_w, date_w, note=note_w)
            st.success("완료")
            st.rerun()

    with st.expander("계좌간 이동"):
        c1, c2, c3 = st.columns(3)
        with c1:
            code_f = st.selectbox("From", codes, format_func=display_name, key="x_tr_from")
        with c2:
            code_t = st.selectbox("To", codes, format_func=display_name, key="x_tr_to")
        with c3:
            amt_t = _money_input("금액", default=0, min_value=0, key="x_tr_amt")
        date_t = st.date_input("일자", value=date.today(), key="x_tr_date")
        note_t = st.text_input("메모", value="이동", key="x_tr_note")
        if st.button("이동 실행", key="x_tr_btn") and amt_t > 0 and code_f != code_t:
            transfer(code_f, code_t, amt_t, date_t, note=note_t)
            st.success("완료")
            st.rerun()


# -------------------------------------------------------------------
# 보유 종목
# -------------------------------------------------------------------
def page_holdings():
    st.subheader("보유 종목")
    on_date = st.date_input("기준일", value=date.today(), key="hold_date")
    accs = list_accounts()
    if not accs:
        st.info("계좌 없음")
        return

    # 계좌별 평가액 집계 + 상세 행 준비
    acc_totals: list[dict] = []
    acc_rows: dict[str, list[dict]] = {}
    for a in accs:
        code = a["code"]
        with get_conn() as conn:
            holdings = conn.execute(
                """SELECT h.ticker, h.shares, h.cost_basis,
                          ta.name AS alloc_name, ta.target_ratio
                   FROM holdings h
                   LEFT JOIN target_allocations ta
                     ON ta.account_id = h.account_id AND ta.ticker = h.ticker
                   WHERE h.account_id = ?""",
                (a["id"],),
            ).fetchall()

        rows = []
        for h in holdings:
            shares = float(h["shares"])
            cb = int(h["cost_basis"])
            if shares <= 0 and cb <= 0:
                continue
            ticker = h["ticker"]
            if ticker in ("__CASH__", "__SAVINGS__"):
                px = 1.0
            else:
                px, _ = resolve_price(ticker, on_date)
                px = px or 0.0
            mv = int(round(shares * px))
            avg = (cb / shares) if shares > 0 else 0.0
            ret_pct = ((mv - cb) / cb * 100) if cb > 0 else 0.0
            rows.append({
                "티커": ticker,
                "종목명": h["alloc_name"] or ticker,
                "목표비중": f"{(h['target_ratio'] or 0)*100:.1f}%" if h["target_ratio"] else "-",
                "좌수": round(shares, 4),
                "평단가": int(round(avg)),
                "현재가": int(round(px)),
                "원가": cb,
                "평가액": mv,
                "수익률": ret_pct,
            })
        if rows:
            acc_rows[code] = rows
            acc_totals.append({
                "계좌": display_name(code),
                "평가액": sum(r["평가액"] for r in rows),
            })

    if not acc_totals:
        st.info("보유 종목 없음")
        return

    # 전체 자산 대비 계좌 비중
    st.markdown("#### 전체 자산 대비 계좌 비중")
    total_mv = sum(x["평가액"] for x in acc_totals)
    chart_df = pd.DataFrame(acc_totals).set_index("계좌")["평가액"]
    st.bar_chart(chart_df)

    ratio_rows = [
        {
            "계좌": x["계좌"],
            "평가액": x["평가액"],
            "비중": (x["평가액"] / total_mv * 100) if total_mv > 0 else 0.0,
        }
        for x in sorted(acc_totals, key=lambda r: -r["평가액"])
    ]
    ratio_df = pd.DataFrame(ratio_rows)
    ratio_df["평가액"] = _won_col(ratio_df["평가액"])
    st.dataframe(
        ratio_df, hide_index=True, use_container_width=True,
        column_config={"비중": st.column_config.NumberColumn(format="%.2f%%")},
    )

    # 계좌별 보유 종목 상세 (전체 펼쳐 표시)
    for a in accs:
        code = a["code"]
        rows = acc_rows.get(code)
        if not rows:
            continue
        st.markdown(f"#### {display_name(code)}")
        df = pd.DataFrame(rows)
        for c in ("평단가", "현재가", "원가", "평가액"):
            df[c] = _won_col(df[c])
        st.dataframe(
            df, hide_index=True, use_container_width=True,
            column_config={
                "수익률": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )


# -------------------------------------------------------------------
# 거래 내역
# -------------------------------------------------------------------
def page_history():
    st.subheader("거래 내역")
    tab1, tab2 = st.tabs(["최근 거래", "월별 스냅샷"])

    with tab1:
        limit = st.slider("건수", 10, 500, 100, 10)
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT t.date, t.type, t.amount, t.ticker,
                          fa.code AS from_code, ta.code AS to_code, t.note
                   FROM transactions t
                   LEFT JOIN accounts fa ON fa.id = t.from_account_id
                   LEFT JOIN accounts ta ON ta.id = t.to_account_id
                   ORDER BY t.date DESC, t.id DESC LIMIT ?""", (limit,),
            ).fetchall()
        if rows:
            df = pd.DataFrame([dict(r) for r in rows])
            df.columns = ["일자", "유형", "금액", "종목", "From", "To", "메모"]
            df["From"] = df["From"].map(display_name)
            df["To"] = df["To"].map(display_name)
            df["금액"] = _won_col(df["금액"])
            st.dataframe(df, hide_index=True, use_container_width=True)
        else:
            st.info("거래 내역 없음")

    with tab2:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT s.date, a.code, s.principal, s.market_value,
                          s.return_amount, s.return_pct
                   FROM monthly_snapshots s JOIN accounts a ON a.id = s.account_id
                   ORDER BY s.date DESC, a.id"""
            ).fetchall()
        if not rows:
            st.info("스냅샷 없음")
            return
        df = pd.DataFrame([dict(r) for r in rows])
        df.columns = ["월말", "계좌", "원금", "평가액", "수익", "수익률"]
        df["계좌"] = df["계좌"].map(display_name)
        for c in ("원금", "평가액", "수익"):
            df[c] = _won_col(df[c])
        st.dataframe(
            df, hide_index=True, use_container_width=True,
            column_config={
                "수익률": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )


# -------------------------------------------------------------------
# 미래가치 시뮬
# -------------------------------------------------------------------
def page_projection():
    st.subheader("미래가치 시뮬레이션")
    c1, c2, c3 = st.columns(3)
    with c1:
        years = st.slider("시뮬 기간(년)", 1, 20, 2)
    with c2:
        spread_pct = st.slider("시나리오 스프레드 ±%p", 0.0, 5.0, 2.0, 0.5)
    with c3:
        savings_move = _money_input(
            "적금 만기 시 ISA 이동액", default=10_000_000,
            min_value=0, max_value=20_000_000,
            key="proj_savings_move",
        )

    start = st.date_input("시작일", value=date.today(), key="proj_start")
    spread = spread_pct / 100

    if not st.button("시뮬 실행", type="primary"):
        st.caption("실행 버튼 누르면 과거 CAGR 조회 후 시뮬 (1~2분 소요, 네트워크 필요)")
        return

    with st.spinner("과거 CAGR 조회 중..."):
        results = simulate_scenarios(
            years=years, start_date=start,
            savings_isa_move=savings_move, spread=spread,
        )

    neu = results["중립"].yearly
    pes = results["비관"].yearly
    opt = results["낙관"].yearly

    # 요약 지표
    final = neu[-1]
    final_opt = opt[-1]
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric(f"{years}년 후 원금", _fmt_won(final["total_principal"]))
    c2.metric("비관 평가액", _fmt_won(pes[-1]["total_market_value"]))
    c3.metric("중립 평가액", _fmt_won(final["total_market_value"]),
              _fmt_pct(final["total_return_pct"]))
    c4.metric("낙관 평가액", _fmt_won(final_opt["total_market_value"]),
              _fmt_pct(final_opt["total_return_pct"]))
    c5.metric("중립 수익", _fmt_won(final["total_return"]))
    c6.metric("낙관 수익", _fmt_won(final_opt["total_return"]))

    # 연도별
    st.markdown("#### 연도별 전개")
    rows = []
    for i, y in enumerate(neu):
        rows.append({
            "연차": y["year"],
            "일자": y["date"],
            "누적 원금": y["total_principal"],
            "비관 평가": pes[i]["total_market_value"],
            "중립 평가": y["total_market_value"],
            "낙관 평가": opt[i]["total_market_value"],
            "비관 수익": pes[i]["total_return"],
            "중립 수익": y["total_return"],
            "낙관 수익": opt[i]["total_return"],
            "비관 수익률": pes[i]["total_return_pct"],
            "중립 수익률": y["total_return_pct"],
            "낙관 수익률": opt[i]["total_return_pct"],
        })
    df = pd.DataFrame(rows)
    chart_df = df[["일자", "누적 원금", "비관 평가", "중립 평가", "낙관 평가"]].copy()
    chart_df["일자"] = pd.to_datetime(chart_df["일자"])
    chart_df = chart_df.set_index("일자")

    disp = df.copy()
    for c in ("누적 원금", "비관 평가", "중립 평가", "낙관 평가",
              "비관 수익", "중립 수익", "낙관 수익"):
        disp[c] = _won_col(disp[c])
    st.dataframe(
        disp, hide_index=True, use_container_width=True,
        column_config={
            "비관 수익률": st.column_config.NumberColumn(format="%.1f%%"),
            "중립 수익률": st.column_config.NumberColumn(format="%.1f%%"),
            "낙관 수익률": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

    st.markdown("#### 시나리오 비교 차트")
    st.line_chart(chart_df)

    st.markdown(f"#### {years}년 후 계좌별 (중립)")
    final_rows = []
    for code, r in results["중립"].by_account_final.items():
        final_rows.append({
            "계좌": display_name(code),
            "연 기대수익률": r["cagr"] * 100,
            "원금": r["principal"],
            "평가액": r["market_value"],
            "수익": r["market_value"] - r["principal"],
        })
    final_df = pd.DataFrame(final_rows)
    for c in ("원금", "평가액", "수익"):
        final_df[c] = _won_col(final_df[c])
    st.dataframe(
        final_df, hide_index=True, use_container_width=True,
        column_config={
            "연 기대수익률": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )

    st.markdown("#### 만기 이벤트 (중립)")
    events = []
    for y in neu:
        for e in y.get("events", []):
            events.append(f"[{y['date']}] {e}")
    if events:
        for e in events[:40]:
            st.text(e)
        if len(events) > 40:
            st.caption(f"... 생략 {len(events)-40}건")
    else:
        st.caption("없음")


# -------------------------------------------------------------------
# 설정
# -------------------------------------------------------------------
def page_settings():
    st.subheader("설정")
    tab1, tab2, tab3, tab4 = st.tabs(["시드/초기화", "수동 가격", "가격 새로고침", "목표 비중"])

    with tab1:
        st.markdown("#### 첫 시드")
        st.info(f"시드 기준일: {SEED_DATE}")
        if st.button("시드 실행 (기존 DB 유지, 누락만 채움)"):
            with st.spinner("시딩 및 초기 가격 조회 중..."):
                seed(reset=False)
                prices = fetch_prices_for_date(
                    all_tickers(), date.fromisoformat(SEED_DATE)
                )
                skipped = apply_opening_balances(prices)
            if skipped:
                st.warning(f"가격 미수신/스킵: {len(skipped)}건")
                with st.expander("상세"):
                    for s in skipped:
                        st.text(s)
            st.success("시드 완료")
            st.rerun()

        st.markdown("#### 완전 초기화 (위험)")
        with st.expander("모든 거래/스냅샷 삭제하고 처음부터"):
            confirm = st.text_input("확인 문자열로 'RESET' 입력", key="reset_confirm")
            if st.button("DB 완전 리셋 실행"):
                if confirm == "RESET":
                    with st.spinner("리셋 중..."):
                        seed(reset=True)
                        prices = fetch_prices_for_date(
                            all_tickers(), date.fromisoformat(SEED_DATE)
                        )
                        apply_opening_balances(prices)
                    st.success("리셋 완료")
                    st.rerun()
                else:
                    st.error("확인 문자열 불일치")

    with tab2:
        st.markdown("#### 수동 가격 입력")
        st.caption("자동 조회가 안 되는 티커에 대해 직접 가격을 저장.")
        ticker = st.text_input("티커", key="mprice_ticker")
        price = st.number_input("종가", min_value=0.0, step=1.0, key="mprice_px")
        d = st.date_input("일자", value=date.today(), key="mprice_date")
        if st.button("저장", key="mprice_btn") and ticker and price > 0:
            set_manual_price(ticker, d, price)
            st.success(f"{ticker} @ {d} = {price}")

    with tab3:
        st.markdown("#### 전체 티커 가격 새로고침")
        d = st.date_input("조회일", value=date.today(), key="refresh_date")
        if st.button("새로고침 실행"):
            with st.spinner("조회 중..."):
                tickers = all_tickers()
                prices = fetch_prices_for_date(tickers, d)
            st.success(f"{len(prices)}/{len(tickers)} 종목 조회 성공")
            if len(prices) < len(tickers):
                missing = sorted(set(tickers) - set(prices.keys()))
                st.warning(f"미조회 ({len(missing)}): {', '.join(missing)}")

    with tab4:
        st.markdown("#### 목표 비중 현황")
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT a.code, ta.ticker, ta.name AS etf_name, ta.target_ratio
                   FROM target_allocations ta
                   JOIN accounts a ON a.id = ta.account_id
                   WHERE a.active = 1 ORDER BY a.id, ta.ticker"""
            ).fetchall()
        df = pd.DataFrame([dict(r) for r in rows])
        if df.empty:
            st.info("비중 데이터 없음")
            return
        df.columns = ["계좌", "티커", "종목명", "비중"]
        df["계좌"] = df["계좌"].map(display_name)
        df["비중"] = df["비중"] * 100
        st.dataframe(
            df, hide_index=True, use_container_width=True,
            column_config={"비중": st.column_config.NumberColumn(format="%.1f%%")},
        )
        st.caption(
            "비중/티커 수정은 `src/seed.py`의 `ALLOCATIONS` 편집 후 "
            "'완전 초기화' 또는 DB에서 `target_allocations` 직접 UPDATE."
        )


PAGES = {
    "대시보드": page_dashboard,
    "월 납입": page_monthly,
    "보유 종목": page_holdings,
    "거래 내역": page_history,
    "미래가치 시뮬": page_projection,
    "설정": page_settings,
}
PAGES[page]()

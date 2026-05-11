"""2026-05-31부터 적용되는 월 납입 조정 마이그레이션.

- SAVINGS1, SAVINGS2 monthly_deposit: 500,000 → 200,000
- 신규 STOCK 계좌 추가 (월 600,000, KRW 누적, VT 비중 100%)

실행: python scripts/migrate_2026_05.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "portfolio.db"
SEED_DATE = "2026-04-22"      # __STOCK__ 가상 가격 기준일 (다른 internal과 동일)
STOCK_OPENED_AT = "2026-05-31"  # STOCK 계좌 개설일


def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERROR] DB 파일 없음: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        # 1) 적금 월 납입 조정
        cur = conn.execute(
            "UPDATE accounts SET monthly_deposit = 200000 "
            "WHERE code IN ('SAVINGS1', 'SAVINGS2')"
        )
        print(f"[1/5] 적금1/2 월 납입 200,000원으로 갱신 ({cur.rowcount}건)")

        # 2) STOCK 계좌 (없으면 추가)
        existing = conn.execute(
            "SELECT id, monthly_deposit FROM accounts WHERE code = 'STOCK'"
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO accounts
                   (code, name, kind, monthly_deposit, opened_at, matures_at,
                    interest_rate, cycle_months, tax_deductible, active, generation)
                   VALUES ('STOCK', '주식계좌', 'STOCK', 600000, ?, NULL,
                           0, 0, 0, 1, 1)""",
                (STOCK_OPENED_AT,),
            )
            print(f"[2/5] STOCK 계좌 신규 생성 (월 600,000원, opened {STOCK_OPENED_AT})")
        else:
            conn.execute(
                "UPDATE accounts SET monthly_deposit = 600000 WHERE code = 'STOCK'"
            )
            print(f"[2/5] STOCK 계좌 이미 존재 (id={existing['id']}), 월 납입만 600,000원으로 갱신")

        stock_id = conn.execute(
            "SELECT id FROM accounts WHERE code = 'STOCK'"
        ).fetchone()["id"]

        # 3) target_allocations: VT 100% (projection에서 VT의 CAGR 사용)
        conn.execute(
            """INSERT OR REPLACE INTO target_allocations
               (account_id, ticker, name, target_ratio)
               VALUES (?, 'VT', 'Vanguard Total World Stock ETF', 1.0)""",
            (stock_id,),
        )
        print("[3/5] STOCK target_allocations: VT 100%")

        # 4) __STOCK__ 가상 티커 가격 (price=1, internal)
        conn.execute(
            """INSERT OR REPLACE INTO prices(ticker, date, close, source)
               VALUES ('__STOCK__', ?, 1, 'internal')""",
            (SEED_DATE,),
        )
        print("[4/5] __STOCK__ 가상 티커 가격 등록 (1원, internal)")

        # 5) holdings 빈 항목 생성 (없으면)
        exists = conn.execute(
            "SELECT 1 FROM holdings WHERE account_id = ? AND ticker = '__STOCK__'",
            (stock_id,),
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO holdings(account_id, ticker, shares, cost_basis)
                   VALUES (?, '__STOCK__', 0, 0)""",
                (stock_id,),
            )
            print("[5/5] STOCK holdings 초기화 (__STOCK__ shares=0)")
        else:
            print("[5/5] STOCK holdings 이미 존재 - 유지")

        conn.commit()
        print("\n[OK] 마이그레이션 완료")
        print("     다음 'python main.py monthly 2026-05-31' 실행 시 새 납입금 적용됨")
        return 0

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] 롤백: {e}")
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

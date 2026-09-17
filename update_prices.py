"""
stock 폴더 안의 stock.json에 있는 종목 마스터(stockCatalog)의 currentPrice를
실시간(지연) 시세로 갱신합니다.

- 데이터 소스: FinanceDataReader (KRX, 무료, API 키 불필요)
- stockCatalog는 "종목 하나당 한 줄"이라, 계좌가 여러 개여도 딱 한 번만 갱신하면
  모든 계좌의 평가금액에 자동 반영됩니다 (stocks[] 각각을 돌 필요 없음).
- ticker가 있는 항목만 갱신하고, 없는 항목(예: 이름만 등록된 펀드 등)은 건드리지 않습니다.
- owners / accounts / stocks / dividends / targets / plannedCash / snapshots 등
  다른 필드는 그대로 둡니다.

이 파일은 update_prices.py 에 위치합니다 (stock.json과 같은 폴더).
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr

# 이 스크립트 파일과 같은 폴더에 있는 stock.json을 가리킴 (경로 하드코딩 없이 안전하게)
DATA_PATH = Path(__file__).resolve().parent / "stock.json"



def load_data(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_latest_close(ticker):
    """최근 거래일 종가(또는 최신가)를 가져옵니다."""
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    df = fdr.DataReader(ticker, start)
    if df.empty:
        raise ValueError(f"{ticker}: 조회된 데이터가 없습니다")
    return int(df["Close"].iloc[-1])


def main():
    data = load_data(DATA_PATH)
    catalog = data.get("stockCatalog", [])

    if not catalog:
        print("stockCatalog가 비어있습니다. (구버전 stock.json이거나 아직 종목이 없어요)")
        return

    tickers = sorted({
        c["ticker"].strip()
        for c in catalog
        if c.get("ticker") and c["ticker"].strip()
    })

    if not tickers:
        print("갱신할 티커가 없습니다.")
        return

    prices = {}
    for ticker in tickers:
        try:
            price = get_latest_close(ticker)
            prices[ticker] = price
            print(f"{ticker}: {price:,}원")
        except Exception as e:
            print(f"[실패] {ticker}: {e}", file=sys.stderr)

    updated = 0
    for c in catalog:
        t = (c.get("ticker") or "").strip()
        if t in prices and c.get("currentPrice") != prices[t]:
            c["currentPrice"] = prices[t]
            updated += 1

    if updated > 0:
        kst_now = (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
        data["lastPriceUpdate"] = kst_now + " (KST)"
        save_data(DATA_PATH, data)
        print(f"{updated}개 종목의 현재가를 갱신했습니다.")
    else:
        print("변경된 가격이 없습니다.")


if __name__ == "__main__":
    main()

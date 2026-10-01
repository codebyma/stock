"""
stock 폴더 안의 stock.json을 자동으로 갱신합니다.

1) 종목 마스터(stockCatalog)의 currentPrice 갱신
   - 데이터 소스: FinanceDataReader (KRX, 무료, API 키 불필요)
   - stockCatalog는 "종목 하나당 한 줄"이라, 계좌가 여러 개여도 딱 한 번만 갱신하면
     모든 계좌의 평가금액에 자동 반영됩니다.
   - ticker가 있는 항목만 갱신하고, 없는 항목(예: 이름만 등록된 펀드 등)은 건드리지 않습니다.

2) 자산 스냅샷(snapshots) 자동 기록 / 누락 날짜 채우기
   - 앱을 열지 않은 날도 거래일마다 스냅샷이 쌓이도록 합니다.
   - 과거 날짜는 매수/매도 내역의 날짜로 그날의 보유수량·투자원금을 다시 계산하고,
     그날 종가를 곱해 평가금액을 만듭니다. (앱의 stockCostBasis와 같은 이동평균원가 방식)
   - 오늘 스냅샷은 앱과 같은 방식(stock.quantity × currentPrice)으로 만듭니다.
   - 거래일 판단은 종가 데이터가 있는 날짜 기준이라, 주말·휴장일은 자동으로 건너뜁니다.

   실행 모드 (환경변수 SNAPSHOT_MODE)
     daily    (기본) 오늘 스냅샷 갱신 + 최근 30일 중 빠진 거래일 채우기. 기존 스냅샷은 덮어쓰지 않음
     backfill 가장 이른 매수일부터 빠진 거래일을 모두 채움. 기존 스냅샷은 덮어쓰지 않음
     rebuild  기존 스냅샷까지 전부 다시 계산해서 덮어씀 (직접 고친 값도 사라지니 주의)
   SNAPSHOT_FROM=YYYY-MM-DD 를 주면 backfill/rebuild의 시작일을 지정할 수 있습니다.

- owners / accounts / stocks / dividends / targets / plannedCash 등 다른 필드는 그대로 둡니다.

이 파일은 update_prices.py 에 위치합니다 (stock.json과 같은 폴더).
"""

import json
import os
import sys
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr

# 이 스크립트 파일과 같은 폴더에 있는 stock.json을 가리킴 (경로 하드코딩 없이 안전하게)
DATA_PATH = Path(__file__).resolve().parent / "stock.json"

FAR_FUTURE = "9999-12-31"


def load_data(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------------------------------------------------------------- 시세 조회

def fetch_history(ticker, start):
    """start(YYYY-MM-DD) 이후의 일별 종가를 (날짜 리스트, 종가 리스트)로 반환합니다."""
    df = fdr.DataReader(ticker, start)
    if df.empty:
        raise ValueError(f"{ticker}: 조회된 데이터가 없습니다")
    dates, closes = [], []
    for idx, close in zip(df.index, df["Close"]):
        if close != close:  # NaN 건너뜀
            continue
        dates.append(idx.strftime("%Y-%m-%d"))
        closes.append(int(close))
    if not dates:
        raise ValueError(f"{ticker}: 유효한 종가가 없습니다")
    return dates, closes


def price_on(histories, ticker, date):
    """date 당일(없으면 직전 거래일) 종가. 데이터가 없으면 None."""
    h = histories.get(ticker)
    if not h:
        return None
    dates, closes = h
    i = bisect_right(dates, date) - 1
    return closes[i] if i >= 0 else None


# ---------------------------------------------------------------- 보유 내역 계산 (앱 로직과 동일)

def qty_as_of(stock, date):
    """앱의 quantityAsOfDate와 동일: 날짜가 있는 매수/매도만 기준일까지 합산."""
    bought = sum((b.get("quantity") or 0) for b in stock.get("buys", []) if b.get("date") and b["date"] <= date)
    sold = sum((s.get("quantity") or 0) for s in stock.get("sells", []) if s.get("date") and s["date"] <= date)
    return max(0, bought - sold)


def cost_as_of(stock, date):
    """앱의 stockCostBasis와 동일한 이동평균원가를 기준일까지의 거래만으로 계산."""
    txns = [("buy", b.get("date") or "", b.get("quantity") or 0, b.get("amount") or 0) for b in stock.get("buys", [])]
    txns += [("sell", s.get("date") or "", s.get("quantity") or 0, 0) for s in stock.get("sells", [])]
    txns = [t for t in txns if t[1] <= date]
    txns.sort(key=lambda t: t[1])  # 안정 정렬: 같은 날이면 매수가 먼저
    qty, cost = 0.0, 0.0
    for kind, _d, q, amount in txns:
        if kind == "buy":
            cost += amount
            qty += q
        else:
            avg = cost / qty if qty > 0 else 0
            sell_qty = min(q, qty)
            cost -= avg * sell_qty
            qty -= sell_qty
    return max(0.0, cost)


def earliest_tx_date(data):
    dates = []
    for s in data.get("stocks", []):
        for t in list(s.get("buys", [])) + list(s.get("sells", [])):
            if t.get("date"):
                dates.append(t["date"])
    return min(dates) if dates else None


def compute_snapshot(data, date, is_today, histories, catalog_by_key, proxied):
    acc_owner = {a["id"]: a.get("ownerId") for a in data.get("accounts", [])}
    owner_ids = [o["id"] for o in data.get("owners", [])]
    totals = {oid: 0.0 for oid in owner_ids}
    costs = {oid: 0.0 for oid in owner_ids}
    total_all = cost_all = 0.0

    for stock in data.get("stocks", []):
        acc = stock.get("accountId")
        if acc not in acc_owner:
            continue
        cat = catalog_by_key.get(stock.get("key"), {})
        cost = cost_as_of(stock, FAR_FUTURE if is_today else date)

        if is_today:
            # 앱과 동일: 저장된 보유수량 × 현재가
            value = (stock.get("quantity") or 0) * (cat.get("currentPrice") or 0)
        else:
            qty = qty_as_of(stock, date)
            value = 0.0
            if qty > 0:
                ticker = (cat.get("ticker") or "").strip()
                price = price_on(histories, ticker, date) if ticker else None
                if price is None:
                    # 과거 시세를 알 수 없는 종목(티커 없음, 상장 전 등)은 매입원가로 평가
                    price = cost / qty
                    proxied.add(cat.get("name") or stock.get("key"))
                value = qty * price

        owner = acc_owner[acc]
        if owner in totals:
            totals[owner] += value
            costs[owner] += cost
        total_all += value
        cost_all += cost

    totals_out = {k: int(round(v)) for k, v in totals.items()}
    costs_out = {k: int(round(v)) for k, v in costs.items()}
    totals_out["ALL"] = int(round(total_all))
    costs_out["ALL"] = int(round(cost_all))
    return {"date": date, "totals": totals_out, "costs": costs_out}


# ---------------------------------------------------------------- 메인

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

    now_kst = datetime.utcnow() + timedelta(hours=9)
    today_kst = now_kst.strftime("%Y-%m-%d")
    kst_now_full = now_kst.strftime("%Y-%m-%d %H:%M") + " (KST)"

    # ----- 스냅샷 범위 결정
    mode = (os.environ.get("SNAPSHOT_MODE") or "daily").strip().lower()
    if mode not in ("daily", "backfill", "rebuild"):
        print(f"알 수 없는 SNAPSHOT_MODE '{mode}' → daily로 실행합니다.", file=sys.stderr)
        mode = "daily"
    from_env = (os.environ.get("SNAPSHOT_FROM") or "").strip()

    earliest = earliest_tx_date(data)
    snap_start = None
    if earliest:
        if mode == "daily":
            snap_start = max((now_kst - timedelta(days=30)).strftime("%Y-%m-%d"), earliest)
        else:
            snap_start = max(from_env, earliest) if from_env else earliest

    # 시세 조회 시작일: 스냅샷 범위 + 직전 거래일 종가 확보용 여유 10일
    base = min(snap_start, (now_kst - timedelta(days=10)).strftime("%Y-%m-%d")) if snap_start \
        else (now_kst - timedelta(days=10)).strftime("%Y-%m-%d")
    hist_start = (datetime.strptime(base, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")

    # ----- 시세 조회 (티커당 1회)
    histories = {}
    prices = {}
    for ticker in tickers:
        try:
            dates, closes = fetch_history(ticker, hist_start)
            histories[ticker] = (dates, closes)
            prices[ticker] = closes[-1]
            print(f"{ticker}: {closes[-1]:,}원")
        except Exception as e:
            print(f"[실패] {ticker}: {e}", file=sys.stderr)

    # ----- 현재가 갱신
    updated = 0
    for c in catalog:
        t = (c.get("ticker") or "").strip()
        if t not in prices:
            continue
        # 값이 그대로여도 오늘 시세 조회에 성공했다면 "최신 확인일"은 갱신한다.
        # 이걸 안 하면 앱의 "현재가 방치 알림"(priceUpdatedAt 기준)이
        # 실제로는 매일 갱신되고 있어도 계속 옛날 날짜에 멈춰 있게 된다.
        if c.get("priceUpdatedAt") != today_kst:
            c["priceUpdatedAt"] = today_kst
            updated += 1
        if c.get("currentPrice") != prices[t]:
            c["currentPrice"] = prices[t]
            updated += 1

    # ----- 스냅샷 기록
    snap_changed = 0
    if snap_start:
        catalog_by_key = {c.get("key"): c for c in catalog}

        # 보유수량이 매수/매도 내역과 안 맞는 종목은 과거 계산이 어긋나므로 알려준다
        for s in data.get("stocks", []):
            if (s.get("quantity") or 0) != qty_as_of(s, FAR_FUTURE):
                name = catalog_by_key.get(s.get("key"), {}).get("name") or s.get("key")
                print(f"[주의] {name}: 보유수량({s.get('quantity')})이 매수/매도 내역 합계"
                      f"({qty_as_of(s, FAR_FUTURE)})와 달라 과거 스냅샷이 어긋날 수 있어요", file=sys.stderr)

        trading_days = set()
        for dates, _ in histories.values():
            trading_days.update(d for d in dates if snap_start <= d <= today_kst)

        snap_by_date = {s["date"]: s for s in data.get("snapshots", []) if s.get("date")}
        proxied = set()
        for d in sorted(trading_days):
            is_today = (d == today_kst)
            exists = d in snap_by_date
            if exists and not is_today and mode != "rebuild":
                continue  # 이미 있는 과거 스냅샷은 덮어쓰지 않음
            snap = compute_snapshot(data, d, is_today, histories, catalog_by_key, proxied)
            if snap["totals"]["ALL"] == 0 and snap["costs"]["ALL"] == 0:
                continue
            if exists and snap_by_date[d] == snap:
                continue
            snap_by_date[d] = snap
            snap_changed += 1

        if snap_changed:
            data["snapshots"] = [snap_by_date[k] for k in sorted(snap_by_date)]
        if proxied:
            print("[참고] 과거 시세가 없어 매입원가로 평가한 종목: " + ", ".join(sorted(proxied)), file=sys.stderr)
        print(f"스냅샷 {snap_changed}건 기록 (mode={mode}, 범위 {snap_start} ~ {today_kst})")

    if updated > 0 or snap_changed > 0:
        if updated > 0:
            data["lastPriceUpdate"] = kst_now_full
        save_data(DATA_PATH, data)
        print(f"저장 완료 (가격/확인일 {updated}건, 스냅샷 {snap_changed}건)")
    else:
        print("변경된 내용이 없습니다.")


if __name__ == "__main__":
    main()

"""
stock.json의 현재가(stockCatalog.currentPrice)를 기준으로 오늘 날짜의
자산 스냅샷(owners별 + 전체 평가금액·투자원금)을 계산해 snapshots에 upsert합니다.

- 평가금액(totals) = 각 보유 종목의 quantity × catalog.currentPrice 합계
- 투자원금(costs)  = 평균법(이동평균원가) 기준.
  매도 시에는 매도금액이 아니라 "매도 시점의 평균매입단가 × 매도수량"만큼만
  원금에서 차감합니다. (stock-tracker.html의 stockCostBasis()와 완전히 동일한 로직 —
  두 계산이 어긋나면 앱에서 보는 수익률과 스냅샷 그래프가 서로 달라지므로
  이 로직을 수정하게 되면 앱 쪽도 함께 맞춰야 합니다.)

이 파일은 update_prices.py와 같은 폴더(=stock.json이 있는 폴더)에 둡니다.
가격 갱신(update_prices.py)이 끝난 뒤에 실행하는 걸 전제로 하며, 이 스크립트는
가격을 건드리지 않고 stockCatalog.currentPrice를 "읽기만" 합니다.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "stock.json"


def load_data(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def stock_total(stock, catalog_by_key):
    """quantity × 현재가. 카탈로그에 없는 종목(키 불일치)은 0으로 취급."""
    cat = catalog_by_key.get(stock.get("key"))
    price = (cat.get("currentPrice") or 0) if cat else 0
    return (stock.get("quantity") or 0) * price


def stock_cost_basis(stock):
    """평균법(이동평균원가). stock-tracker.html의 stockCostBasis()와 동일한 로직.

    매수/매도 내역을 날짜순으로 처리하면서:
    - 매수: 원금에 매수금액을 더하고 수량을 더한다.
    - 매도: "매도 시점의 평균매입단가(=현재 원금/현재 수량) × 매도수량"만큼만
      원금에서 뺀다. (매도금액 자체를 빼면 실현손익이 원금에 섞여
      남은 수량의 수익률이 왜곡된다)
    """
    txns = []
    for b in stock.get("buys") or []:
        txns.append((b.get("date") or "", "buy", b.get("quantity") or 0, b.get("amount") or 0))
    for s in stock.get("sells") or []:
        txns.append((s.get("date") or "", "sell", s.get("quantity") or 0, 0))
    txns.sort(key=lambda t: t[0])

    qty = 0.0
    cost = 0.0
    for _, ttype, quantity, amount in txns:
        if ttype == "buy":
            cost += amount
            qty += quantity
        else:
            avg_cost = (cost / qty) if qty > 0 else 0
            sell_qty = min(quantity, qty)
            cost -= avg_cost * sell_qty
            qty -= sell_qty
    return max(0.0, cost)


def main():
    data = load_data(DATA_PATH)
    owners = data.get("owners") or []
    accounts = data.get("accounts") or []
    stocks = data.get("stocks") or []
    catalog_by_key = {c["key"]: c for c in (data.get("stockCatalog") or []) if c.get("key")}

    if not isinstance(data.get("snapshots"), list):
        data["snapshots"] = []

    account_owner = {a["id"]: a.get("ownerId") for a in accounts}

    totals = {}
    costs = {}

    def add(owner_id, stock):
        totals[owner_id] = totals.get(owner_id, 0) + stock_total(stock, catalog_by_key)
        costs[owner_id] = costs.get(owner_id, 0) + stock_cost_basis(stock)

    for stock in stocks:
        owner_id = account_owner.get(stock.get("accountId"))
        if owner_id is None:
            continue  # 계좌가 삭제된 고아 데이터 등은 스킵
        add(owner_id, stock)
        add("ALL", stock)

    # 보유 종목이 없는 owner도 0으로 채워서, 앱(upsertSnapshot)과 동일하게
    # 모든 owner + 'ALL'에 항상 값이 존재하도록 한다.
    for o in owners:
        totals.setdefault(o["id"], 0)
        costs.setdefault(o["id"], 0)
    totals.setdefault("ALL", 0)
    costs.setdefault("ALL", 0)

    today = (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d")  # KST 기준 날짜

    snapshots = data["snapshots"]
    idx = next((i for i, s in enumerate(snapshots) if s.get("date") == today), None)
    entry = {"date": today, "totals": totals, "costs": costs}
    if idx is not None:
        snapshots[idx] = entry
    else:
        snapshots.append(entry)
    snapshots.sort(key=lambda s: s.get("date", ""))

    save_data(DATA_PATH, data)
    print(f"{today} 스냅샷 저장 완료 (전체 평가금액: {totals.get('ALL', 0):,.0f}원)")


if __name__ == "__main__":
    main()

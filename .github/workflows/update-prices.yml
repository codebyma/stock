name: Update KRX Stock Prices

on:
  schedule:
    # 평일(월~금) 09:00~17:00 KST(=UTC 00:00~08:00) 사이 2시간마다 실행
    - cron: '0 0,2,4,6,8 * * 1-5'
  workflow_dispatch:   # 필요할 때 수동으로도 실행 가능 (Actions 탭 > Run workflow)
    inputs:
      snapshot_mode:
        description: '스냅샷 모드 (daily: 최근 빠진 날만 / backfill: 전체 기간 빠진 날 채우기 / rebuild: 전부 다시 계산)'
        type: choice
        default: daily
        options:
          - daily
          - backfill
          - rebuild
      snapshot_from:
        description: '시작일 (선택, 예: 2026-01-01. backfill/rebuild에서만 사용)'
        required: false
        default: ''

permissions:
  contents: write

jobs:
  update-prices:
    runs-on: ubuntu-latest
    steps:
      - name: 저장소 체크아웃
        uses: actions/checkout@v4

      - name: 파이썬 설치
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: 의존성 설치
        run: pip install finance-datareader pandas

      - name: 주가 갱신 및 스냅샷 기록 스크립트 실행
        env:
          # 예약 실행(schedule)에는 입력값이 없으므로 빈 값 → 스크립트 기본값(daily)으로 동작
          SNAPSHOT_MODE: ${{ github.event.inputs.snapshot_mode }}
          SNAPSHOT_FROM: ${{ github.event.inputs.snapshot_from }}
        run: python update_prices.py

      - name: 변경사항 커밋 및 푸시
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add stock.json
          git diff --staged --quiet || git commit -m "chore: 주가·스냅샷 자동 갱신 [skip ci]"
          git push

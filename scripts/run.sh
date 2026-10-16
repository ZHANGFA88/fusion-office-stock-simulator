#!/usr/bin/env bash
# Fusion Office launcher. Background workers are opt-in; the default preview is demo-only for news.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${FINANCE_PORT:-8772}"
WITH_SPIDER_DEMO=0
WITH_AI=0
WITH_MARKET_REFRESH=0
for arg in "$@"; do
  case "$arg" in
    --with-spider-demo) WITH_SPIDER_DEMO=1 ;;
    --with-ai) WITH_AI=1 ;;
    --with-market-refresh) WITH_MARKET_REFRESH=1 ;;
    --help)
      printf '%s\n' 'Usage: scripts/run.sh [--with-spider-demo] [--with-ai] [--with-market-refresh]' \
        'Default: start only the finance server; no background feeder, AI crew, or live market refresh.'
      exit 0 ;;
    *) echo "Unknown option: $arg (use --help)" >&2; exit 2 ;;
  esac
done
if [ "$WITH_SPIDER_DEMO" -eq 1 ]; then
  (ENABLE_DEMO_SPIDER_FEEDER=1 FINANCE_PORT="$PORT" nohup node scripts/spider_news_feeder.mjs > /tmp/fusion-office-spider.log 2>&1 &) || true
  echo "synthetic Spider DEMO feeder → local /api/finance/observatory only (log: /tmp/fusion-office-spider.log)"
fi
if [ "$WITH_AI" -eq 1 ]; then
  (FINANCE_PORT="$PORT" nohup python3 scripts/ai_crew.py > /tmp/fusion-office-ai.log 2>&1 &) || true
  echo "AI trading crew explicitly enabled (PAPER only; log: /tmp/fusion-office-ai.log)"
fi
if [ "$WITH_MARKET_REFRESH" -eq 1 ]; then
  echo "live market background refresh explicitly enabled"
else
  echo "live market background refresh disabled by default"
fi
echo "fusion office → http://127.0.0.1:${PORT}"
FINANCE_ENABLE_BACKGROUND_REFRESH="$WITH_MARKET_REFRESH" FINANCE_PORT="$PORT" python3 scripts/serve.py

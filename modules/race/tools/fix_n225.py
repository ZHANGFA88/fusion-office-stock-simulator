#!/usr/bin/env python3
"""Rebuild 1321.T total-return series from Yahoo close + dividend events.
Yahoo's adjclose for 1321.T is corrupt (negative values). close is split-adjusted;
layer dividends: on ex-date, return = (close + div) / prev_close.
Writes cache/yahoo_n225.json = {date: TR price in JPY} (same scale as close)."""
import json, subprocess, datetime, sys

PROXY = "http://hatch-egress-proxy:3128"
P1 = int(datetime.datetime(2009, 12, 1, tzinfo=datetime.timezone.utc).timestamp())
P2 = int(datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc).timestamp())
url = (f"https://query2.finance.yahoo.com/v8/finance/chart/1321.T"
       f"?period1={P1}&period2={P2}&interval=1d&events=div,split")
out = subprocess.run(["curl", "-s", "-x", PROXY, "-H", "User-Agent: Mozilla/5.0", url],
                     capture_output=True, text=True, timeout=90)
d = json.loads(out.stdout)
r = d["chart"]["result"][0]
off = r["meta"].get("gmtoffset", 0)
ts = r["timestamp"]
q = r["indicators"]["quote"][0]
splits = r.get("events", {}).get("splits", {})
split_list = []
for k, v in splits.items():
    num, den = v["splitRatio"].split(":")
    split_list.append((v["date"], float(num) / float(den)))
print("split events:", split_list, file=sys.stderr)

def split_factor_after(ts_epoch):
    f = 1.0
    for sdate, ratio in split_list:
        if sdate > ts_epoch:
            f *= ratio
    return f

divs = {}
ev = r.get("events", {}).get("dividends", {})
for k, v in ev.items():
    day = datetime.datetime.utcfromtimestamp(v["date"] + off).strftime("%Y-%m-%d")
    divs[day] = v["amount"] / split_factor_after(v["date"])
print("dividend events:", len(divs), "sample:", dict(list(divs.items())[:3]), file=sys.stderr)

rows = []
for i in range(len(ts)):
    c = q["close"][i]
    if c is None:
        continue
    day = datetime.datetime.utcfromtimestamp(ts[i] + off).strftime("%Y-%m-%d")
    rows.append((day, c))
rows.sort()
print("close rows:", len(rows), rows[0], rows[-1], file=sys.stderr)

tr = {}
prev_close, prev_tr = None, None
for day, c in rows:
    if prev_close is None:
        t = c
    else:
        t = prev_tr * (c + divs.get(day, 0.0)) / prev_close
    tr[day] = t
    prev_close, prev_tr = c, t
print("TR x:", round(tr[rows[-1][0]] / tr[rows[0][0]], 2),
      "close-only x:", round(rows[-1][1] / rows[0][1], 2), file=sys.stderr)
json.dump(tr, open("/home/hatch/workspace/global_etf_race/data/cache/yahoo_n225.json", "w"))
print("cache yahoo_n225.json replaced with TR series")

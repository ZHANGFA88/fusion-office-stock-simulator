#!/usr/bin/env python3
"""Build a total-return daily series from Yahoo close + dividend events (split-safe).
Usage: build_tr.py SYMBOL OUTFILE.json  -> writes {date: TR price} (close scale)."""
import json, subprocess, datetime, sys

PROXY = "http://hatch-egress-proxy:3128"
sym, outfile = sys.argv[1], sys.argv[2]
P1 = int(datetime.datetime(2009, 12, 1, tzinfo=datetime.timezone.utc).timestamp())
P2 = int(datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc).timestamp())
url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}"
       f"?period1={P1}&period2={P2}&interval=1d&events=div,split")
out = subprocess.run(["curl", "-s", "-x", PROXY, "-H", "User-Agent: Mozilla/5.0", url],
                     capture_output=True, text=True, timeout=90)
d = json.loads(out.stdout)
r = d["chart"]["result"][0]
off = r["meta"].get("gmtoffset", 0)
splits = []
for k, v in r.get("events", {}).get("splits", {}).items():
    num, den = v["splitRatio"].split(":")
    splits.append((v["date"], float(num) / float(den)))

def factor_after(epoch):
    f = 1.0
    for sdate, ratio in splits:
        if sdate > epoch:
            f *= ratio
    return f

divs = {}
for k, v in r.get("events", {}).get("dividends", {}).items():
    day = datetime.datetime.utcfromtimestamp(v["date"] + off).strftime("%Y-%m-%d")
    divs[day] = divs.get(day, 0.0) + v["amount"] / factor_after(v["date"])

q = r["indicators"]["quote"][0]
rows = []
for i, t in enumerate(r["timestamp"]):
    c = q["close"][i]
    if c is not None:
        rows.append((datetime.datetime.utcfromtimestamp(t + off).strftime("%Y-%m-%d"), c))
rows.sort()
tr, pc, pt = {}, None, None
for day, c in rows:
    t = c if pc is None else pt * (c + divs.get(day, 0.0)) / pc
    tr[day] = t
    pc, pt = c, t
print(sym, "rows", len(rows), "divs", len(divs), "splits", splits,
      "TR x", round(tr[rows[-1][0]] / tr[rows[0][0]], 2), file=sys.stderr)
json.dump(tr, open(outfile, "w"))

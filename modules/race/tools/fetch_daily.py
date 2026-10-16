#!/usr/bin/env python3
"""Daily fetch: 5 ETFs (video's instruments) + FX from Yahoo, H00300 TRI from CSI official.
All converted to CNY daily closes; unified calendar = union of trading days, forward-filled.
Output: data/daily.json {dates:[...], assets:{qqq,spy,n225,dax,cac,csi:[...]}} in CNY.
Range: 2010-01-01 .. 2026-10-08 (video's window)."""
import json, subprocess, sys, datetime, time

PROXY = "http://hatch-egress-proxy:3128"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
P1 = int(datetime.datetime(2009, 12, 1, tzinfo=datetime.timezone.utc).timestamp())
P2 = int(datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc).timestamp())

def curl(url, timeout=120):
    for attempt in range(5):
        try:
            out = subprocess.run(["curl", "-s", "-x", PROXY, "-H", f"User-Agent: {UA}", url],
                                 capture_output=True, text=True, timeout=timeout)
            if out.stdout:
                return out.stdout
        except subprocess.TimeoutExpired:
            print("timeout, retry", attempt, url[:70], file=sys.stderr)
        time.sleep(3)
    raise RuntimeError("empty response: " + url[:80])

import os
CACHE = "/home/hatch/workspace/global_etf_race/data/cache"
os.makedirs(CACHE, exist_ok=True)

def cached(name, fn):
    p = os.path.join(CACHE, name + ".json")
    if os.path.exists(p):
        return json.load(open(p))
    v = fn()
    json.dump(v, open(p, "w"))
    return v

def yahoo(symbol):
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={P1}&period2={P2}&interval=1d&events=div,split")
    d = json.loads(curl(url))
    r = d["chart"]["result"][0]
    off = r["meta"].get("gmtoffset", 0)
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose")
    series = {}
    for i in range(len(ts)):
        c = q["close"][i]
        if c is None:
            continue
        v = adj[i] if adj and adj[i] else c
        day = datetime.datetime.utcfromtimestamp(ts[i] + off).strftime("%Y-%m-%d")
        series[day] = v
    return series

def csi_year(y):
    url = (f"https://www.csindex.com.cn/csindex-home/perf/index-perf?"
           f"indexCode=H00300&startDate={y}0101&endDate={y}1231")
    d = json.loads(curl(url))
    out = {}
    for row in d.get("data", []):
        td = row["tradeDate"]
        day = f"{td[:4]}-{td[4:6]}-{td[6:]}"
        if row.get("close"):
            out[day] = float(row["close"])
    return out

def csi_h00300():
    series = {}
    for y in range(2010, 2027):
        part = cached(f"csi_{y}", lambda y=y: csi_year(y))
        series.update(part)
        print("csi", y, "rows so far", len(series), file=sys.stderr)
    return series

symbols = {
    "qqq": "QQQ", "spy": "SPY", "n225": "1321.T", "dax": "EXS1.DE", "cac": "CAC.PA",
    "usdcny": "USDCNY%3DX", "eurusd": "EURUSD%3DX", "usdjpy": "JPY%3DX",
}
raw = {}
for k, s in symbols.items():
    raw[k] = cached("yahoo_" + k, lambda s=s: yahoo(s))
    ks = sorted(raw[k])
    print(k, len(ks), ks[0], "->", ks[-1], file=sys.stderr)
raw["csi"] = csi_h00300()
ks = sorted(raw["csi"])
print("csi", len(ks), ks[0], "->", ks[-1], file=sys.stderr)

START, END = "2010-01-01", "2026-10-08"
all_days = set()
for s in raw.values():
    all_days.update(d for d in s if START <= d <= END)
dates = sorted(all_days)
print("unified days:", len(dates), dates[0], "->", dates[-1], file=sys.stderr)

def filled(series):
    out, last = [], None
    for d in dates:
        v = series.get(d)
        if v is not None:
            last = v
        out.append(last)
    first = next(v for v in out if v is not None)
    return [first if v is None else v for v in out]

f_usdcny = filled(raw["usdcny"])
f_eurusd = filled(raw["eurusd"])
f_usdjpy = filled(raw["usdjpy"])
eurcny = [a * b for a, b in zip(f_eurusd, f_usdcny)]
cnyjpy = [a / b for a, b in zip(f_usdjpy, f_usdcny)]

def conv(key, fx, mode):
    s = filled(raw[key])
    out = []
    for i, p in enumerate(s):
        out.append(None if p is None else round(p if fx is None else (p * fx[i] if mode == "mul" else p / fx[i]), 4))
    return out

data = {"dates": dates, "assets": {
    "qqq": conv("qqq", f_usdcny, "mul"),
    "spy": conv("spy", f_usdcny, "mul"),
    "n225": conv("n225", cnyjpy, "div"),
    "dax": conv("dax", eurcny, "mul"),
    "cac": conv("cac", eurcny, "mul"),
    "csi": conv("csi", None, None),
}}
assert all(v[0] is not None for v in data["assets"].values()), "leading None remains"
with open("/home/hatch/workspace/global_etf_race/data/daily.json", "w") as f:
    json.dump(data, f)
print("written daily.json:", len(dates), "days")
for k, v in data["assets"].items():
    print(k, "x", round(v[-1] / v[0], 2))

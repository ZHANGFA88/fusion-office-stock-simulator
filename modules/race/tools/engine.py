#!/usr/bin/env python3
"""DCA engine on daily.json (CNY prices). Semantics verified against video math:
portfolio = sum of independent buy-and-hold slices (no rebalancing);
solo = ¥3000/mo into one asset; optimal slices 1190/920/320/270/150/150; equal = 500 each.
Purchases on the first unified-calendar day of each month (2010-01..2026-10 = 202 periods).
Emits data/engine.json with daily series + finals + anchors + event-window stats."""
import json, datetime, math, sys

DATA = sys.argv[1] if len(sys.argv) > 1 else "/home/hatch/workspace/global_etf_race/data/daily.json"
OUTP = sys.argv[2] if len(sys.argv) > 2 else "/home/hatch/workspace/global_etf_race/data/engine.json"
D = json.load(open(DATA))
dates, assets = D["dates"], D["assets"]
KEYS = ["qqq", "spy", "csi", "n225", "dax", "cac"]
AMT = {"qqq": 1190, "spy": 920, "csi": 320, "n225": 270, "dax": 150, "cac": 150}
N = len(dates)
didx = {d: i for i, d in enumerate(dates)}

buy_idx = []
seen = set()
for i, d in enumerate(dates):
    ym = d[:7]
    if ym not in seen:
        seen.add(ym)
        buy_idx.append(i)
print("periods:", len(buy_idx), "first buy:", dates[buy_idx[0]], "last buy:", dates[buy_idx[-1]])
is_buy = [False] * N
for i in buy_idx:
    is_buy[i] = True

def run(amounts):
    """amounts: {key: monthly cny}. Returns (value[], invested[])."""
    shares = {k: 0.0 for k in KEYS}
    value = [0.0] * N
    invested = [0.0] * N
    inv = 0.0
    for i in range(N):
        if is_buy[i]:
            for k in KEYS:
                a = amounts.get(k, 0)
                if a:
                    shares[k] += a / assets[k][i]
            inv += sum(amounts.values())
        value[i] = sum(shares[k] * assets[k][i] for k in KEYS)
        invested[i] = inv
    return value, invested

solos = {}
for k in KEYS:
    v, inv = run({k: 3000})
    solos[k] = (v, inv)
port_v, port_inv = run(AMT)
eq_v, eq_inv = run({k: 500 for k in KEYS})

def ret(v, inv):
    return [ (v[i] / inv[i] - 1) if inv[i] else 0.0 for i in range(N) ]

port_r = ret(port_v, port_inv)
eq_r = ret(eq_v, eq_inv)
solo_r = {k: ret(*solos[k]) for k in KEYS}

# drift weights (slice market value share)
slice_shares = {k: 0.0 for k in KEYS}
drift = {k: [0.0] * N for k in KEYS}
for i in range(N):
    if is_buy[i]:
        for k in KEYS:
            slice_shares[k] += AMT[k] / assets[k][i]
    tot = sum(slice_shares[k] * assets[k][i] for k in KEYS)
    for k in KEYS:
        drift[k][i] = (slice_shares[k] * assets[k][i] / tot) if tot else 0.0

def xirr(cfs):
    def xnpv(r):
        t0 = cfs[0][0]
        return sum(a / (1 + r) ** ((d - t0).days / 365.0) for d, a in cfs)
    lo, hi = -0.9, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if xnpv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

pdates = [datetime.date.fromisoformat(dates[i]) for i in buy_idx]
cfs = [(d, -3000.0) for d in pdates] + [(datetime.date.fromisoformat(dates[-1]), port_v[-1])]
irr = xirr(cfs)

peak, mdd, mdd_day = -1, 0.0, None
for i in range(N):
    peak = max(peak, port_v[i])
    if peak > 0:
        dd = port_v[i] / peak - 1
        if dd < mdd:
            mdd, mdd_day = dd, dates[i]

finals = {}
for k in KEYS:
    sv = solos[k][0][-1]
    finals[k] = {"solo_value": round(sv), "solo_ret": round(sv / 606000 - 1, 4),
                 "contrib": round(sv * AMT[k] / 3000)}
print("== FINALS (video targets in brackets) ==")
print("portfolio value", round(port_v[-1]), "[2,756,014]  ret", round(port_r[-1] * 100, 2), "[+354.79]  eq ret", round(eq_r[-1] * 100, 1), "[+221.8] eq value", round(eq_v[-1]), "[1,950,077]")
print("IRR", round(irr * 100, 2), "[16.21]  MDD", round(mdd * 100, 1), "[", -28.0, "] on", mdd_day)
for k in KEYS:
    print(f"  {k}: solo {finals[k]['solo_ret']*100:+.1f}% contrib ¥{finals[k]['contrib']:,}")

def anchor(day):
    i = didx[day]
    p = sum(1 for b in buy_idx if b <= i)
    print(f"anchor {day} P{p}: port ret {port_r[i]*100:+.2f}% [+8.52@2010-10-22 / -3.17@2010-08-13] value ¥{port_v[i]:,.0f} solo:",
          {k: round(solo_r[k][i] * 100, 1) for k in KEYS})
anchor("2010-08-13")
anchor("2010-10-22")
anchor("2010-09-16")

EVENTS = [
    ("欧债危机蔓延", "cac", ["cac", "dax"], "2011-07-01", "2011-10-04"),
    ("安倍经济学", "n225", ["n225"], "2012-11-13", "2013-05-22"),
    ("A股杠杆牛市", "csi", ["csi"], "2014-11-20", "2015-06-08"),
    ("股灾·熔断", "csi", ["csi"], "2015-06-08", "2016-01-28"),
    ("新冠疫情·全球暴跌", "qqq", ["qqq", "spy"], "2020-02-19", "2020-03-23"),
    ("无限量宽松·科技狂飙", "qqq", ["qqq"], "2020-03-23", "2021-11-19"),
    ("四十年来最激进加息", "qqq", ["qqq", "n225"], "2022-01-03", "2022-10-13"),
    ("AI浪潮", "qqq", ["qqq"], "2022-12-30", "2024-07-10"),
    ("对等关税冲击", "qqq", ["qqq", "n225"], "2025-02-19", "2025-04-08"),
]
def nearest(day):
    if day in didx:
        return didx[day]
    cand = [d for d in dates if d >= day]
    return didx[cand[0]] if cand else N - 1
ev_out = []
for name, hero, heroes, s, e in EVENTS:
    si, ei = nearest(s), nearest(e)
    row = {"name": name, "hero_idx_change": round(assets[hero][ei] / assets[hero][si] - 1, 4),
           "start": dates[si], "end": dates[ei], "pairs": {}, "pairs_minmax": {}}
    for h in heroes:
        row["pairs"][h] = [round(solo_r[h][si] * 100, 1), round(solo_r[h][ei] * 100, 1)]
        seg = solo_r[h][si:ei + 1]
        row["pairs_minmax"][h] = [round(min(seg) * 100, 1), round(max(seg) * 100, 1)]
    ev_out.append(row)
    print("event", name, "idx", row["hero_idx_change"], "pairs", row["pairs"])

out = {
    "dates": dates, "buy_idx": buy_idx,
    "prices": {k: assets[k] for k in KEYS},
    "solo_ret": {k: [round(x, 4) for x in solo_r[k]] for k in KEYS},
    "port_ret": [round(x, 4) for x in port_r], "port_value": [round(x) for x in port_v],
    "eq_ret": [round(x, 4) for x in eq_r],
    "drift": {k: [round(x, 4) for x in drift[k]] for k in KEYS},
    "invested": [round(x) for x in port_inv],
    "finals": finals,
    "portfolio": {"value": round(port_v[-1]), "ret": round(port_r[-1], 4),
                  "eq_value": round(eq_v[-1]), "eq_ret": round(eq_r[-1], 4),
                  "irr": round(irr, 4), "mdd": round(mdd, 4), "mdd_day": mdd_day},
    "events": ev_out,
}
with open(OUTP, "w") as f:
    json.dump(out, f)
print("engine.json written;", N, "days")

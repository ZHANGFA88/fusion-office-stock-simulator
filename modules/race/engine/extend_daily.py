#!/usr/bin/env python3
"""把已校验的 daily 种子（截至 2026-10-08）按日增量延长到今天。

方法：收益率链式拼接（ratio chaining），绝不重算历史——
对每个资产拉一个覆盖 [种子末日-20 天, 今天] 的 Yahoo 短窗，在窗内按原 fetch_daily.py
的口径重建人民币价（QQQ/SPY/EXS1.DE/CAC.PA 用复权价；日经用 1346.T 收盘+分红自建
全收益，避开 1321.T 坏复权；汇率用收盘），再把每个新交易日的人民币收益率链到种子
末值上。沪深 H00300 全收益从中证官网拉收盘、同样链式拼接。
拼接对缩放免疫：复权口径日后修订也只影响新段收益率，不动历史水平。

代理：读环境变量 RACE_PROXY / HTTPS_PROXY（curl -x），无则直连。
用法：extend_daily.py SEED.json OUT.json   （无新交易日时原样写出并退出 0）
"""
import json, subprocess, sys, os, datetime, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
PROXY = os.environ.get("RACE_PROXY") or os.environ.get("HTTPS_PROXY") or ""


def curl(url, timeout=90):
    cmd = ["curl", "-s", "-H", f"User-Agent: {UA}"]
    if PROXY:
        cmd += ["-x", PROXY]
    cmd.append(url)
    for attempt in range(4):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if out.stdout:
                return out.stdout
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    raise RuntimeError("empty response: " + url[:90])


def yahoo_chart(symbol, p1, p2):
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={p1}&period2={p2}&interval=1d&events=div,split")
    d = json.loads(curl(url))
    r = d["chart"]["result"][0]
    off = r["meta"].get("gmtoffset", 0)
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose")
    close, adjc = {}, {}
    for i in range(len(ts)):
        day = datetime.datetime.utcfromtimestamp(ts[i] + off).strftime("%Y-%m-%d")
        if q["close"][i] is not None:
            close[day] = q["close"][i]
        if adj and adj[i]:
            adjc[day] = adj[i]
    divs = {}
    for v in (r.get("events", {}).get("dividends", {}) or {}).values():
        day = datetime.datetime.utcfromtimestamp(v["date"] + off).strftime("%Y-%m-%d")
        divs[day] = divs.get(day, 0.0) + v["amount"]
    return close, adjc, divs


def tr_series(close, divs):
    """收盘+分红自建全收益（build_tr.py 口径的窗内版，拆股因子在短窗内忽略：
    窗口仅约 1 个月且 1346.T 近期无拆股；若窗内有拆股 close 已被 Yahoo 按拆股调整）。"""
    out, pc, pt = {}, None, None
    for day in sorted(close):
        c = close[day]
        t = c if pc is None else pt * (c + divs.get(day, 0.0)) / pc
        out[day] = t
        pc, pt = c, t
    return out


def csi_closes(years):
    series = {}
    for y in years:
        url = (f"https://www.csindex.com.cn/csindex-home/perf/index-perf?"
               f"indexCode=H00300&startDate={y}0101&endDate={y}1231")
        d = json.loads(curl(url))
        for row in d.get("data", []):
            td = row["tradeDate"]
            if row.get("close"):
                series[f"{td[:4]}-{td[4:6]}-{td[6:]}"] = float(row["close"])
    return series


def filled(series, days):
    out, last = {}, None
    for d in days:
        if series.get(d) is not None:
            last = series[d]
        out[d] = last
    return out


def main():
    seed_path, out_path = sys.argv[1], sys.argv[2]
    D = json.load(open(seed_path))
    dates, assets = D["dates"], D["assets"]
    last = dates[-1]
    last_dt = datetime.date.fromisoformat(last)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    if last_dt >= today:
        print("already up to date:", last)
        json.dump(D, open(out_path, "w"))
        return
    p1 = int(datetime.datetime.combine(last_dt - datetime.timedelta(days=20),
                                       datetime.time.min, datetime.timezone.utc).timestamp())
    p2 = int(datetime.datetime.combine(today + datetime.timedelta(days=1),
                                       datetime.time.min, datetime.timezone.utc).timestamp())

    qqq_c, qqq_a, _ = yahoo_chart("QQQ", p1, p2)
    spy_c, spy_a, _ = yahoo_chart("SPY", p1, p2)
    dax_c, dax_a, _ = yahoo_chart("EXS1.DE", p1, p2)
    cac_c, cac_a, _ = yahoo_chart("CAC.PA", p1, p2)
    n225_c, _n225_a, n225_d = yahoo_chart("1346.T", p1, p2)
    fxu_c, _, _ = yahoo_chart("USDCNY%3DX", p1, p2)
    fxe_c, _, _ = yahoo_chart("EURUSD%3DX", p1, p2)
    fxj_c, _, _ = yahoo_chart("JPY%3DX", p1, p2)
    n225_tr = tr_series(n225_c, n225_d)

    win_days = sorted(set(qqq_c) | set(spy_c) | set(dax_c) | set(cac_c) | set(n225_c))
    if not win_days:
        raise RuntimeError("no window data")
    F = lambda s: filled(s, win_days)
    f_fxu, f_fxe, f_fxj = F(fxu_c), F(fxe_c), F(fxj_c)
    eurcny = {d: (f_fxe[d] * f_fxu[d] if f_fxe[d] and f_fxu[d] else None) for d in win_days}
    cnyjpy = {d: (f_fxj[d] / f_fxu[d] if f_fxj[d] and f_fxu[d] else None) for d in win_days}

    def cny_window(price_series, fx_series, mode):
        out = {}
        fp = filled(price_series, win_days)
        for d in win_days:
            p, fx = fp[d], (fx_series[d] if fx_series else None)
            out[d] = None if p is None or (fx_series and fx is None) else (
                p if fx_series is None else (p * fx if mode == "mul" else p / fx))
        return out

    win_cny = {
        "qqq": cny_window(qqq_a or qqq_c, f_fxu, "mul"),
        "spy": cny_window(spy_a or spy_c, f_fxu, "mul"),
        "dax": cny_window(dax_a or dax_c, eurcny, "mul"),
        "cac": cny_window(cac_a or cac_c, eurcny, "mul"),
        "n225": cny_window(n225_tr, cnyjpy, "div"),
    }
    csi = csi_closes(sorted({last_dt.year, today.year}))
    win_cny["csi"] = {d: csi.get(d) for d in win_days}

    new_days = [d for d in win_days if d > last]
    # CSI 可能有 Yahoo 窗外（更晚）的交易日，补进统一日历
    extra = sorted(d for d in csi if d > last and d not in new_days)
    new_days = sorted(set(new_days) | set(extra))
    if not new_days:
        print("no new trading days after", last)
        json.dump(D, open(out_path, "w"))
        return

    all_win_days = sorted(set(win_days) | set(new_days))
    for key in assets:
        series = win_cny[key]
        if key == "csi":
            series = {d: csi.get(d) for d in all_win_days}
        f = filled(series, all_win_days)
        anchor_day = max(d for d in all_win_days if d <= last)
        prev_cny = f[anchor_day]
        if prev_cny is None:
            raise RuntimeError(f"{key}: no anchor price at {anchor_day}")
        level = assets[key][-1]
        for d in new_days:
            cur = f[d]
            if cur is None:
                raise RuntimeError(f"{key}: missing price on {d}")
            level = level * cur / prev_cny
            assets[key].append(round(level, 4))
            prev_cny = cur
    dates.extend(new_days)
    json.dump(D, open(out_path, "w"))
    print(f"extended: {last} -> {dates[-1]} (+{len(new_days)} days, total {len(dates)})")


if __name__ == "__main__":
    main()

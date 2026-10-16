#!/usr/bin/env python3
"""赛跑活数据刷新：种子/上次活数据 → extend_daily 延长 → engine 重算 → 落盘活引擎。
输出（仓库根 data/，运行时生成，不入库）：
  race-daily-live.json / race-engine-live.json / race-meta.json
基线：优先用上次的 race-daily-live.json 接着延长；没有时用 engine/seed_daily.json。
带单例锁；任何一步失败都保留旧活数据、非零退出。"""
import json, os, subprocess, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DATA = os.path.join(ROOT, "data")
SEED = os.path.join(HERE, "seed_daily.json")
DAILY_LIVE = os.path.join(DATA, "race-daily-live.json")
ENGINE_LIVE = os.path.join(DATA, "race-engine-live.json")
META = os.path.join(DATA, "race-meta.json")
LOCK = os.path.join(DATA, "race-refresh.lock")
PY = sys.executable or "python3"


def locked():
    try:
        pid = int(open(LOCK).read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def main():
    os.makedirs(DATA, exist_ok=True)
    if locked():
        print("another refresh is running; skip")
        return
    open(LOCK, "w").write(str(os.getpid()))
    try:
        base = DAILY_LIVE if os.path.exists(DAILY_LIVE) else SEED
        tmp_daily = DAILY_LIVE + ".tmp"
        r = subprocess.run([PY, os.path.join(HERE, "extend_daily.py"), base, tmp_daily],
                           capture_output=True, text=True, timeout=1200)
        print(r.stdout.strip())
        if r.returncode != 0:
            print(r.stderr[-1500:], file=sys.stderr)
            raise SystemExit("extend_daily failed")
        os.replace(tmp_daily, DAILY_LIVE)
        tmp_engine = ENGINE_LIVE + ".tmp"
        r = subprocess.run([PY, os.path.join(HERE, "engine.py"), DAILY_LIVE, tmp_engine],
                           capture_output=True, text=True, timeout=1200)
        print(r.stdout.strip()[-400:])
        if r.returncode != 0:
            print(r.stderr[-1500:], file=sys.stderr)
            raise SystemExit("engine failed")
        os.replace(tmp_engine, ENGINE_LIVE)
        eng = json.load(open(ENGINE_LIVE))
        meta = {"built_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                "days": len(eng["dates"]), "last_date": eng["dates"][-1],
                "periods": len(eng.get("buy_idx") or []),
                "final_value": (eng.get("portfolio") or {}).get("value"),
                "port_ret": (eng.get("portfolio") or {}).get("ret")}
        json.dump(meta, open(META, "w"), ensure_ascii=False)
        print("race live refreshed:", meta)
    finally:
        try:
            os.unlink(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()

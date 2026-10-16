#!/usr/bin/env python3
"""AI 炒股小队引擎（fusion-office）：办公室八人格组成的模拟炒股流水线。

分工（与办公室首页小人一一对应）：
  CRAWLER 数据抓取 → SOCIAL 新闻舆情 → SCAN 指标扫描评分 → VET 审查否决
  → RISK 风控闸门（报价新鲜度/仓位上限/现金保留/手数）→ CHIEF 拍板
  → FILLS 执行下单（纸面网关）→ JOURNAL 复盘记录。
只走本机 serve.py 的 HTTP 接口下单（/api/trade/order），与手动交易同一网关、
同一套规则（T+1/整数手/验资/无报价拒单）。配置 data/ai-config.json 由
/api/ai/config 写入，本进程轮询生效；状态与动态写 data/ai-status.json 供页面展示。
零第三方依赖；单例锁防重复拉起。
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
PORT = os.environ.get('FINANCE_PORT', '8772')
API = 'http://127.0.0.1:%s' % PORT
CFG_PATH = os.path.join(DATA, 'ai-config.json')
STATUS_PATH = os.path.join(DATA, 'ai-status.json')
LOCK_PATH = os.path.join(DATA, 'ai-crew.lock')

PROFILES = {
    'conservative': {'buy': 5.0, 'sell': -2.5, 'size': 5, 'cap': 12, 'max_orders': 1},
    'balanced':     {'buy': 4.0, 'sell': -3.0, 'size': 8, 'cap': 20, 'max_orders': 2},
    'aggressive':   {'buy': 3.0, 'sell': -3.5, 'size': 12, 'cap': 30, 'max_orders': 3},
}
BULL = ['涨停', '大涨', '利好', '新高', '增持', '回购', '预增', '中标', '突破', '分红']
BEAR = ['大跌', '跌停', '利空', '减持', '亏损', '警示', '处罚', '暴雷', '预减', '退市']

STATE = {'events': [], 'persona_last': {}, 'cycle': 0, 'last_cycle_ts': 0,
         'next_cycle_ts': 0, 'started_ts': int(time.time())}


def http(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'ai-crew'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def http_try(method, path, body=None, timeout=60):
    try:
        return http(method, path, body, timeout)
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8'))
        except Exception:
            return {'ok': False, 'error': 'HTTP %s' % e.code}
    except Exception as e:
        return {'ok': False, 'error': str(e)[:100]}


def read_config():
    cfg = {'enabled': False, 'profile': 'balanced', 'cycle_min': 10}
    try:
        with open(CFG_PATH, encoding='utf-8') as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            cfg.update({k: raw[k] for k in cfg if k in raw})
    except Exception:
        pass
    if cfg['profile'] not in PROFILES:
        cfg['profile'] = 'balanced'
    return cfg


def event(persona, kind, text):
    e = {'ts': int(time.time()), 'persona': persona, 'kind': kind, 'text': text[:240]}
    STATE['events'] = (STATE['events'] + [e])[-80:]
    STATE['persona_last'][persona] = {'ts': e['ts'], 'text': e['text']}
    print('[ai-crew]', persona, text, flush=True)


def write_status(cfg):
    st = {'ts': int(time.time()), 'pid': os.getpid(), 'enabled': bool(cfg['enabled']),
          'profile': cfg['profile'], 'cycle_min': cfg['cycle_min'],
          'cycle': STATE['cycle'], 'last_cycle_ts': STATE['last_cycle_ts'],
          'next_cycle_ts': STATE['next_cycle_ts'],
          'events': STATE['events'][-40:], 'persona_last': STATE['persona_last']}
    tmp = STATUS_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATUS_PATH)


def market_of(sym):
    s = (sym or '').upper()
    if s.endswith('.HK'):
        return 'HK'
    if s.endswith('.SS') or s.endswith('.SZ'):
        return 'CN'
    return 'US'


def score_symbol(ind, quote, senti):
    """SCAN 评分：返回 (score, reasons[(delta,text)...], rsi)。规则全透明。"""
    rs = []
    close = ind.get('close') or (quote or {}).get('price') or 0
    ma = ind.get('ma') or {}
    if ma.get('MA20'):
        rs.append((1, '站上MA20') if close > ma['MA20'] else (-1, '跌破MA20'))
    if ma.get('MA60'):
        rs.append((1, '站上MA60') if close > ma['MA60'] else (-1, '跌破MA60'))
    mc = ind.get('macd') or {}
    if mc.get('gold_cross'):
        rs.append((1, 'MACD金叉'))
    if mc.get('dead_cross'):
        rs.append((-1, 'MACD死叉'))
    if mc.get('trend') == 'up':
        rs.append((1, 'MACD多头'))
    elif mc.get('trend') == 'down':
        rs.append((-1, 'MACD空头'))
    rsi = ind.get('rsi')
    if rsi is not None:
        if 45 <= rsi <= 65:
            rs.append((1, 'RSI健康(%.0f)' % rsi))
        elif rsi > 75:
            rs.append((-2, 'RSI超买(%.0f)' % rsi))
        elif rsi < 30:
            rs.append((0.5, 'RSI超卖(%.0f)' % rsi))
    kd = (ind.get('kdj') or {}).get('signal')
    if kd == 'overbought':
        rs.append((-1, 'KDJ超买'))
    elif kd == 'oversold':
        rs.append((0.5, 'KDJ超卖'))
    vr = ((ind.get('volume') or {}).get('vol_ratio_5'))
    if vr is not None:
        if vr >= 1.3:
            rs.append((1, '放量(量比%.1f)' % vr))
        elif vr <= 0.6:
            rs.append((-0.5, '缩量(量比%.1f)' % vr))
    bp = ((ind.get('boll') or {}).get('pos'))
    if bp is not None:
        if bp >= 1:
            rs.append((-1, '冲出布林上轨'))
        elif bp <= 0:
            rs.append((0.5, '布林下轨'))
    if senti:
        rs.append((senti, '新闻偏多' if senti > 0 else '新闻偏空'))
    return round(sum(d for d, _ in rs), 1), rs, rsi


def run_cycle(cfg):
    prof = PROFILES[cfg['profile']]
    STATE['cycle'] += 1
    STATE['last_cycle_ts'] = int(time.time())
    n = STATE['cycle']
    # ---- CRAWLER：抓universe与报价 ----
    wl = http_try('GET', '/api/finance/watchlist')
    watch = [x['symbol'] for x in (wl.get('items') or [])]
    pk = http_try('GET', '/api/finance/picks')
    picks = [x['symbol'] for x in (pk.get('picks') or [])[:5]]
    universe = list(dict.fromkeys(watch + picks))
    names = {x['symbol']: x.get('name') or x['symbol'] for x in (wl.get('items') or [])}
    for x in (pk.get('picks') or []):
        names.setdefault(x['symbol'], x.get('name') or x['symbol'])
    if not universe:
        event('CRAWLER', 'warn', '第%d轮：自选与精选均为空，跳过' % n)
        return
    qs = http_try('GET', '/api/finance/quotes?symbols=' + ','.join(universe), timeout=120)
    quotes = {x['symbol']: x for x in (qs.get('items') or [])}
    event('CRAWLER', 'info', '第%d轮：抓取自选 %d 只 + 精选 %d 只，报价到手 %d 只'
          % (n, len(watch), len(picks), len(quotes)))
    # ---- SOCIAL：新闻舆情 ----
    senti = {}
    news = http_try('GET', '/api/finance/news?limit=30')
    hits = 0
    for item in (news.get('items') or []):
        title = item.get('title') or ''
        for sym in universe:
            nm = names.get(sym) or ''
            short = nm.replace('-W', '').replace('-SW', '').replace('控股', '')
            if short and len(short) >= 2 and short in title:
                s = sum(1 for w in BULL if w in title) - sum(1 for w in BEAR if w in title)
                if s:
                    senti[sym] = max(-1, min(1, senti.get(sym, 0) + (1 if s > 0 else -1)))
                    hits += 1
                    if hits <= 3:
                        event('SOCIAL', 'info', '「%s」见报：%s → 情绪%+d'
                              % (nm, title[:40], senti[sym]))
    # ---- 账户与持仓 ----
    acc = http_try('GET', '/api/trade/account', timeout=120)
    accounts = {a['market']: a for a in (acc.get('accounts') or [])}
    pos = http_try('GET', '/api/trade/positions', timeout=120)
    positions = {p['symbol']: p for p in (pos.get('positions') or [])}
    # ---- SCAN + VET：逐只评分 ----
    scored = []
    for sym in universe:
        an = http_try('GET', '/api/finance/analyze?symbol=' + sym, timeout=60)
        ind = an.get('indicators') or {}
        if not an.get('ok') or not ind.get('ok'):
            continue
        sc, rs, rsi = score_symbol(ind, quotes.get(sym), senti.get(sym, 0))
        scored.append({'symbol': sym, 'name': names.get(sym) or sym,
                       'market': market_of(sym), 'score': sc, 'reasons': rs,
                       'rsi': rsi, 'quote': quotes.get(sym)})
    if scored:
        top = max(scored, key=lambda x: x['score'])
        event('SCAN', 'info', '扫描 %d 只：最高分 %.1f（%s %s），最低分 %.1f'
              % (len(scored), top['score'], top['name'], top['symbol'],
                 min(x['score'] for x in scored)))
    orders_done = 0
    now = int(time.time())

    def risk_gate_buy(c):
        """RISK 买入闸门：返回 (qty, None) 或 (0, 否决原因)。"""
        q = c['quote']
        if not q or not q.get('price'):
            return 0, '无报价'
        if q.get('stale'):
            return 0, '报价为缓存旧价（stale），不用旧价下单'
        if q.get('ts') and now - int(q['ts']) > 26 * 3600:
            return 0, '报价超过 26 小时未更新'
        a = accounts.get(c['market'])
        if not a:
            return 0, '账户缺失'
        equity, cash, price = a['equity'], a['cash'], float(q['price'])
        cur_val = (positions.get(c['symbol']) or {}).get('market_value') or 0
        if cur_val >= equity * prof['cap'] / 100:
            return 0, '该股市值已达仓位上限 %d%%' % prof['cap']
        # 目标仓位是「总市值」口径：只买到目标的差额，避免每轮对同一只票重复加仓
        target_total = equity * prof['size'] / 100
        room = target_total - cur_val
        lot_val = price * (100 if c['market'] == 'CN' else 1)
        if room < lot_val:
            return 0, '已达目标仓位（市值 %.0f / 目标 %.0f）' % (cur_val, target_total)
        budget = min(room, cash * 0.85, equity * prof['cap'] / 100 - cur_val)
        qty = int(budget // price)
        if c['market'] == 'CN':
            qty = qty // 100 * 100
        if qty <= 0:
            return 0, '可用资金不足一手（现金 %.0f）' % cash
        return qty, None

    # ---- CHIEF 卖出决策（先卖后买） ----
    for c in sorted(scored, key=lambda x: x['score']):
        if orders_done >= prof['max_orders']:
            break
        p = positions.get(c['symbol'])
        if not p or (p.get('sellable_qty') or 0) <= 0:
            continue
        stop = (p.get('unrealized_pnl_pct') is not None and p['unrealized_pnl_pct'] <= -8)
        if c['score'] <= prof['sell'] or stop:
            why = ('止损：浮亏 %.1f%%' % p['unrealized_pnl_pct']) if stop else \
                  ('评分 %.1f ≤ 卖出线 %.1f' % (c['score'], prof['sell']))
            note = 'AI·CHIEF %s：%s' % (why, '、'.join(t for _, t in c['reasons'][:3]))
            r = http_try('POST', '/api/trade/order',
                         {'market': c['market'], 'symbol': c['symbol'], 'side': 'sell',
                          'qty': int(p['sellable_qty']), 'type': 'market', 'note': note},
                         timeout=120)
            if r.get('ok'):
                event('FILLS', 'sell', '卖出 %s %s %d 股 @%s（%s）'
                      % (c['name'], c['symbol'], int(p['sellable_qty']),
                         r.get('fill_price'), why))
                orders_done += 1
            else:
                event('RISK', 'veto', '卖出 %s 被网关拒绝：%s'
                      % (c['symbol'], r.get('error') or r))
    # ---- CHIEF 买入决策 ----
    for c in sorted(scored, key=lambda x: -x['score']):
        if orders_done >= prof['max_orders']:
            break
        if c['score'] < prof['buy']:
            continue
        if c['rsi'] is not None and c['rsi'] > 78:
            event('VET', 'veto', '否决买入 %s：RSI %.0f 过热，评分再高也不追'
                  % (c['symbol'], c['rsi']))
            continue
        qty, veto = risk_gate_buy(c)
        if veto:
            event('RISK', 'veto', '否决买入 %s %s：%s' % (c['name'], c['symbol'], veto))
            continue
        top_rs = '、'.join(t for _, t in sorted(c['reasons'], key=lambda x: -abs(x[0]))[:3])
        note = 'AI·CHIEF 评分 %.1f：%s' % (c['score'], top_rs)
        r = http_try('POST', '/api/trade/order',
                     {'market': c['market'], 'symbol': c['symbol'], 'side': 'buy',
                      'qty': qty, 'type': 'market', 'note': note}, timeout=120)
        if r.get('ok'):
            event('FILLS', 'buy', '买入 %s %s %d 股 @%s，费用 %s（%s）'
                  % (c['name'], c['symbol'], qty, r.get('fill_price'),
                     r.get('fee'), note))
            orders_done += 1
        else:
            event('RISK', 'veto', '买入 %s 被网关拒绝：%s'
                  % (c['symbol'], r.get('error') or r))
    # ---- JOURNAL 复盘 ----
    eq = ' · '.join('%s %s' % (m, format(int(a['equity']), ','))
                    for m, a in accounts.items())
    event('JOURNAL', 'info', '第%d轮收工：成交 %d 笔；权益 %s' % (n, orders_done, eq))


def acquire_lock():
    if os.path.exists(LOCK_PATH):
        try:
            pid = int(open(LOCK_PATH).read().strip())
            os.kill(pid, 0)
            print('[ai-crew] already running pid', pid, flush=True)
            sys.exit(0)
        except Exception:
            pass
    with open(LOCK_PATH, 'w') as f:
        f.write(str(os.getpid()))


def main():
    os.makedirs(DATA, exist_ok=True)
    acquire_lock()
    print('[ai-crew] up, api =', API, flush=True)
    while True:
        cfg = read_config()
        try:
            if cfg['enabled'] and int(time.time()) >= STATE['next_cycle_ts']:
                run_cycle(cfg)
                STATE['next_cycle_ts'] = int(time.time()) + cfg['cycle_min'] * 60
            elif cfg['enabled'] and STATE['next_cycle_ts'] == 0:
                STATE['next_cycle_ts'] = int(time.time())
        except Exception as e:
            event('JOURNAL', 'warn', '本轮异常：%s' % str(e)[:120])
            STATE['next_cycle_ts'] = int(time.time()) + cfg['cycle_min'] * 60
        write_status(cfg)
        time.sleep(15)


if __name__ == '__main__':
    main()

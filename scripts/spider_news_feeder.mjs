#!/usr/bin/env node
/**
 * Fusion Office · 蜘蛛新闻喂入器 v2（2026-10-16，word 模式）
 * 蜘蛛的看家本领是「逐词模式」：它沿着一段文字爬行、伸触手抓词、把词甩进蛛网分流——
 * 演示模式就是这么演的。v1 用任务模式推标题只有列表、没有这套互动，故 v2 改走逐词模式：
 * 每批新快讯拼成一页「提示词文本」（中文经 Intl.Segmenter 分词），逐词以约 8 词/秒滴灌进会话，
 * 每个词按关键词归类到三分支（市场动态 code / 公司行业 model / 宏观政策 you），
 * 点任意词可看它所属的快讯标题、时间与原文链接。一页织完且有新快讯排队时翻页开新会话。
 * 会话地址（带令牌）写 data/spider-live.json，供 /api/fusion/spider-live 与蜘蛛分室消费。
 * 零第三方依赖（蜘蛛自带 lib + Node 内置模块），不需要 npm ci、不需要 MCP SDK。
 */
import path from 'node:path';
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { SessionStore } from '../modules/spider/lib/session-store.js';
import { normalizeConfig } from '../modules/spider/lib/config.js';
import { tokenizePrompt } from '../modules/spider/lib/config.js';
import { createPreviewHub } from '../modules/spider/mcp/hub.mjs';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const SPIDER_ROOT = path.join(ROOT, 'modules', 'spider');
const DATA_DIR = path.join(ROOT, 'data');
const LIVE_FILE = path.join(DATA_DIR, 'spider-live.json');
const SEEN_FILE = path.join(DATA_DIR, 'spider-seen.json');
if (process.env.ENABLE_DEMO_SPIDER_FEEDER !== '1') {
  console.log('[spider-feeder] disabled; set ENABLE_DEMO_SPIDER_FEEDER=1 to opt in to the local synthetic demo');
  process.exit(0);
}
const PORT = Number(process.env.FINANCE_PORT || '8772');
if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65535) {
  console.error('[spider-feeder] invalid FINANCE_PORT; refusing to start');
  process.exit(2);
}
// Always use the loopback DEMO endpoint; never accept an externally supplied URL.
const DEMO_URL = new URL('/api/finance/observatory', `http://127.0.0.1:${PORT}`).href;
const POLL_MS = Number(process.env.SPIDER_POLL_MS || 45000);
const DRIP_MS = 1200;          // 滴灌节拍
const DRIP_N = 10;             // 每拍词数（≈8.3 词/秒，与演示节奏同量级）
const PAGE_MIN_FLIP = 3;       // 排队 ≥3 条就翻页
const PAGE_WAIT_MS = 30000;    // 页织完后有排队则最多等 30 秒翻页
const FIRST_PAGE = 20;         // 首轮取最近 20 条快讯成页（页大一些，三分支都更容易有关键词命中）

fs.mkdirSync(DATA_DIR, { recursive: true });

// 单例锁：防止 run.sh 重复拉起多个喂入器抢写 spider-live.json
const LOCK_FILE = path.join(DATA_DIR, 'spider-feeder.lock');
try {
  const oldPid = Number(fs.readFileSync(LOCK_FILE, 'utf8'));
  if (oldPid && oldPid !== process.pid) { try { process.kill(oldPid, 0); console.error(`[spider-feeder] already running (pid ${oldPid}), exiting`); process.exit(0); } catch { /* 旧进程已死，接管 */ } }
} catch { /* 无锁文件 */ }
fs.writeFileSync(LOCK_FILE, String(process.pid));
process.on('exit', () => { try { if (fs.readFileSync(LOCK_FILE, 'utf8') === String(process.pid)) fs.unlinkSync(LOCK_FILE); } catch { /* 忽略 */ } });

const store = new SessionStore();
const hub = createPreviewHub({ store, root: SPIDER_ROOT });
const segmenter = typeof Intl.Segmenter === 'function'
  ? new Intl.Segmenter('zh', { granularity: 'word' }) : null;

const MACRO_KW = ['央行','国务院','发改委','证监会','银保监','财政部','工信部','统计局','商务部','海关总署','外交部','利率','降准','降息','加息','政策','监管','新规','办法','意见','规划','GDP','CPI','PPI','社融','国债','专项债','发布会'];
const MARKET_KW = ['指数','收盘','开盘','涨停','跌停','涨幅','跌幅','上涨','下跌','大涨','暴跌','收涨','收跌','拉升','跳水','反弹','回调','新高','新低','成交额','成交量','沪指','深成指','创业板','科创','板块','ETF','期货','期指','原油','黄金','白银','铜价','汇率','人民币','美元','日元','欧元','比特币','以太坊','港股','美股','A股','大盘','北向','南向','主力','资金净','股价','市值蒸发'];
const ROUTE_NAME = { code: '市场动态', model: '公司行业', you: '宏观政策' };
/** 在整条标题里检出全部关键词（标题级匹配，不受分词切分影响）。 */
function detectKeywords(title) {
  const hits = [];
  for (const kw of MACRO_KW) if (title.includes(kw)) hits.push({ kw, route: 'you' });
  for (const kw of MARKET_KW) if (title.includes(kw)) hits.push({ kw, route: 'code' });
  return hits;
}
/** 逐词定分支：词与某个检出关键词互相包含 → 该关键词的分支（关键词单独跳出）；其余词默认公司行业。
 *  单字词不做反向包含匹配（防「会」命中「发布会」、「人」命中「人民币」这类假阳性）。 */
function routeWord(word, hits) {
  let best = null;
  for (const h of hits) {
    const hit = word.includes(h.kw) || (word.length >= 2 && h.kw.includes(word));
    if (hit && (!best || h.kw.length > best.kw.length)) best = h;
  }
  return best ? { route: best.route, keyword: best.kw } : { route: 'model', keyword: null };
}
const eid = item => 'n' + createHash('sha1').update(item.url || `${item.title || ''}|${item.time || item.event_ts || ''}|${item.source || ''}`).digest('hex').slice(0, 16);

function segmentWords(title) {
  if (!segmenter) return title.split(/\s+/).filter(Boolean);
  const out = [];
  for (const part of segmenter.segment(title)) {
    const w = part.segment.trim();
    if (!w) continue;
    if (part.isWordLike || /[\dA-Za-z%]/.test(w)) out.push(w);
  }
  return out.length ? out : [title];
}

let seen = new Set();
try { seen = new Set(JSON.parse(fs.readFileSync(SEEN_FILE, 'utf8'))); } catch { /* 首次运行 */ }
function persistSeen() {
  try { fs.writeFileSync(SEEN_FILE, JSON.stringify([...seen].slice(-3000))); } catch { /* 不致命 */ }
}

const queue = [];        // 待成页的快讯（时间顺序）
let page = null;         // 当前页 {id, tokens:[{word,route,detail}], pushed, doneAt}
const oldSessions = [];  // 待关闭的旧会话（保留最近 1 个）

function writeLive() {
  if (!page) return;
  const payload = { ok: true, url: hub.previewUrl(page.id), session_id: page.id, updated: Date.now() };
  const tmp = LIVE_FILE + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(payload));
  fs.renameSync(tmp, LIVE_FILE);
}

function openPage(headlines) {
  const lines = [], tokens = [];
  for (const h of headlines) {
    const words = segmentWords(h.title);
    lines.push(words.join(' '));
    const hits = detectKeywords(h.title);
    for (const w of words) {
      const { route, keyword } = routeWord(w, hits);
      const why = keyword ? `分流依据：关键词「${keyword}」→ ${ROUTE_NAME[route]}` : `分流依据：普通词 → ${ROUTE_NAME.model}（默认）`;
      const link = h.url ? `原文链接：${h.url}` : 'DEMO fixture：无外部原文链接';
      const detail = `所属快讯：${h.title}\n${why}\n${h.time || h.event_ts || ''} · 财经快讯 · ${h.data_mode || 'DEMO'}\n${link}`;
      tokens.push({ word: w, route, detail });
    }
  }
  const prompt = lines.join('\n');
  const total = tokenizePrompt(prompt).tokens.length;
  const config = normalizeConfig({
    title: '财经快讯 · DEMO 演示',
    subtitle: '仅使用本地合成 DEMO fixture；关键词跳进对应分支——政策词→宏观政策、涨跌词→市场动态，其余词→公司行业',
    modelName: 'Fusion News Feeder',
    layout: 'landscape',
    unit: 'word',
    prompt,
    expectedTotal: total,
    routes: {
      code: { label: '市场动态' },
      model: { label: '公司行业' },
      you: { label: '宏观政策' },
    },
  });
  const snap = store.create(config);
  if (page) { oldSessions.push(page.id); while (oldSessions.length > 1) { try { store.close(oldSessions.shift()); } catch { /* 已关 */ } } }
  page = { id: snap.id, tokens, pushed: 0, doneAt: 0 };
  writeLive();
  console.log(`[spider-feeder] page opened: ${headlines.length} headlines, ${total} words, session ${page.id}`);
}

function drip() {
  if (!page) return;
  if (page.pushed < page.tokens.length) {
    const slice = page.tokens.slice(page.pushed, page.pushed + DRIP_N);
    const events = slice.map((t, i) => ({
      id: `w-${page.id.slice(0, 8)}-${page.pushed + i}`,
      label: t.word,
      route: t.route,
      status: 'completed',
      tokenIndex: page.pushed + i,
      detail: t.detail,
    }));
    try {
      store.updateEvents(page.id, events);
      page.pushed += slice.length;
      if (page.pushed >= page.tokens.length) { page.doneAt = Date.now(); console.log('[spider-feeder] page fully woven'); }
    } catch (err) { console.error('[spider-feeder] drip failed:', err.message); }
    return;
  }
  // 页已织完：有排队且（数量够或等待够久）就翻页
  if (queue.length >= PAGE_MIN_FLIP || (queue.length > 0 && Date.now() - page.doneAt > PAGE_WAIT_MS)) {
    const next = queue.splice(0, queue.length);
    try { store.complete(page.id, 'completed'); } catch { /* 忽略 */ }
    openPage(next);
  }
}

async function poll(first = false) {
  try {
    const resp = await fetch(DEMO_URL, { signal: AbortSignal.timeout(20000) });
    const data = await resp.json();
    if (data.domain !== 'finance' || data.data_mode !== 'DEMO' || data.demo !== true) {
      throw new Error('refusing non-DEMO observatory response');
    }
    const items = Array.isArray(data.news) ? data.news : [];
    const fresh = items.filter(it => it && it.kind === 'news' && it.title && it.data_mode === 'DEMO' && !seen.has(eid(it)));
    fresh.reverse(); // 接口新→旧，反转为时间顺序
    for (const it of fresh) { seen.add(eid(it)); queue.push(it); }
    if (fresh.length) { persistSeen(); console.log(`[spider-feeder] queued ${fresh.length} headlines (queue=${queue.length})`); }
  } catch (err) {
    console.error('[spider-feeder] poll failed:', err.message);
  }
  if (first && !page && queue.length) openPage(queue.splice(0, FIRST_PAGE));
  else if (!page && queue.length >= PAGE_MIN_FLIP) openPage(queue.splice(0, queue.length));
  writeLive();
}

const origin = await hub.start();
console.log(`[spider-feeder] hub at ${origin} · local DEMO=${DEMO_URL} · poll=${POLL_MS}ms · word mode`);
await poll(true);
setInterval(() => { void poll(false); }, POLL_MS);
setInterval(drip, DRIP_MS);

for (const sig of ['SIGINT', 'SIGTERM']) {
  process.once(sig, async () => { try { await hub.close(); } finally { process.exit(0); } });
}

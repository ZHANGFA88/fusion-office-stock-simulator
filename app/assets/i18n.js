/* Fusion Office 双语包 + 葡语日志模板翻译层
 * 界面字符串：I18N[lang]；数据日志：trLog(msg, lang) 按引擎固定句式翻译，未覆盖回退原文并加标记。 */
(function(){
'use strict';
const I18N={
zh:{title:'GROKBOT + JEV — 交易办公室 (PAPER)',sub:'DEGEN 实验室 · SOLANA MEME 币 · 桌面精选',paper:'📄 PAPER · 虚拟资金',
 nav_office:'办公室',nav_trade:'交易台',nav_markets:'行情',nav_race:'赛跑',nav_spider:'蜘蛛',nav_observatory:'财经观测台',nav_settings:'设置',
 saldo:'余额',jev:'JEV · 分析',thr_note:'— 桌面规则（引擎）',fail_closed:'所有否决一律失败关闭',
 th_autopre:'自动预审',th_veto:'否决钥匙',day:'日',live:'实况',delayed:'延迟',nodata:'无数据',cb:'熔断',cb_ok:'熔断正常',clk_suffix:' 里斯本',gates:['RugCheck 报告','X 主页','VET 只放 GOOD','真实官网','CA 已确认','无付费热度','流动性 ≥ $40k','量 < 10× 流动性','交易对 ≥ 30 分钟','boosts < 30'],
 st_chief:'从不无共识开仓',st_scan:'寻找异动',st_vet:'链上风险，是否真实？',st_social:'项目的 X 账号',st_risk:'下多少、进不进',st_fills:'执行（纸面）',st_journal:'记录每一张工单',st_crawler:'先读完，再由桌面决定',
 m_bal:'虚拟余额',m_seen:'已见代币',m_good:'GOOD / 已评',m_hype:'平均热度（模拟）',m_closed:'已平仓',m_fill:'最近成交',m_tickets:'工单',m_profiles:'项目 / 交易对',
 tv_bal:'虚拟余额 (PAPER)',cb_pause:'暂停 — 熔断',connecting:'正在连接引擎…',
 crawler_lines:['追踪气味中…','闻到鲸鱼味了 🐋','又读完一个项目'],scan_lines:['新猎物：','雷达响了：','发现目标：'],
 crawler_read:n=>('读了 '+(n||'—')+' 个项目，交给桌上决定'),
 vet_rug:' 闻着像 rug',vet_good:' 链上干净',sim_suffix:'（模拟）',risk_no:'过不了：',
 why_norc:'无 RugCheck',why_nox:'无 X',why_nosite:'无官网',why_lowliq:'流动性低',why_baby:'新对',why_rules:'规则',
 chief_cb:'熔断开着，谁也不许进',risk_watch:' 持仓中，盯着呢',journal_reg:'正在登记',
 bal:'余额',analyzed:' 已分析',p_price:'价格',p_liq:'流动性',p_age:'年龄',ach_cap:'最近代币 24h 涨跌',spider_cap:'否决蛛网（真实计数）',
 pills_note:' — 顺序就是关键：',hard_note:'算术，不花钱',vet_note:'CHIEF 的否决项',yes:'是',no:'否',
 stocks:'股票交易员',stocks_total:'总市值',stocks_today:'今日盈亏',stocks_last:'最近工单',stocks_none:'暂无工单',stocks_paper:'模拟盘'},
en:{title:'GROKBOT + JEV — Trading Office (PAPER)',sub:'DEGEN LAB · SOLANA MEMECOINS · DESK PICKS',paper:'📄 PAPER · VIRTUAL MONEY',
 nav_office:'Office',nav_trade:'Trade Desk',nav_markets:'Markets',nav_race:'Race',nav_spider:'Spider',nav_observatory:'Observatory',nav_settings:'Settings',
 saldo:'BALANCE',jev:'JEV · ANALYSIS',thr_note:'— desk rules (engine)',fail_closed:'EVERY VETO FAILS CLOSED',
 th_autopre:'AUTO-PRE',th_veto:'VETO KEYS',day:'DAY',live:'LIVE',delayed:'DELAYED',nodata:'NO DATA',cb:'CIRCUIT BREAKER',cb_ok:'breaker OK',clk_suffix:' PT',gates:['RugCheck report','X profile','VET GOOD only','real website','CA confirmed','no paid hype','liquidity ≥ $40k','vol < 10× liq','pair ≥ 30 min','boosts < 30'],
 st_chief:'never opens without consensus',st_scan:'hunting what moves',st_vet:'on-chain risk, is it real?',st_social:"the project's X account",st_risk:'how much, and whether in',st_fills:'execution (paper)',st_journal:'logs every ticket',st_crawler:'reads first, desk decides after',
 m_bal:'virtual balance',m_seen:'tokens seen',m_good:'GOOD / rated',m_hype:'avg hype (simulated)',m_closed:'closed',m_fill:'last fill',m_tickets:'tickets',m_profiles:'profiles / pairs',
 tv_bal:'VIRTUAL BALANCE (PAPER)',cb_pause:'PAUSED — breaker',connecting:'connecting to engine…',
 crawler_lines:['following the scent…','smells like a whale 🐋','another profile read'],scan_lines:['new critter: ','radar pinged: ','land in sight: '],
 crawler_read:n=>('read '+(n||'—')+' profiles, the desk decides'),
 vet_rug:' smells like a rug',vet_good:' clean on-chain',sim_suffix:' (simulated)',risk_no:'no pass: ',
 why_norc:'no RugCheck',why_nox:'no X',why_nosite:'no site',why_lowliq:'low liq',why_baby:'baby pair',why_rules:'rules',
 chief_cb:'breaker on, nobody gets in',risk_watch:' open, watching it',journal_reg:'logging',
 bal:'BALANCE',analyzed:' ANALYZED',p_price:'Price',p_liq:'Liquidity',p_age:'Age',ach_cap:'24h change of latest tokens',spider_cap:'veto web (real counts)',
 pills_note:' — order is the point: ',hard_note:'arithmetic, costs nothing',vet_note:"CHIEF's vetoes",yes:'yes',no:'no',
 stocks:'STOCKS TRADER',stocks_total:'Total value',stocks_today:'Today P&L',stocks_last:'Last order',stocks_none:'no orders yet',stocks_paper:'PAPER'}};
/* ---------- 葡语日志模板翻译 ---------- */
function frag(s,zh){ // 片段级替换（VET 报告尾巴、测试理由共用）
 const R=zh?[
  [/VET on-chain \(RugCheck\): sem problemas detetados/g,'链上验真（RugCheck）：未发现问题'],
  [/VET on-chain parcial \(RPC\): só RPC \(sem dados de holders\)/g,'链上验真（部分·RPC）：仅 RPC（无持有人数据）'],
  [/VET on-chain \(RugCheck\):/g,'链上验真（RugCheck）：'],
  [/heurístico \(fallback\)/g,'启发式（兜底）'],[/heur (GOOD|WARN|RUG) (\d+)\/100/g,'启发式 $1 $2/100'],
  [/aviso:/g,'警告：'],[/perigo:/g,'危险：'],[/maior holder/g,'最大持有人'],[/\(liquidez\/idade\)/g,'（流动性/年龄）'],
  [/só RPC \(sem dados de holders\)/g,'仅 RPC（无持有人数据）'],[/não corresponde ao aviso suave único/g,'与唯一软警告不符'],
  [/com (\d+) motivos/g,'共 $1 个理由'],[/auto-vet (GOOD|WARN|RUG)/g,'自动验真 $1']
 ]:[
  [/VET on-chain \(RugCheck\): sem problemas detetados/g,'on-chain vet (RugCheck): no issues found'],
  [/VET on-chain parcial \(RPC\): só RPC \(sem dados de holders\)/g,'on-chain vet (partial RPC): RPC only (no holder data)'],
  [/VET on-chain \(RugCheck\):/g,'on-chain vet (RugCheck):'],
  [/heurístico \(fallback\)/g,'heuristic (fallback)'],[/heur (GOOD|WARN|RUG) (\d+)\/100/g,'heur $1 $2/100'],
  [/aviso:/g,'warning:'],[/perigo:/g,'danger:'],[/maior holder/g,'top holder'],[/\(liquidez\/idade\)/g,'(liquidity/age)'],
  [/só RPC \(sem dados de holders\)/g,'RPC only (no holder data)'],[/não corresponde ao aviso suave único/g,'does not match the single soft warning'],
  [/com (\d+) motivos/g,'with $1 reasons'],[/auto-vet (GOOD|WARN|RUG)/g,'auto-vet $1']
 ];
 R.forEach(([a,b])=>{s=s.replace(a,b)});return s}
function reason(r,zh){ // BLOQUEADO 的单条理由
 r=r.replace(/^veto: /,''); let m;
 if(m=r.match(/^sem relatório RugCheck(?: \(RugCheck: HTTP (\d+)\))?$/))return zh?('无 RugCheck 报告'+(m[1]?'（HTTP '+m[1]+'）':'')):('no RugCheck report'+(m[1]?' (HTTP '+m[1]+')':''));
 if(m=r.match(/^VET (RUG|WARN) \(só GOOD limpo entra\)$/))return zh?('VET 为 '+m[1]+'（只放行干净的 GOOD）'):('VET '+m[1]+' (only clean GOOD passes)');
 if(r==='sem website')return zh?'无官网':'no website';
 if(m=r.match(/^par sem perfil X nas socials(?: \(link não é perfil: (\S+?)\))?$/))return zh?('交易对无 X 主页'+(m[1]?'（链接非主页）':'')):('pair has no X profile'+(m[1]?' (link is not a profile)':''));
 if(r==='CA não confirmado')return zh?'合约地址未确认':'CA unconfirmed';
 if(m=r.match(/^liquidez (\S+) < (\S+)$/))return (zh?'流动性 ':'liquidity ')+m[1]+' < '+m[2];
 if(m=r.match(/^vol24h (\S+) ≥ (\S+) liq$/))return (zh?'24h 量 ':'24h vol ')+m[1]+' ≥ '+m[2]+(zh?'×流动性':'× liq');
 if(m=r.match(/^injeção: salto visto em (\d+)\/(\d+) leituras seguidas$/))return zh?('注入嫌疑：连续 '+m[2]+' 次读数中 '+m[1]+' 次跳变'):('injection: jump in '+m[1]+'/'+m[2]+' straight reads');
 if(m=r.match(/^injeção: liquidez (\S+) < (\S+)$/))return zh?('注入嫌疑：流动性 '+m[1]+' < '+m[2]):('injection: liquidity '+m[1]+' < '+m[2]);
 if(m=r.match(/^lançamento: liquidez (\S+) < (\S+)$/))return zh?('发射期：流动性 '+m[1]+' < '+m[2]):('launch: liquidity '+m[1]+' < '+m[2]);
 if(m=r.match(/^injeção: (.*)$/))return (zh?'注入嫌疑：':'injection: ')+frag(m[1],zh);
 if(m=r.match(/^criador com histórico de rug \(risk: (.+)\)$/))return zh?('创建者有 rug 前科（'+m[1]+'）'):('creator has rug history (risk: '+m[1]+')');
 if(m=r.match(/^holder \(não-pool\) com (\S+) > (\S+)$/))return zh?('单一持有人（非池）'+m[1]+' > '+m[2]):('single holder (non-pool) '+m[1]+' > '+m[2]);
 if(m=r.match(/^(\d+) insiders ligados > (\d+)$/))return zh?('关联内幕地址 '+m[1]+' 个 > '+m[2]):(m[1]+' linked insiders > '+m[2]);
 if(m=r.match(/^lançamento: (\d+) insiders > (\d+)$/))return zh?('发射期：内幕地址 '+m[1]+' 个 > '+m[2]):('launch: '+m[1]+' insiders > '+m[2]);
 if(m=r.match(/^teste: (.*)$/))return (zh?'测试：':'test: ')+frag(m[1],zh);
 return frag(r,zh)}
function trLog(msg,lang){ if(!msg||msg==='—'||msg.length<2)return msg; const zh=lang!=='en'; let m;
 if(m=msg.match(/^DexScreener: (\d+) perfis Solana, (\d+) pares com preço/))return zh?('DexScreener：'+m[1]+' 个 Solana 项目，'+m[2]+' 个有报价的交易对'):('DexScreener: '+m[1]+' Solana profiles, '+m[2]+' priced pairs');
 if(m=msg.match(/^Encontrou (\$\S+) — vol (\S+), liq (\S+), (\S+)/))return zh?('发现 '+m[1]+' — 量 '+m[2]+'，流动性 '+m[3]+'，'+m[4]):('Found '+m[1]+' — vol '+m[2]+', liq '+m[3]+', '+m[4]);
 if(m=msg.match(/^(\$\S+): sentimento (\d+)\/100 — SIMULADO, não usado/))return zh?(m[1]+'：情绪 '+m[2]+'/100 — 模拟值，未采用'):(m[1]+': sentiment '+m[2]+'/100 — SIMULATED, unused');
 if(m=msg.match(/^(\$\S+): (GOOD|WARN|RUG) \[fonte: auto RugCheck\+heurístico\] — (.*)$/))return zh?(m[1]+'：'+m[2]+'［来源：自动 RugCheck+启发式］— '+frag(m[3],1)):(m[1]+': '+m[2]+' [source: auto RugCheck+heuristic] — '+frag(m[3],0));
 if(m=msg.match(/^(\$\S+) (TESTE )?BLOQUEADO[^:]*: (?:regras da mesa: )?(.*)$/)){const parts=[];let dep=0,cur='';for(let i=0;i<m[3].length;i++){const ch=m[3][i];if(ch==='(')dep++;if(ch===')')dep--;if(ch===';'&&dep===0){parts.push(cur);cur='';if(m[3][i+1]===' ')i++}else cur+=ch}if(cur)parts.push(cur);const rs=parts.map(x=>reason(x.trim(),zh)).join(zh?'；':'; ');return zh?(m[1]+' 被拦'+(m[2]?'（测试）':'')+'：桌面规则：'+rs):(m[1]+' BLOCKED'+(m[2]?' (TEST)':'')+': desk rules: '+rs)}
 if(m=msg.match(/^APROVADO (\$\S+): tamanho (\S+), risco (\S+) \(([^)]+)\)(.*)$/))return zh?('批准 '+m[1]+'：仓位 '+m[2]+'，风险 '+m[3]+'（'+m[4]+'）'+(m[5]?' · VET WARN → 仓位减半':'')):('APPROVED '+m[1]+': size '+m[2]+', risk '+m[3]+' ('+m[4]+')'+(m[5]?' · VET WARN → size halved':''));
 if(m=msg.match(/^Resumo: (\d+) abertas, saldo virtual (\S+) — dados ao vivo/))return zh?('摘要：'+m[1]+' 笔持仓，虚拟余额 '+m[2]+' — 实时数据'):('Summary: '+m[1]+' open, virtual balance '+m[2]+' — live data');
 if(m=msg.match(/^Relatório noturno (\S+) escrito \((\d+) trades, P&L (\S+)\)/))return zh?('夜间报告 '+m[1]+' 已生成（'+m[2]+' 笔交易，盈亏 '+m[3]+'）'):('Night report '+m[1]+' written ('+m[2]+' trades, P&L '+m[3]+')');
 if(/^TICKET PAPER/.test(msg)){let s=msg.replace('TICKET PAPER TESTE',zh?'纸面工单·测试':'PAPER TICKET TEST').replace(/\(aviso: ([^)]+)\)/g,zh?'（注意：$1）':'(warning: $1)').replace(/ hoje\b/g,zh?' 今日':' today').replace(/teste (\d+)\/(\d+)/g,zh?'测试 $1/$2':'test $1/$2');return s}
 if(msg==='auto (RugCheck + heurístico)')return zh?'自动（RugCheck + 启发式）':'auto (RugCheck + heuristic)';
 return msg+(zh?' [原文]':' [orig.]')}
window.I18N=I18N;window.trLog=trLog;
})();

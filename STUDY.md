# grokbot-jev-dashboard 研读笔记（2026-10-16 通读原仓全部文件）

来源：github.com/Vazolini/grokbot-jev-dashboard（无 LICENSE）。文件全集仅 4 个：
index.html（35,519 字节，全部应用）、decisions.js（826 字节）、office.html（384 字节，重定向页）、assets/office-bg.jpg（416,902 字节，办公室插画）。数据在 `data` 分支 state.json。

## 一句话本质
不是「仪表盘」，是一个**虚拟交易办公室**：8 个机器人格在一间像素办公室里上班，真实交易引擎（state.json）驱动他们走动、说话、否决、成交——数据是真的，表演也是真的。

## 视觉系统
- 背景：玫红 #e04aa6 + 6px 白色网格线（纸面感）；正文等宽字体 ui-monospace/JetBrains Mono，标题 system-ui 900 粗体。
- 色板：--pk #ff4fb4（主粉）、--bg #0c0c10、--card #111116、--ln #24242c、--txt #ececf1、--mut #8a8a99、--g #3dff8f、--r #ff4d5e、--y #ffd23f、--c #3fd5ff、--o #ff9a3c。
- 卡片：黑底、7px 圆角、0 2px 0 硬阴影（无模糊、无玻璃）——和 V1 钛金风完全相反的「像素街机」语言。

## 布局（全窗自适应网格，设计稿 1180px）
grid-template-areas："hd hd hd" / "cl of cr" / "bt bt bt"
- 顶栏：像素 logo（粉圆双眼 SVG）+ GROKBOT + JEV + 副标「DEGEN LAB · MEMECOINS SOL · ESCOLHAS DO DESK」+ 📄 PAPER 徽章 + 日期/实况 + 里斯本时钟（黑盒白字）。
- 左列 cardsL / 右列 cardsR：各 4 张机器人卡（见下）。
- 正中 #ofr：办公室舞台 16:9，粉色内描边框。
- 底部 #bot3 三面板：SALDO / JEV·ANÁLISE / thresholds.py。

## 办公室动画层（灵魂所在）
- 底图 office-bg.jpg（像素办公室插画）+ 覆盖 Canvas 2048×1152，内部以 512×288 像素缓冲 ×4 放大绘制（image-rendering: pixelated）。
- 7 个有工位的像素小人（SEATS 坐标）+ CRAWLER 无座位全场巡逻（WALK 路点、2.6 秒换目标）；小人有：身体色=人格色、深色下摆、高光、眨眼、腮红、打字手抖/走路方向眼神、头顶状态图标（spin 加载/✓/✗）、专属道具：CHIEF 皇冠+领带、SCAN 眼镜、VET 放大镜（旋转光点）、SOCIAL 手机、CRAWLER 棒球帽、RISK 盾牌（红十字）、FILLS 铃铛、JOURNAL 笔记本+铅笔线。
- 墙上 TV 区域 [343,17,644,135]：覆黑后画真实净值 K 线（36 根聚合蜡烛+量柱+闪烁最新价块）+「SALDO VIRTUAL (PAPER)」字幕。
- 激光线：忙碌的小人向 TV 发射其人格色虚线。
- 气泡：真实日志事件驱动台词（onLog 按 tag 分发）：SCAN 发现新币、VET 判定 GOOD/RUG、RISK 否决附短原因（shortWhy 从葡语文本正则提炼）、FILLS 成交「ding!」、CHIEF 系统消息、熔断时 CHIEF 固定台词「disjuntor ligado, ninguém entra」；每 4.5 秒还有随机小人复述自己最新日志（截 44 字）。
- 熔断（S.cb）：每 0.5 秒闪烁红色横幅「PAUSA — disjuntor」。
- derive()：新日志（id 增量）排队触发动画（每条间隔 600–900ms）；tid 增长触发 FILLS+JOURNAL 双人庆祝。

## 机器人人格（8 个，BOTS 表）
CHIEF 粉 👑「nunca abre posição sem consenso」指标=虚拟余额；SCAN 蓝 🔭 已见 token 数；VET 白 🔍 GOOD/已评数（合并 AUTO-VET/VET 两路日志）；SOCIAL 红 📱 平均 hype（无独立 agent，从 sentimento 日志回退合成）；RISK 橙 🛡️ 战绩 W/L；FILLS 绿 🔔 最近一笔平仓盈亏；JOURNAL 黄 ✏️ ticket 号 tid；CRAWLER 青 🧢 已读 perfis/pares 数（从日志正则提取）。
每卡：人格色圆点+名字（人格色粗体）+ 人设句 + PAPER/OFF 标签 +「指标名 · 时间」与指标值一行 + 新鲜度条（按 agent.at 距今衰减）+ 最新一句日志引言（引号内）。

## 底部三面板
1. **SALDO**：净值面积图（绿辉光线+渐变填充+末点白点）+ 下方日变动柱 + 右侧巨字余额（绿辉光 900）与「nW nL · DIA ±%」；标题含 DIA 日期。
2. **JEV · ANÁLISE**：每 7 秒轮换选中 token（tokens 前 40）；6 项统计格（FDV/价格/24h/量/流动性/年龄）；24h 涨跌柱图（前 24 个 token，选中者粉色）；10 道门 GATES 逐项 PASS/FAIL 表（NO_RUGCHECK/NO_X/VET_/SITE_PLACEHOLDER/CA_UNCONFIRMED/PAID_HYPE/LIQ_LT_40K/VOL_GE_10X_LIQ/PAIR_AGE_LT_30M/BOOSTS_GE_30）；最近 7 个 token 表（LIQ/VOL/24H/AUTO-PRÉ/FINAL/否决键前 3）；右上 JSON/CSV 导出按钮（导出全部 open+closed 交易，23 字段）。
3. **thresholds.py**：把引擎风控渲染成「代码视图」：HARD 组 7 项算术门（min_liquidez 40k / max_vol_sobre_liq 10× / min_idade 30min / max_boosts 30 / max_top10 38% / min_lp_lock 100% / max_posicoes 3）与 VET/SOCIAL 组 4 项（rugcheck_obrigatorio / vet_auto_pre=GOOD / perfil_x / mint-freeze 已撤销），逐项显示阈值 vs 实际值 + ✓/✗，失败行红底；右侧**否决蛛网 Canvas**（spider()：统计 tokens.finalKeys + finals.keys 真实计数 Top10，橙环+绿节点+旋转，中心粉框）；底部 filter.py 流水线 pills（rugcheck→x→vet→site/CA→hype→liq/vol/idade→ENTRY）每 0.9 秒轮进高亮。

## 数据契约（data 分支 state.json，20 秒轮询）
拉取方式：先 GET api.github.com/repos/.../commits/data 取 sha，再 raw 拉该 sha 的 state.json（防缓存）；失败回退分支直拉。AbortController 12 秒超时。
顶层字段：version/startedAt/updatedAt/live/bal/dayStart/dayKey/weekStart/weekKey/cb/cbReason/wins/losses/tid/seq；
open[]（46 字段：entry/last/high/size/risk/sl/tp1/tp/vet/vetWhy/liq/vol/flags/gateReasons…）、closed[]（28 字段，含 pnl/exit/exitReason/vetScore/social/sentiment/boosts/gating/trailing）、tokens[]（37 字段：price/ch/vol/liq/fdv/created/vet/final/finalKeys/score/heur/sent/xAccount…）、agents{7 个 CHIEF/SCAN/RISK/CRAWLER/AUTO-VET/FILLS/JOURNAL → {last, at, on}}、logs[]（250 条 {id,t,tag,msg}）、equity[]（339 点 {t,v}）、seen[]（221）、finals{77 条 → {final, keys, …}}、vetCache{17 条 → 19 字段含 markets/top10/mintAuth}、liqHist{116 币的流动性历史}、injHits{116 币}、boosts、testDaily、teste（测试通道统计）、nightReportDone、caCache、lastPollAt。
decisions.js：window.GROUP_DECISIONS 群体决策复盘数组（当前 1 条：VEILTRUMP 被 REJEITADO，含 summary/assertiveness/note 葡语复盘）——index.html 主脚本未见引用，疑似预留/外部使用。

## 语言与时区
全葡语 pt-PT；时钟 Europe/Lisbon；数字 toLocaleString('pt-PT')；日志与台词均为葡语（数据自带，页面不翻译）。

## V2 融合可复用点（以它为主的含义）
1. 基座=这页本体：办公室、8 人格、底部三面板的形制与全部动画逻辑原样保留，这是它的灵魂，不重画皮。
2. 视觉 token（粉底网格/黑卡/等宽/人格色）成为 V2 的全站设计系统。
3. 融合形态：在办公室之外加「分室」场景（行情/赛跑/蜘蛛/安全），沿用同一 token 与顶栏，切场景不切语言体系。
4. 双语：界面字符串抽 i18n（zh/en 两包）；数据自带葡语日志经模板翻译层转中文/英文显示（补充 2 已改此条，不再以葡语原文为主显示）。
5. 数据通道：state.json 沿用其 20 秒轮询法；其余四路沿用 V1 已验证的接口与口径。

## 目的性（为什么做成这样）
一句话：把一个自主运行的多智能体**纸面**交易系统，变成一间能亲眼看懂的办公室——让运营者不读日志、不读 JSON，也能实时监督、事后审计这群机器人到底有没有按规矩干活。
- 监督而非操作：人类不下单。引擎全自动跑（扫描→爬取→链上验真→社交热度→风控定仓→总指挥共识→执行→记账），人只看：实况灯（AO VIVO/ATRASADO）、熔断红警、余额趋势。
- 可解释/可审计：每一次否决都有具名钥匙（finalKeys）、门槛以代码形态摊开并逐项对实时值打 ✓/✗、蛛网聚合否决统计、decisions.js 做群体决策复盘（含自我批评：哪次批准忽略了 red flag、果断度打分）。这是把「审计轨迹」做成了界面。
- 彩排实验：通篇 PAPER/虚拟钱、日/周锚点（dayStart/weekStart）、夜间报告——先用假钱连续跑，证明这支队伍的纪律，再谈真钱。
- 在场感：人格、气泡、走动、激光、电视 K 线，都是为降低黑箱焦虑：运营者是「去办公室看一眼」，不是「查数据库」。
对 V2 的约束：融合的本质是给这间办公室加同事/分室，新模块必须以同样的「看得懂它在干活」的方式进来，不能把它降级成普通数据看板。

## 用户补充 1（2026-10-16）：股票交易台
- V2 需要实现 A股 / 港股 / 美股「交易」。
- 行情与语言/数据接口：用之前项目（FinSight finance-dashboard）的接口层——/api/finance/*（指数、自选报价、K线、技术分析、筹码、板块、快讯、精选）已覆盖三市场，V1 已实证可用，不另造数据管道。
- **已拍板（2026-10-16）**：先做纸面模拟交易台；真实下单留口以后接。实现约束：下单统一走 OrderGateway 抽象（paper 撮合实现先行），券商实现以后按同一接口接入，UI 与记账不改。

## 用户补充 2（2026-10-16）：葡语日志中文化 + 英文切换
- 数据自带的葡语内容（logs[].msg、agents[].last、气泡台词、decisions 复盘）默认显示中文，语言开关切英文；不再保留葡语原文作主显示（推翻此前「原文保留」条）。
- 实现路线：日志是高度模板化的（扫描/批准/否决/成交/情绪等固定句式带变量），建规则模板翻译层（正则→zh/en 模板）覆盖绝大多数；decisions 等自由散文：已知条目随仓附译文，未覆盖的新散文回退原文并标记。界面字符串走完整 zh/en 双语包（同此前计划）。

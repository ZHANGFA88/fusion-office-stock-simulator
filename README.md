# Fusion Office（融合办公室）

以 **grokbot-jev-dashboard 的虚拟交易办公室**为基座的个人情报与交易台：走进办公室就能看懂一支自主运行的智能体队伍在干什么、合不合规矩、账对不对。在此之上加了 A股/港股/美股**模拟交易台**（纸面下单、真实下单接口留口）与分室：行情、定投赛跑、机械蜘蛛、财经观测台和独立的 REA 站点分析。界面中英双语（默认中文），数据自带的葡语日志经模板翻译层转成当前语言显示。

构建规格见 [docs/SPEC-V2.md](docs/SPEC-V2.md)，grokbot 原仓研读笔记见 [STUDY.md](STUDY.md)。钛金影院风版本是独立项目：ZHANGFA88/fusion-dashboard；本仓 `fusion-office-stock-simulator` 为单独的新仓，不覆盖其内容。

## 发布状态：WIP

这是仍在集成中的公开源码，不是可直接部署的公共实时行情服务。已通过的自动/DEMO 验收与未通过项分别记录在 [审查与修复记录](docs/REVIEW-AND-REMEDIATION.md)；当前已知限制包括：设置页尚无密码认证后端、部分按需行情/办公室/赛跑路径可能访问上游且许可未全部核实、启动主服务会初始化并写入本地 SQLite、限价单自动撮合暂停、首页语言按钮交互与持久化尚未完成最终 QA。首页根级溢出检查在 360/390/768/1280px 已通过，但不代表完整视觉验收。

## 分室

| 路由 | 内容 |
|---|---|
| `/`（app/index.html） | 办公室：像素办公室动画 + 8 位机器人格 + 余额/JEV 分析/thresholds 三面板（grokbot 原页改造：双语化 + 日志翻译层 + 顶栏分室导航 + 股票交易员状态条）。数据每 20 秒直连上游 `data` 分支 state.json，本机 `/api/office/state` 代理兜底 |
| `/app/trade.html` | 股票交易台：三市场模拟子账户（CNY/HKD/USD）、FinSight 行情与 K 线、市价/限价下单、持仓盈亏、工单账本 |
| `/app/markets.html` | 行情室：FinSight 指数、自选、板块、精选、快讯 |
| `/app/race.html` | 赛跑放映室：六大指数定投赛跑全片 + 终值条（组合 ¥2,746,664 / +353.24% / IRR 16.17%） |
| `/app/spider.html` | 蜘蛛工坊：prompt-spider-ui 舞台（本机 :4317）状态与内嵌 + MCP 工具清单 |
| `/app/finance-observatory.html` | 财经观测台：行情、公司公告/申报、财经新闻和观察摘要；当前为 DEMO |
| `/app/rea.html` | REA 只读站点分析：AI Safety HOT 固定目标、话题共现图、主要接口与本机快照；[逆向分析记录](docs/REA-SITE-ANALYSIS.md) |
| `/app/settings.html` | 数据源与安全设置占位：公开只读、无密码/密钥输入、无配置写 API；不构成身份验证边界 |

## 运行

本项目提供的交易仅为 PAPER 模拟，不连接真实券商、不构成投资建议。财经观测台与兼容路由 `/api/finance/news` 只返回 DEMO fixture（合成演示数据，不代表实时市场）。默认启动不会启动后台行情/赛跑刷新；用户主动请求其他继承的行情、办公室或赛跑接口仍可能访问外部源，因此不要把整套服务描述为“无 live 请求”，也不要未经授权审查和网络隔离验收就作为公共实时数据预览发布。

REA 站点扫描默认关闭；`--with-rea-inspector` 仅启用固定目标 `aisafetyhot.com/zh` 与 `/zh/hot` 的本机分析 API。REA 场景没有请求拦截/域名白名单，因此即使没有点击、搜索或提交，页面脚本仍可能自行发出资源请求或 Cloudflare RUM 遥测；本功能不保证 GET-only。结果写入静态网站根目录之外的 `$XDG_DATA_HOME/fusion-office/rea/`（默认 `~/.local/share/fusion-office/rea/`，报告权限 0600），不会进入 Git、行情或 PAPER 交易链路。

```bash
bash scripts/run.sh                 # 主服务，默认端口 8772；无后台 provider 刷新
bash scripts/run.sh --with-market-refresh # 显式启用行情/指数/板块/K线/赛跑后台刷新（可能外呼）
bash scripts/run.sh --with-rea-inspector # 显式启用本机、固定目标的 REA 站点扫描
node modules/spider/server.mjs      # 蜘蛛舞台，端口 4317（可选）
```

- 行情与分析接口沿用 FinSight（`/api/finance/*`），办公室代理 `/api/office/state`。
- `GET /api/finance/observatory` 只返回本地合成 DEMO fixture；设置页当前也为只读。配置管理与密码认证未实现，不能将设置页当作安全边界。
- `GET /api/rea/status` 与 `/api/rea/report`、`POST /api/rea/scan` 仅接受 loopback 同源请求且不开放通配 CORS；扫描路由只接受空请求体，目标 URL 固定且必须显式启用。扫描只保留净化后的节点/边与首方请求路径，不保留原始 Evidence 或文章正文。
- 蜘蛛 MCP（可选）：`cd modules/spider/mcp && npm ci` 后按其 README 接入。
- 本集成目录不携带 SQLite 数据库文件；首次启动主服务会创建/初始化 `data/finance.db` 与 `data/paper.db`，并为模拟交易写入种子账户/自选数据。若不希望产生这些运行时写入，请勿启动主服务；本轮测试只用临时 loopback handler，未运行主进程。

## 模拟交易规则（PAPER）

- 下单走统一网关抽象 `OrderGateway`：当前实现 `PaperGateway`（纸面撮合）；`BrokerGateway` 为真实券商留口（未接入，调用返回未实现）。
- 市价单按 FinSight 最新报价即时成交；无报价拒绝成交（不伪造价格），缓存旧价成交会打 `stale_price` 标记。限价单仅进入 `open` 队列；自动撮合目前暂停，未设置自动成交调用点。
- `GET /api/trade/account`、`/positions`、`/orders` 仅读取交易账本，不撮合、不结算 T+1，也不改变订单/持仓；但账户与持仓估值仍可能按需请求行情上游。
- A股：买入 100 股整数手，T+1（当日买入不可卖）；佣金 0.025%（最低 ¥5），卖出印花税 0.05%。港股/美股模拟盘允许同日买入并卖出（T+0 日内交易）；这描述的是可否当日交易，不是交收周期：香港股票市场交收为 T+2，美国多数适用证券自 2024-05-28 起为 T+1。模拟账本不模拟真实券商的现金交收、现金账户资金限制或保证金规则。港股：佣金 0.03% + 约 0.0057% 征费。美股：0 费率。以上均为模拟参数，界面有明示。
- 初始虚拟资金：CNY 1,000,000 / HKD 1,000,000 / USD 100,000；交易台内可一键重置。

## 赛跑活数据 + 可交互

赛跑页已接活数据，不再是定格录像：

- **数据管线**（modules/race/engine/）：以已校验种子 `seed_daily.json`（截至 2026-10-08，与视频公布值逐项对账过）为基线，`extend_daily.py` 用「收益率链式拼接」按日延长到最近交易日——只算新段收益率、绝不重算历史（QQQ/SPY/EXS1.DE/CAC.PA 用 Yahoo 复权；日经用 1346.T 收盘+分红自建全收益，避开 1321.T 坏复权；沪深 H00300 全收益取中证官网）。`engine.py` 随之重算全部定投口径，产物落在 `data/race-engine-live.json`（不入库）。
- **刷新节奏**：后台行情、全球指数/板块/K线预热及赛跑刷新默认关闭；只有显式 `bash scripts/run.sh --with-market-refresh` 才会启动（会调用已有外部数据源）。赛跑管线也可手动运行 `python3 modules/race/engine/refresh.py`。接口：`GET /api/race/engine`（活数据优先、静态兜底）、`GET /api/race/meta`（来源与截至日）。
- **交互**：页面下方新增金色「数据时间轴」——拖动按数据日定位（读数含日期/期数/组合值），与胶片轴双向同步；9 个历史事件做成 chips，点任意事件跳到其窗口并钉住点评卡（窗内涨跌、定投收益率高点→窗末、判语）；「曲线对比」打开全屏累计收益曲线（组合/等分/六个单投可勾选），十字线与数据轴同步；另有 0.5×–4× 变速。片尾金句金额与数据来源截至日随活数据自动更新。

## 蜘蛛财经事件 DEMO

蜘蛛是「提示词可视化舞台」：默认播放本地演示，不联网、不调用模型。`/api/finance/observatory` 与兼容路由 `/api/finance/news` 固定返回本地合成 DEMO records。新闻路由的网络传输 mock 回归测试验证 `urllib`/`subprocess` 外呼计数为 0；此结论只适用于被测新闻路由，不代表整套应用没有 live 请求。

`scripts/run.sh` 默认只启动主服务，不会自动启动 Spider feeder、AI crew 或后台行情刷新。显式运行 `scripts/run.sh --with-spider-demo` 才会启动本地 feeder；它只轮询经数值端口校验的 `127.0.0.1:<port>/api/finance/observatory`，拒绝非 DEMO payload，不接受外部 URL 覆盖。`--with-ai` 和 `--with-market-refresh` 是独立 opt-in；AI 仅使用 PAPER 交易，但其按需行情请求以及后台刷新选项都可能调用外部源。Spider MCP/Agent 的实时上报能力与财经新闻采集仍彼此独立。

`--with-market-refresh` 才会启用后台实时行情刷新；默认后台刷新关闭。不过行情页/交易页的部分按需请求仍会访问继承的上游接口，所以公开托管前仍须对这些接口逐项许可审查和网络隔离。

## 数据来源（多源互备）

- A股报价：新浪 → 东方财富 → 雪球 → 百度；港美报价：腾讯 → 新浪 → CNBC → Yahoo；加密/外汇：Yahoo。任一源无数据自动顺延下一源，全部无数据时用本地缓存旧值并打「旧价」标记，绝不伪造价格。
- 板块榜：东方财富 → 新浪行业/概念板块（新浪源无主力净流入与涨跌家数，相应字段为空）。
- 财经快讯：观测台及 `/api/finance/news` 兼容路由只返回本地 DEMO fixture；实时新闻采集保持关闭。行情、K线等其他接口在用户请求或显式启用后台刷新时仍可能调用外部源（未来启用前须核验许可）。
- 办公室数据：上游 grokbot `data` 分支 state.json 直连，本机 `/api/office/state` 代理兜底。

## 语言

- 界面字符串中英双语包（`app/assets/i18n.js`，办公室页因单文件架构内联同份词条，改词条需两处同步）。
- 葡语日志翻译层 `trLog()`：引擎日志为固定句式 + 变量，按模板正则翻译（实测对上游 250 条日志残留葡语为 0），未覆盖的新句式回退原文并加 `[原文]` 标记，不乱译。

## 来源与许可

- 办公室基座：Vazolini/grokbot-jev-dashboard（葡语 Degen Lab 纸面交易页，含其 `decisions.js` 与办公室插画）——**上游未声明 LICENSE**；当前维护者已确认有权公开本衍生项目代码，但该确认不构成对上游或其他组件的额外授权/再许可。
- 行情后端 FinSight finance-dashboard：用户自有项目。
- 机械蜘蛛：Kirito14IT/prompt-spider-ui，MIT（见 modules/spider/LICENSE）。
- 定投赛跑：用户自有回测引擎与数据（modules/race）。
- 未入库的大文件：`modules/spider/docs/preview.gif`（8.7MB 预览动图）与 `modules/spider/docs/brand/` 品牌图片（非运行必需，需要时见上游仓库）。

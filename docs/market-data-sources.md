# 行情源选择与生产许可闸门

核对日期：2026-10-16。产品目标按用户澄清设为个人本地/私有自用。本文用于 provider-neutral 接入层的来源配置与上线审查；价格、配额与条款可能变化，购买/启用前须与供应商确认。本文不表示项目已接入任何供应商，也不构成法律意见。

## 结论

- 个人本地/私有自用与对外展示是不同场景。可按供应商明确的个人使用方案评估本地行情界面和模拟撮合；个人/API 权限不自动覆盖把数据继续开放到临时公网预览、分享页面、多人产品、再分发或商用。外部展示须单独确认展示/缓存/历史/衍生数据和二次传播权利。
- 在首发市场和粒度（A 股分钟线、美国全市场 SIP、单交易所实时等）尚未确定前，开发与自动测试使用 mock/fixture；确定个人使用的市场和粒度后，再选择其条款明确覆盖本地个人用途的 provider。当前公开临时 8772 预览继续只用 mock，不能因为本地自用而向公网展示真实源。
- 配置中将 `provider_enabled`、`personal_local_use_allowed` 与 `external_display_authorized` 分开。默认前两者均 false，外部展示授权默认 false；启用本地真实数据须通过明确的个人方案/条款核对和私有部署配置，启用外部页面则必须单独有展示授权。不得把免费个人方案或公开网页可见性推断为可外部发布。
- Scrapling 只是受许可 HTTP/API/RSS 内容获取的实现工具，不授予数据使用或再发布权。采集端只使用标准 Fetcher 和明确允许的官方 API/RSS/网页；不得启用 stealth、代理轮换、Cloudflare/反爬绕过、登录墙/访问控制规避。实时行情供应商优先走其正式 REST/WebSocket API，不从搜索结果或浏览器能访问推断抓取许可。
- 页面/接口分别披露行情源、市场覆盖/粒度、数据时间、接收时间、延迟/陈旧状态。源切换必须保留 provider/feed 标识；不得静默混合口径。

## 可核实候选

| 候选 | 可核实的覆盖/实时特征 | 公开额度/价格信息 | 面向外部产品的许可状态 | 当前推荐状态 |
|---|---|---|---|---|
| Alpaca Market Data（Trading API） | Basic：美股实时数据 feed 使用 IEX 单一交易所；官方说明约占美股市场成交量 2.5%，不是全美 SIP。官方批量 snapshots 端点返回最新成交、最新 bid/ask 和 bar。 | IEX 是唯一无需行情订阅的 feed；请求需用户自有 API key/secret。具体 API 配额随账户权益/响应限额头核对。 | 可作为私有个人模拟盘的候选；个人 API 不等于多人或公网再分发授权。 | `personal_local_candidate`；`AlpacaIEXPersonalProvider` 使用 `GET /v2/stocks/snapshots?symbols=…&feed=iex`，只在用户私有运行环境显式配置 `ALPACA_API_KEY`、`ALPACA_API_SECRET`、`provider_enabled`、`local_personal` scope 和 freshness 阈值后可用。默认为关闭、缺 key 零请求；公网预览仍 mock。 |
| Massive（原 Polygon） | 个人套餐与商业行情套餐分开；全市场/实时特征须按具体 feed 确认，IEX 或单场所 feed 不能视为全市场。 | 个人 Stocks Advanced 公开页列 $199/月；商业页列全美市场延迟 15 分钟 $499/月、全市场实时 $1,999/月，且额外交易所费用可能适用。 | 个人计划标为非专业个人用途，可作为私人自用评估，不得据此推定可向用户展示/再分发。未来公开部署须购买商用方案并确认展示、缓存、留存、超额费、SLA和总价。 | `personal_local_candidate`；公网预览仍 mock。 |
| Twelve Data | 官方称默认实时 feed 覆盖全部上市美股代码，但来源场所约占总美股成交量 5%；非 SIP/全市场成交量。OTC 不含在默认计划。 | Basic 免费档列 8 credits/min、800 请求/日，价格页注明内部非展示用途；商业页列 Venture/Enterprise 等方案，官方不同页面对计划权益的描述不完全一致。 | 适合先询问个人本地展示是否由当前计划覆盖；外部分发依用途需相应方案与 Redistribution Rights Add-On，full-market 亦需另行许可。书面确认个人展示、数据留存及外部分发范围。 | `personal_local_candidate_pending_terms`；未确认本地展示权前用 mock。 |
| Tushare Pro（A 股） | 官方权限表列实时分钟数据及实时日线；是分钟/日线频率，不可描述成逐笔低延迟行情。 | 个人页面列实时分钟 1000 元/月，500 次/min、每次最多 300 公司；实时日线 200 元/月、50 次/min、每次可全市场。该页说明机构价为个人价 10 倍。 | 页面提供个人方案，可评估本地自用；个人订阅不自动授权向产品用户展示、转发或长期缓存。未来公开部署须确认机构/商业展示、地域、留存和 SLA。 | `personal_local_candidate`；启用前核实个人账号权限与当前条款，公网预览仍 mock。 |
| Stooq（历史 CSV） | 官方历史数据页列有多个市场的日线、小时线及部分 5 分钟档；不能当成逐笔实时源。 | 历史页提供下载入口；本项目不调用，也未核实自动接口额度/服务水平。 | 团队当前检查报告其 robots 对通用爬虫 `Disallow: /`。尽管官方条款/数据页可公开访问，仍不自动抓取；不得尝试用 Scrapling、修改 UA 或其他办法绕过此限制。未来只有站点明确允许自动获取或取得书面许可后再重新评估。 | `blocked_robots`；不实现、不请求，改用条款明确允许的官方 API/RSS 或 mock。 |
| 上交所/深交所及其授权行情服务商 | 交易所 Level-1/Level-2 产品；Level-2 另含逐笔、委托队列等数据。各市场、地区和时延等级分别核对。 | 公开网页不足以确定本项目完整商业资费；正式报价、API/链路和配额以授权商合同为准。 | 上交所明确只有合资格供应商获授权方可转发/使用；申请服务或通过获授权供应商，不以爬虫或未授权 API 替代。具体客户展示资格按市场和地区确认。 | `contract_required`；适合 A 股正式上线的优先询价路径。 |

### 官方核验链接

- Alpaca 订阅和数据覆盖：https://docs.alpaca.markets/us/docs/about-market-data-api
- Alpaca 对 IEX 与 SIP 差异的说明：https://docs.alpaca.markets/us/docs/market-data-faq
- Alpaca 官方批量 Snapshots REST API：https://docs.alpaca.markets/us/reference/stocksnapshots-1（最新成交与最新报价分别带源时间戳；本实现强制 `feed=iex`）
- Alpaca News REST API：https://docs.alpaca.markets/us/reference/news-3
- Alpaca 实时新闻 WebSocket：https://docs.alpaca.markets/us/docs/streaming-real-time-news
- Alpaca Historical News 说明：https://docs.alpaca.markets/us/docs/historical-news-data
- Massive 商业行情价格：https://massive.com/business
- Massive 再分发规则：https://massive.com/knowledge-base/article/how-can-i-redistribute-massives-market-data
- Twelve Data 商业价格：https://twelvedata.com/pricing-business
- Twelve Data 美股覆盖和许可说明：https://support.twelvedata.com/en/articles/9935903-us-equities-market-data
- Tushare 官方权限、价格和频次：https://tushare.pro/document/1?doc_id=290
- Stooq 条款与数据目录（仅供许可复核；当前不自动请求）：https://stooq.com/terms.html、https://stooq.com/db/h/；团队当前 robots 检查结果为 `Disallow: /`。
- 上交所行情产品及授权说明：https://english.sse.com.cn/markets/dataservice/products/
- 上交所海外授权服务商目录：https://english.sse.com.cn/markets/dataservice/list/

## 财经新闻候选：Alpaca News

官方 `/v1beta1/news` REST 与 `wss://stream.data.alpaca.markets/v1beta1/news` 新闻流可返回 `id`、`headline`、`summary`、`author`、`source`（示例来源为 Benzinga）、`symbols`、`created_at`、`updated_at`、原文 `url`，REST 支持时间/证券筛选和分页（每页最多 50 条），响应有 rate-limit headers。`include_content` 可请求可能含 HTML 的正文、另有图片 URL；新闻接口需要 API key/secret。官方文档称历史新闻数据自 2015 年起且由 Benzinga 提供。

**启用状态：候选、默认关闭。** 官方公开文档未能确认 Alpaca Basic 个人方案是否包含本项目所需 News 权益，也未明确允许缓存/公开转发第三方媒体的 headline、summary、正文或图片。用户自行配置 Alpaca 凭据之后，仍需在其账户核对 News entitlement 与个人本地展示/缓存条款；确认之前不请求新闻端点。届时优先只保留 headline、publisher/source、symbols、原文 URL、`created_at`/`updated_at` 与本机 `received_at`，不请求/保存正文和图片；`provider=Alpaca` 与 `publisher=source` 分开记录，新闻条目标记 `domain=finance`，不得默认为可公开展示。越权字段只供源端传输，不因 API 返回就推断转载权。公开 8772 预览仍使用 fixture/mock。

## 个人本地自用与外部发布检查项

对每个 `provider + market + feed` 组合分别建使用记录，至少确认：

1. 当前是个人本地私有自用，还是对外展示/多人使用；provider 的个人条款是否涵盖本地可视化和纸面撮合输入。
2. 实时、延迟或 EOD 的明确等级；股票代码范围、交易所/场外市场、成交/报价/分钟线/逐笔的字段和口径。
3. 页面展示、衍生指标、撮合输入、用户导出、缓存期限、历史存档、回放和二次分发权利是否分别覆盖。
4. API 与 WebSocket 的并发连接、标的数、频次、突发限制、批量请求、数据补发、SLA、支持和费用；是否还需交易所单独报备/付费。
5. 来源署名/标识要求、供应商/交易所通知、合同到期、撤回授权时的删除/停止分发义务。
6. 适用市场日历、时区、竞价/午休/停牌状态，事件时间与接收时间的定义及精度。

未完成这些检查，`personal_local_use_allowed=false` 且 `provider_enabled=false`。个人本地使用确认后，只能在私有本地配置中明确开启；公开预览始终使用 mock，除非另行核实已获外部展示许可且单独批准启用。

Alpaca Basic 的调用需要用户自己的 API 凭据；本项目不注册账户、不索取或保存 key。适配器只请求最新 IEX snapshot，不调用交易端点；行情记录分别保留 latestQuote 和 latestTrade 源时间以及本机 received_at，并标明 IEX 单场所覆盖。只有在私有部署中用户自行配置 `ALPACA_API_KEY`、`ALPACA_API_SECRET`，同时显式开启 provider、仅授予 `local_personal` 并设置项目级 freshness 阈值后才可发请求。缺少任一项均关闭/拒绝，不连接公网预览；模拟成交不能把 stale 记录当现价。凭据只供服务端使用，不传到前端、日志或样例文件。

## 新鲜度与模拟成交

- 接入记录分别保留供应商事件时间 `event_ts` 和本系统接收时间 `received_ts`；若供应商提供 feed、交易所代码、序列/批次、报价状态，也须保留。
- 新鲜度阈值按产品粒度、供应商 SLA、市场交易阶段和单证券状态配置，不写死统一秒数；“停牌/无成交”和“源断流”不可混为一谈。
- 仅在交易所日历允许新报价的阶段判断市场 feed 是否陈旧；闭市、午休或正常盘后应展示对应市场状态，不触发假性断流警报。
- stale 或无法确认 feed 状态时保留 last-known-good 并标注来源/时间/延迟；模拟撮合默认冻结，不得静默按旧价成交。每笔模拟成交记录当时的 quote ID、source/feed、事件时间和撮合规则。
- 备用源必须具有同等覆盖、许可与撮合口径。没有合法且兼容的备用源时，不要自动切换；明确显示“数据源不可用/数据截至…”并冻结撮合。

## 建议的配置语义

无需绑定某供应商 SDK 的记录可含以下字段；接入代码由统一适配层消费，任何密钥只能从运行环境读取，不入仓库：

```json
{
  "provider": "provider_name",
  "market": "US",
  "feed": "sip_or_iex_or_delayed_or_intraday_bars",
  "coverage": "consolidated_or_single_venue_or_partial_volume",
  "entitlement_status": "unverified_or_contract_required_or_authorized",
  "provider_enabled": false,
  "personal_local_use_allowed": false,
  "external_display_authorized": false,
  "allowed_uses": [],
  "retention_policy": null,
  "attribution_required": null,
  "quota_reference": null,
  "source_url": null
}
```

示例仅为字段契约，不是可直接启用的供应商配置。个人私有本地开关与公网展示授权必须彼此独立；即使本地真实源已启用，外部预览默认继续只用 mock。

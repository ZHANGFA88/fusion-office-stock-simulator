# REA 站点分析器

工作台里的独立只读网页分析面板。扫描固定目标 `https://aisafetyhot.com/zh` 与 `https://aisafetyhot.com/zh/hot`，不接受客户端 URL，也不点击、不搜索、不提交表单。扫描默认关闭，需要用 `scripts/run.sh --with-rea-inspector` 显式启用；状态、报告与扫描 API 只接受 loopback 同源请求，不开放通配 CORS。

> REA 6.5.0 的浏览器场景捕获没有网络请求拦截或来源白名单。页面脚本仍可能自行请求图片、第三方资源或遥测；界面会对此提示。此处不是网络层 GET-only 保证。

只保留净化后的 first-party 请求路径与 `/api/site/monitor` 图谱节点/边；不保存原始 REA Evidence、请求正文、文章标题或文章正文。运行时摘要写入静态网站根目录之外的 `$XDG_DATA_HOME/fusion-office/rea/`，默认 `~/.local/share/fusion-office/rea/`，目录/文件权限分别为 0700/0600。该摘要不会通过静态文件服务公开，也不会进入仓库。此功能不接入行情、交易、券商、钱包、密钥或 PAPER 下单链路。

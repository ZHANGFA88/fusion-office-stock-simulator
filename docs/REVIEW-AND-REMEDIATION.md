# Integration review and remediation

Review date: 2026-10-16

## Scope and current state

The integration target is the clean, finance-only source tree at `/workspace/fusion-office-integrated`. It was assembled from a tracked baseline and explicit code/test/documentation allow-lists. No SQLite database, `ai-config.json`, API key, password, `.env`, source account data, or personal contact data is included. The finance observatory and finance-news route return synthetic `DEMO` fixtures; the platform is PAPER simulation and does not place real brokerage orders.

The unrelated room page, navigation, static module, data-source proxy, fusion-feed/status/digest entries, adapter, tests, fixtures, and documentation were excluded. A project scan found no retired source, route, or product references in the integrated tree. The finance observatory is consistently named `app/finance-observatory.html` and served by `GET /api/finance/observatory`; quotes, company filings, media news, and insights remain separate typed collections. SEC filings are represented as filing records, not media news. The Prompt Spider receives an explicitly marked finance event projection and is not a collector.

The settings page is a read-only status placeholder. It has no password/key fields and issues no configuration writes; it is not an authentication boundary. The protected settings API, private password initialization, session/CSRF/rate-limit controls, and allow-listed configuration writes remain pending the secure backend implementation. When implemented, public deployments must keep management routes disabled; setup must be private/operator-initiated, and authentication must not widen `authorized_scopes`.

## Source and permission gates

- `GET /api/finance/observatory` and compatibility `GET /api/finance/news` return local synthetic DEMO fixtures. Their endpoint tests block and assert zero calls to `urllib.request.urlopen`, `subprocess.run`, and the legacy Eastmoney/Sina provider functions; this zero-call result is limited to those tested news routes. No Alpaca request was made.
- Other inherited market, office, and race endpoints still contain provider/network integrations and are not covered by this no-live-news check. Background refresh workers are disabled by default and require `--with-market-refresh`, but on-demand page requests can still call upstreams. The integrated server/preview was not started; do not publish it as a public live-data preview until those paths are separately gated and licensed.
- The Alpaca IEX adapter is opt-in, requires operator-supplied server-side credentials, explicit provider enablement, a freshness threshold, and exact `local_personal` permission; it is not enabled here and is a single-venue feed, not SIP/full-market coverage.
- SEC submissions collection is disabled unless its local contact configuration, operator review flags, and personal-use scope are explicitly configured. It requests filing metadata only; public display/redistribution is not inferred.
- Alpaca News entitlement and content caching/redistribution rights remain unverified; no live Alpaca News provider endpoint is enabled.
- Stooq was excluded as an automated source following a reported `robots.txt` block; no adapter or request is present.

A separate SEC smoke check was reported during the parallel research: one GET to an official SEC company-submissions JSON endpoint returned HTTP 200 and metadata only; no filing body was fetched and the result was not connected to the preview. A cancelled non-finance source run reported one public endpoint request before it stopped; that source and payload were excluded, and there were no further requests. Alpaca credentials were not supplied and no Alpaca request was made.

## Validation completed

- Python regression suite: 62 tests passed, including the news network-mock, feeder safety, background-refresh opt-in, and PAPER GET-read-only regressions.
- Scrapling/SEC fixture suite: 20 tests passed.
- Prompt Spider UI suite: 45 tests passed.
- Prompt Spider MCP protocol suite: 3 tests passed.
- Finance observatory and legacy finance-news HTTP tests ran on an ephemeral loopback port and confirmed DEMO-only responses, zero mocked news-route transport/provider calls, and distinct typed collections.
- `bash -n scripts/run.sh`, `node --check scripts/spider_news_feeder.mjs`, Python source compilation, and inline JavaScript syntax checks for all 7 app pages plus `public/finance.html` (10 script blocks) passed.
- Manual UI/network QA used a local static mock with non-local HTTP blocked and POST denied. At 360px, 390px, 768px, and 1280px, all seven page routes passed root-level overflow checks; the Spider/DEMO flow produced zero external attempts and zero write attempts. English navigation labels were checked using `?lang=en`; clicking the homepage language button and verifying saved-language persistence remain untested.
- Temporary-SQLite regression tests prove `GET /api/trade/account`, `/positions`, and `/orders` do not mutate the paper ledger, trigger `_paper_sweep`, or roll T+1 settlement. Quote valuation may still issue on-demand upstream requests. Automatic limit-order matching is currently paused; open limit orders have no active automatic matching call site.
- The real `serve.py` main process was not launched. The default `run.sh` control flow was exercised with a temporary `python3` shim that only captured the attempted server command; endpoint tests used an ephemeral local handler. No SQLite database was included or opened/modified in this run. A real server start calls `init_db()` and `init_paper_db()`, creating `data/finance.db` and `data/paper.db` and seeding watchlist/PAPER-account records. `data/ai-config.json` was not opened or modified. No public preview was started or shared.

## Outstanding items

**WIP / known limitations:** The responsive root-overflow check now passes at all four tested widths; this does not verify every visual detail. English navigation labels pass under `?lang=en`, but the homepage language button interaction and persistence are not fully QA'd. The settings page is only a read-only placeholder; its password-authenticated backend has not been integrated. Automatic limit-order matching is paused. Other on-demand market, office, and race endpoints may access upstream services, so the whole application is not offline/public-preview safe.

1. Merge and independently verify the secure settings backend, then test that the public deployment cannot reach admin routes and that no first-password takeover is possible.
2. Re-check market/news rights and account entitlements before enabling any provider; set provider-specific thresholds and rights scopes in private deployment configuration only.
3. Confirm the desired state of the pre-existing `8772` preview before any restart. Recent checks reported HTTP 000 and no listening server; this integration did not start or restart it.
4. Complete homepage language-button interaction/persistence QA; the existing `?lang=en` navigation-label check is not a substitute.
5. Decide and implement the explicit limit-order matching lifecycle, then test it separately from GET/read paths.
6. Repeat public network-panel QA after upstream routes are gated/licensed and the secure backend is integrated; current browser QA used a local static mock, while endpoint tests are local-only and fixture-based.

## Public repository release preflight (2026-10-17)

- Destination: `https://github.com/ZHANGFA88/fusion-office-stock-simulator`. GitHub API confirmed it is public (`private: false`), with no branches or files before this release. The name is distinct from the user's other project repositories; this task did not modify or overwrite those projects.
- Publication authority: the maintainer explicitly confirmed authority to publish this derivative source. This confirmation is not treated as additional permission or relicensing from the upstream grokbot project, which still has no declared LICENSE; attribution and that limitation remain in README.
- Preflight scan: no database/SQLite, `.env`, credential file, `ai-config.json`, or actual secret value was found among release candidates. Search hits were environment-variable names, UI copy, and redacted/test-only fixture values. QA screenshots and generated runtime data remain ignored/excluded.
- Tests run for this publication: `python3 -m pytest -q` — 83 passed (one third-party lxml deprecation warning); Spider UI `npm test` — 45 passed; Spider MCP `npm run test:mcp` after lockfile-based local dependency installation — 3 passed; `bash -n scripts/run.sh`, `node --check scripts/spider_news_feeder.mjs`, and Python `compileall` — passed. The temporary `node_modules` installation is ignored and not part of the source release.
- README was corrected to reflect that root-level overflow checks pass at 360/390/768/1280px while full visual QA and homepage language-button interaction/persistence remain incomplete. It also now states that limit orders have no active automatic matching call site and GET account/positions/orders remain read-only.
- Release remains WIP, not production-ready: settings authentication/backend is absent; on-demand market, office, and race paths may access upstream services and rights are not fully verified; main-server startup initializes local SQLite; limit orders do not automatically match; homepage language interaction/persistence still needs QA. Passing local/demo tests is not public deployment or live-data authorization.

## Same-day trading versus settlement (2026-10-17)

- Project rule: only A-share buys are locked from same-day resale (T+1). HK and U.S. paper positions are sellable on the same day. A regression test exercises the paper-ledger buy/sell validation for CN/HK/US; market trading permission and settlement timeline are documented separately.
- Hong Kong: [HKEX's trading FAQ](https://www.hkex.com.hk/Global/Exchange/FAQ/Securities-Market/Trading?sc_lang=en) says brokers can arrange a day trade selling securities bought earlier in the day. The [IFEC stock-settlement guide](https://www.ifec.org.hk/web/en/investment/investment-products/stock/stock-trading/stock-settlement.page) states that Hong Kong stocks settle T+2 while day trading is allowed as a T+0 transaction; it separately says Shanghai/Shenzhen A-shares bought through Stock Connect can only be sold on T+1 or later.
- United States: [FINRA's investor guidance](https://www.finra.org/investors/investing/investment-products/stocks/day-trading) describes frequent intraday trading and explains that cash-account day trading requires fully settled funds to avoid free-riding/good-faith violations; margin accounts have applicable margin rules. The [SEC Investor.gov T+1 bulletin](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin) says the standard T+1 settlement cycle for most applicable securities began May 28, 2024. Therefore, same-day trading must not be confused with T+0 settlement; the paper simulator does not model broker cash-settlement or margin constraints.


## Finance observatory hot-monitor UI parity (2026-10-17)

- Updated `app/finance-observatory.html` to retain the existing observatory navigation, source/topic filters, news stream, topic map and moving spider while styling it in the Fusion Office black / neon-pink palette. The SVG spider now exposes a label, pauses/resumes with the motion control, and respects reduced-motion preferences.
- Added synthetic DEMO hot stories and bilingual detail fields to `modules/finance_observatory/fixtures/demo.json`, and made the standalone fallback use the same fixture. Hot ranking now sorts finance news/event records by the synthetic attention score; cards expose attention, source/discussion counts, `why_hot`, latest progress, source/discussion samples, ordered event history and a clearly marked pending follow-up. Timeline timestamps are deduplicated and ordered chronologically. No real headlines, forums, quotes, or attention metrics are represented by these fixtures.
- Verification: inline JavaScript `node --check`; fixture contract validation; `PYTHONPATH=. python3 -m pytest -q` — 83 passed, one third-party lxml deprecation warning; `git diff --check` — passed. Browser QA opened the policy-watch hot card, checked its explanatory detail/timeline, and confirmed the motion control changes between pause and resume.
- A temporary static UI preview was served at `https://8811-ibxaa1enpxxjmciaacj3v-efa99509.us2.manus.computer/finance-observatory.html`. The static host does not run the application API; the page's DEMO fallback supplies the visible content, and its `/api/finance/observatory` request returns the expected static-host 404. This is a visual/interaction preview, not a live-data deployment or backend verification. No main server or SQLite database was started.
- Final browser screenshot: `artifacts/finance-observatory-detail.png`. It is kept as a QA artifact and excluded from the public source commit.

# News ingestion adapters

This package has no pre-enabled sources and performs no network access on import. It provides a provider-neutral news record adapter and an SEC EDGAR company submissions adapter for private/local use after the operator has configured the source policy.

## Install

Use Python 3.10 or newer and install the dedicated runtime dependency:

```sh
python3 -m pip install -r modules/newsfeed/requirements.txt
```

The adapter uses only Scrapling's ordinary `Fetcher.get`; it sets `stealthy_headers=False`, `impersonate=None`, disables redirects/retries, and passes no proxy or proxy rotator. Do not run browser-install commands for this module. No headless browser, stealth fetcher, Cloudflare challenge handler, CAPTCHA solver, proxy rotation, or anti-bot bypass is part of this integration.

Scrapling currently supports HTML parsing but not XML-feed parsing ([official parsing docs](https://scrapling.readthedocs.io/en/latest/parsing/selection.html)); RSS/Atom are fetched by Scrapling and parsed with Python's standard XML parser. JSON API bodies are parsed through Scrapling's `Response.json()` when the response is labelled JSON; fixtures can pass decoded data directly. Approved HTML listing pages require explicit CSS selectors and exact-host allow-lists. The request configuration follows the official [static Fetcher guide](https://scrapling.readthedocs.io/en/latest/fetching/static.html), with stealth headers, impersonation, proxies and redirects disabled.

## SEC company filing example

The adapter requests only `https://data.sec.gov/submissions/CIK##########.json` through Scrapling. It emits records with `news.content_type = "SEC Filing"`, not general-media news. It does not fetch linked filing documents. The official SEC API is documented at [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces); automated access rules, including declared User-Agent and a total cap of 10 requests/second, are described at [Accessing EDGAR Data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data). The default polling interval is one minute, comfortably below that ceiling, and `ETag` / `Last-Modified` validators are honored.

The contact email is read only from the local `SEC_CONTACT_EMAIL` environment variable via `SECSubmissionsConfig.from_environment(cik, ...)`; it is never read from the user's profile, hard-coded, logged or sent to the UI. Collection is disabled by default and stays disabled when the variable is absent, even if `enabled=True` is requested. To enable, configure the variable and explicitly set `enabled=True`, `terms_reviewed=True`, `robots_allowed=True`, `local_personal_authorized=True`, and a CIK. Missing email, review flags or explicit local-personal scope approval fail closed before any request. The default `authorized_scopes` is empty; after explicit review it emits only `local_personal`. External/public redistribution scopes are never inferred or enabled. Confirm the source's terms, intended use and any disclosure/cache treatment for the deployment context before enabling. An open endpoint or locally usable response does not grant republication rights.

Example shape (no real CIK or email is configured here):

```python
config = SECSubmissionsConfig.from_environment(
    cik="<10-digit CIK>",
    enabled=True,
    terms_reviewed=True,
    robots_allowed=True,
    local_personal_authorized=True,
)
provider = SECSubmissionsProvider(config)
result = provider.read()
```

The production web preview remains mock-only; do not expose this adapter's local-personal records through a public endpoint. Public display and redistribution require separate reviewed scopes; neither is inferred from `local_personal`.

## Other approved sources

Create a `FeedSource` only after reviewing the exact feed/API/page's robots policy and terms for the intended use. `allowed_hosts` must be an exact hostname allow-list; redirect following is disabled; only the registered endpoint is fetched. Configure the rights scope, attribution, caching, locale, polling period and expected fields. Do not point it at vendor web pages or unofficial endpoints simply because they are public. If any source rejects the request or rate limits it, respect the reported `Retry-After`; the adapter does not retry or silently switch to an unapproved provider.

RSS/Atom records omit article body and descriptions. JSON and HTML adapters extract only explicitly configured title, link, timestamp and optional instrument identifiers. Unknown/unzoned source times remain in `published_at_raw` with normalized time null. News items retain distinct `provider` and `publisher`, canonical source URL, stable ID, timestamps, health, and authorization metadata per `docs/DATA-CONTRACT.md`.

## Offline tests

```sh
python3 -m unittest discover -s modules/newsfeed/tests -v
```

Tests use local JSON/XML fixtures and in-memory transport fakes. They never call SEC or any real news endpoint.

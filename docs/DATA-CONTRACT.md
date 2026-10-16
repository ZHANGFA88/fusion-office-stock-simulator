# Provider-neutral data contract

Scope: the quote and news intake layer in the isolated `fusion-office-data-work` copy. This contract does not authorize any data source. Implementations must use fixtures or mocks unless a source has been reviewed for the specific intended use. Personal local use, internal use, public display, and redistribution are separate scopes.

## Shared envelope

Use JSON-compatible fields so Python and JavaScript adapters can exchange records without vendor-specific dependencies. Timestamps are ISO-8601 UTC strings with `Z`; preserve the original source timestamp separately when its timezone or interpretation is uncertain.

```json
{
  "schema_version": "1.0",
  "record_type": "quote | news",
  "domain": "finance",
  "id": "stable-normalized-record-id",
  "provider": "adapter-or-feed-name",
  "publisher": null,
  "market": "CN | HK | US | UNKNOWN",
  "instrument_id": null,
  "symbol": null,
  "currency": null,
  "event_ts": null,
  "received_at": "2026-10-16T15:00:00Z",
  "status": "fresh | delayed | stale | unavailable | closed | error",
  "source_url": null,
  "dedup_key": null,
  "related_instruments": [],
  "policy": {
    "authorized_scopes": [],
    "cache_until": null,
    "attribution_required": true,
    "policy_reference": null
  },
  "raw": {}
}
```

Required fields for all emitted records: `schema_version`, `record_type`, `domain`, `id`, `provider`, `market`, `received_at`, `status`, `policy`. This intake layer is finance-only and emitted records use `domain: "finance"`; `market` is `UNKNOWN` when a financial filing is not tied to a single security. `publisher` means the original issuing publisher (e.g. exchange, regulator, media outlet); `provider` means the API/adapter that delivered the item. Never collapse these into a single ambiguous `source`.

`raw` is fixture/debug input only and must not be exposed to end users by default. Do not place secrets or unlicensed full article text in it. `authorized_scopes` defaults to an empty list; a missing or unclear permission is not authorization. Use only the exact reviewed scope(s): `local_personal`, `internal`, `public`, or `redistribution`. Public output is not permitted merely because private personal use is allowed. Record `cache_until` and `policy_reference` only when the relevant terms or permission define them.

## Quote record

Quote records use `record_type: "quote"` and require `symbol`, `currency`, and `event_ts` when supplied by the source. Preserve source timestamp and receive timestamp independently.

```json
{
  "schema_version": "1.0",
  "record_type": "quote",
  "domain": "finance",
  "id": "mock-cn-600000-20261016T070000Z-0001",
  "provider": "mock-fixture",
  "publisher": null,
  "market": "CN",
  "instrument_id": "XSHG:600000",
  "symbol": "600000.SH",
  "currency": "CNY",
  "event_ts": "2026-10-16T07:00:00Z",
  "received_at": "2026-10-16T07:00:01Z",
  "status": "fresh",
  "source_url": null,
  "dedup_key": "mock-fixture|XSHG:600000|tick|0001",
  "related_instruments": [],
  "policy": {
    "authorized_scopes": [],
    "cache_until": null,
    "attribution_required": true,
    "policy_reference": null
  },
  "quote": {
    "data_level": "unknown",
    "price": 10.23,
    "bid": null,
    "ask": null,
    "last_size": null,
    "volume": null,
    "sequence": "0001",
    "session": "continuous",
    "phase": "regular",
    "is_delayed": false
  }
}
```

Quote `status` semantics:

- `fresh`: within the configured freshness threshold for this feed/product and current session.
- `delayed`: feed intentionally supplies delayed data; UI must label delay and its policy threshold.
- `stale`: a prior valid record is retained but outside its applicable freshness threshold; never silently treat it as current for new simulated fills.
- `closed`: the exchange calendar says the market is closed; no update during that interval is expected.
- `unavailable`: no usable last-known-good value exists.
- `error`: a collection, parsing, validation, or provider error occurred.

Thresholds are per provider/product/granularity and derive from a documented service level; do not hard-code one universal “N seconds” threshold. A ticker with no trade or an unchanged price is not, by itself, evidence of a broken feed. Evaluate exchange sessions/holidays, auctions, suspensions, feed heartbeats/batch timestamps, sequence gaps, and per-symbol state. A closed market is not a stale feed.

Any simulated order/fill must retain the normalized quote `id`, provider, event timestamp, receive timestamp, applicable ruleset and whether it was stale/delayed. Do not route to a live broker from this layer.

## News record

News records use `record_type: "news"` and require a title and source attribution. `published_at` must be null when not reliably known; never substitute collection time. Store canonical URL and source record ID if supplied. Article body, image and generated summary are excluded unless the relevant usage rights are explicit.

```json
{
  "schema_version": "1.0",
  "record_type": "news",
  "domain": "finance",
  "id": "mock-news-001",
  "provider": "mock-fixture",
  "publisher": "Example Exchange",
  "market": "CN",
  "instrument_id": null,
  "symbol": null,
  "currency": null,
  "event_ts": "2026-10-16T06:55:00Z",
  "published_at": "2026-10-16T06:55:00Z",
  "received_at": "2026-10-16T06:56:00Z",
  "status": "fresh",
  "source_url": "https://example.invalid/announcement/001",
  "canonical_url": "https://example.invalid/announcement/001",
  "source_id": "001",
  "dedup_key": "url:example.invalid/announcement/001",
  "related_instruments": [
    {"instrument_id": "XSHG:600000", "symbol": "600000.SH", "method": "official-code", "confidence": 1.0}
  ],
  "policy": {
    "authorized_scopes": [],
    "cache_until": null,
    "attribution_required": true,
    "policy_reference": null
  },
  "news": {
    "title": "Mock announcement title",
    "language": "zh-CN",
    "content_type": "announcement",
    "is_update": false,
    "withdrawn_at": null,
    "cluster_id": "url:example.invalid/announcement/001"
  }
}
```

`news.content_type` distinguishes financial announcements/filings from media reporting. SEC company submissions are labeled `SEC Filing` (or an equivalent filing-specific value), not general media news. Preserve `published_at`/source filing time, `received_at`, SEC form, accession identifier, and the filing URL; do not fetch the filing body unless a separate allowed scope explicitly covers it.

For news ingestion, use this deterministic deduplication order: stable provider/source ID; canonicalized URL; normalized-title fingerprint as a last-resort near-duplicate candidate. Near-duplicate clustering must preserve each publisher/provider and URL as provenance and must not delete versions/updates. An event cluster can be shown as one UI card with its source links expanded.

Instrument matching keys should be stable market + exchange + instrument identifier, not ticker text alone. Prefer provider-supplied security IDs or explicit official identifiers. Every inferred match records `method` and `confidence`; low-confidence matches remain “possibly related” and must not be silently represented as certain. Keep a distinction between original fact, provider metadata and any model-generated summary/classification.

## Adapter boundaries

- `QuoteProvider`: returns normalized quote records plus health/heartbeat state; never mixes provider fetching with paper-order business logic.
- `NewsProvider`: returns normalized news records plus per-source health state; use only an approved RSS/API or mock fixture. No stealth, proxy rotation, CAPTCHA circumvention, or unauthorized scraping.
- `Normalizer`: maps source-specific fields into this contract, validates required fields and preserves raw source timestamps/IDs where permitted.
- `FreshnessPolicy`: evaluates records using provider/product granularity, market calendar and session; outputs status without rewriting source timestamps.
- `Deduplicator` and `SecurityMatcher`: deterministic interfaces; return provenance and match evidence rather than discarding it.
- UI/API: disclose provider, original publisher, data status, “as of” time and source link where allowed. If a source is not authorized for display, do not surface its record to end users.

## Prompt Spider visualization projection

The finance-only card projection is documented in `docs/SPIDER-FINANCE-EVENT-CONTRACT.md`. It exposes `kind`, `headline`, optional `summary`, explicit `classification` / `route_reason`, high-confidence `symbol` / `symbols`, provider/publisher, market, source `event_ts`, collector `received_at`, canonical freshness, source/provider URLs, `domain: "finance"`, and an explicit `data_mode: "DEMO" | "LIVE"`. DEMO is the current fixture mode. `data_mode` is independent from freshness and must not imply live collection. Projection applies the exact requested authorization scope; `local_personal` records are rejected by public output. SEC filings stay in a separate filing kind.

## Minimal fixture tests

1. Required envelope validation and missing/unknown timestamp preservation.
2. Source timestamp differs from receive timestamp; neither is substituted for the other.
3. Session calendar marks closed/auction/continuous states; stale thresholds are per feed/granularity.
4. No-trade/unchanged ticker does not mark a healthy feed unavailable.
5. Heartbeat timeout, sequence gap and parse error produce explicit error/stale state.
6. News duplicate ID, canonical URL and near-duplicate title cluster correctly while preserving all source attributions and update versions.
7. Instrument symbol collision is low-confidence unless market/exchange/security ID resolves it.
8. Missing authorization defaults to no permitted scopes; local-personal permission alone never permits public output.
9. A simulated fill carries quote provenance and never reaches a live order gateway.


## Finance observatory API response

`GET /api/finance/observatory` returns the provider-neutral response below. During this integration, the endpoint serves only the local synthetic fixture and marks both the envelope and every item `data_mode: "DEMO"`; it does no network access and does not consult a database. The four typed collections remain separate. `items` is the ordered display projection and may include records from those collections; it does not change their source kind. Spider receives only an explicit projection of approved finance events and never performs collection.

```json
{
  "schema_version": "1.0",
  "domain": "finance",
  "data_mode": "DEMO",
  "generated_at": "2026-10-16T08:00:00Z",
  "demo_notice": "Synthetic fixture only; not live market data and not investment advice.",
  "items": [{
    "kind": "news | filing | announcement | quote | insight",
    "title": "Display headline",
    "summary": null,
    "provider": "adapter-or-fixture-id",
    "publisher": null,
    "market": "CN | HK | US | UNKNOWN",
    "symbol": null,
    "symbols": [],
    "event_ts": null,
    "received_at": "2026-10-16T08:00:00Z",
    "freshness": "fresh | delayed | stale | unavailable | closed | error | unknown",
    "source_url": null,
    "provider_url": null,
    "domain": "finance",
    "data_mode": "DEMO",
    "classification": null,
    "route_reason": null
  }],
  "quotes": [],
  "filings": [],
  "news": [],
  "insights": []
}
```

For `kind: "filing"`, preserve filing form and accession identifier and never place the record in `news`. `classification` and `route_reason` are explicit pipeline outputs; leave null when no rule/model result is available. `source_url` is the original item page; `provider_url` identifies the provider documentation/API when known, not an invented source article. `event_ts` and `received_at` are independent. This version's public endpoint is DEMO-only; provider adapters are opt-in/private and do not feed public output.

The security-settings page is a follow-up stage, not a prerequisite for the current demo. Its future configuration surface must be an explicit non-secret allow-list (provider enable, source/market selection, refresh/freshness thresholds). The admin route is disabled for public deployments and restricted to a private authenticated operator; there is no anonymous first-password bootstrap. Password initialization must be performed by the deployment operator in a private environment. API credentials are not collected in this build; if later supported, accept them only server-side, store them in a private 0600 file, and never return them to the browser. Authentication must not alter `authorized_scopes` or promote `local_personal` to `public`.

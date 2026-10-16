# Prompt Spider finance event contract

This is a visualisation and task-reporting projection over approved finance events. Prompt Spider is not a market/news collector. The live provider adapters remain responsible for fetching, provenance, authorization, and freshness; this view model does not grant new data rights.

## Payload

Each item is a JSON object with the following shape:

```json
{
  "kind": "news | filing | announcement | quote | insight",
  "headline": "Display title; not an inferred fact",
  "summary": null,
  "classification": null,
  "route_reason": null,
  "symbol": null,
  "symbols": [],
  "provider": "adapter or fixture ID",
  "publisher": null,
  "market": "CN | HK | US | UNKNOWN",
  "event_ts": null,
  "received_at": "2026-10-16T15:00:00Z",
  "freshness": "fresh | delayed | stale | unavailable | closed | error",
  "source_url": null,
  "provider_url": null,
  "domain": "finance",
  "data_mode": "DEMO | LIVE"
}
```

`headline` maps from normalized `news.title`; `summary` is optional and must remain null unless an approved source supplies it or a separately permitted summarizer generates it. `classification` and `route_reason` are pipeline metadata, not source facts: leave them null when no explicit rule or classifier result exists. Do not infer stock symbols from ticker-like substrings. `symbol` is set only for one certain instrument association; `symbols` contains only provider-supplied or high-confidence, normalized matches. Use the market-specific entity ID and confidence in the canonical record.

`event_ts` is the source event/publication time in UTC and is never replaced with collection time. `received_at` is the local collector receive time. `freshness` maps to the canonical status after the source-specific threshold is evaluated; it is not the DEMO/LIVE switch.

`data_mode` is an explicit source provenance label, independent of `freshness` and any UI task status. Current presentation and tests use `DEMO` fixtures. An adapter must not mark records `LIVE` unless a reviewed/authorized provider actually fetched the record and its policy permits the intended local use. Missing credentials, missing rights, an unavailable source, and a stale source must never silently fall back to `LIVE`; use `DEMO` only for synthetic fixtures and otherwise represent source health separately.

Public endpoints must continue to use DEMO data. `local_personal` records are never projected to public output. SEC company submissions remain `filing` kind with filing-specific `content_type`, form/accession provenance, and the original SEC link; do not relabel filings as media news. Quotes, filings, news, and insights remain separate `kind` values.

## DEMO fixture

The companion fixture `tests/fixtures/spider_finance_event_demo.json` is synthetic and has no external-source content. It exists only to validate field mapping and must not be treated as a live feed.
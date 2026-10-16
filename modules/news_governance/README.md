# Finance news governance module

This isolated module normalizes provider-neutral finance news records to `docs/DATA-CONTRACT.md` v1.0. It does **not** fetch feeds, call providers, expose records through the 8772 service, or decide whether an external source is authorized. Inputs are mappings/dataclasses from reviewed adapters or local fixtures; tests use mock data only.

## Contract behavior

- Every emitted item is `record_type: "news"` and `domain: "finance"`. `provider` (delivery adapter/feed) stays distinct from `publisher` (original issuing source).
- Preserve the original item URL as `source_url`; derive `canonical_url` separately. Preserve publication/event timestamps independently from `received_at`; unknown or ambiguous source times remain unknown rather than being replaced with collection time.
- SEC company submissions are labeled `news.content_type: "SEC Filing"`. Their filing form, accession number, and filing URL are retained under `news.filing`; the fixture does not include or fetch filing body text.
- Do not retain article body, description, image, or arbitrary provider payload. `raw` is absent by default; opt-in diagnostics are strictly allow-listed.
- `policy.authorized_scopes` accepts only the exact scopes `local_personal`, `internal`, `public`, and `redistribution`; it defaults to `[]`. `public_news()` requires an exact requested-scope match. Personal-local permission does not grant public display or redistribution.
- Deduplication checks provider/source ID, canonical URL, then normalized title. Revisions and cross-publisher records remain in the provenance cluster; updates are not deleted.
- Security associations are stable by market + exchange + instrument ID. Inferred symbol-only matches retain method and confidence; collisions remain possible matches rather than being silently selected.
- `to_spider_event()` projects authorized normalized finance news into the UI card contract in `docs/SPIDER-FINANCE-EVENT-CONTRACT.md`; DEMO/LIVE is explicit, summary/classification/routing reasons are never invented here, and local-personal-only data cannot enter a public projection. SEC filings remain a separate card kind.

Live provider enablement and data-use rights are outside this module. No credentials are read, no external service is contacted, and public-preview behavior remains the responsibility of the existing mock-backed application.

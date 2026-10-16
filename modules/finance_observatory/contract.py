"""Finance-observatory response validation; no provider I/O or database access."""
from __future__ import annotations

from typing import Any, Mapping

SCHEMA_VERSION = "1.0"
DOMAIN = "finance"
KINDS = frozenset({"quote", "filing", "announcement", "news", "insight"})
FRESHNESS = frozenset({"fresh", "delayed", "stale", "unavailable", "closed", "error", "unknown"})
MODES = frozenset({"DEMO", "LIVE"})


def validate_item(item: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    for field in ("kind", "title", "provider", "market", "freshness", "domain", "data_mode"):
        if field not in item or item[field] in (None, ""):
            problems.append(f"missing:{field}")
    if item.get("kind") not in KINDS:
        problems.append("invalid:kind")
    if item.get("domain") != DOMAIN:
        problems.append("invalid:domain")
    if item.get("data_mode") not in MODES:
        problems.append("invalid:data_mode")
    if item.get("freshness") not in FRESHNESS:
        problems.append("invalid:freshness")
    for field in ("symbols",):
        if field in item and (not isinstance(item[field], list) or not all(isinstance(x, str) for x in item[field])):
            problems.append(f"invalid:{field}")
    for field in ("event_ts", "received_at", "source_url", "provider_url", "publisher", "market", "summary"):
        if field in item and item[field] is not None and not isinstance(item[field], str):
            problems.append(f"invalid:{field}")
    if item.get("kind") == "filing":
        if not item.get("form") or not item.get("accession"):
            problems.append("missing:filing_provenance")
    return problems


def validate_response(payload: Mapping[str, Any]) -> list[str]:
    """Validate the provider-neutral envelope; LIVE must not be synthesized from DEMO."""
    problems: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append("invalid:schema_version")
    if payload.get("domain") != DOMAIN:
        problems.append("invalid:domain")
    if payload.get("data_mode") not in MODES:
        problems.append("invalid:data_mode")
    for field in ("items", "quotes", "filings", "news", "insights"):
        if not isinstance(payload.get(field), list):
            problems.append(f"invalid:{field}")
    if problems:
        return problems

    items = payload["items"]
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            problems.append(f"invalid:items[{index}]")
            continue
        problems.extend(f"items[{index}].{problem}" for problem in validate_item(item))
        if item.get("data_mode") != payload.get("data_mode"):
            problems.append(f"items[{index}].mode_mismatch")
    # Typed lists are separately discoverable and must not be collapsed into generic news.
    for field, kind in (("quotes", "quote"), ("filings", "filing"), ("news", "news"), ("insights", "insight")):
        for index, item in enumerate(payload[field]):
            if not isinstance(item, Mapping):
                problems.append(f"invalid:{field}[{index}]")
                continue
            problems.extend(f"{field}[{index}].{problem}" for problem in validate_item(item))
            if item.get("kind") != kind:
                problems.append(f"{field}[{index}].kind_mismatch")
            if item.get("data_mode") != payload.get("data_mode"):
                problems.append(f"{field}[{index}].mode_mismatch")
    return problems


def demo_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return an isolated copy only when the fixture is explicitly DEMO and valid."""
    if payload.get("data_mode") != "DEMO":
        raise ValueError("demo_response only accepts DEMO fixtures")
    problems = validate_response(payload)
    if problems:
        raise ValueError("invalid observatory response: " + ", ".join(problems))
    # JSON round-trip makes nested containers independent and excludes non-JSON objects.
    import json
    return json.loads(json.dumps(payload, ensure_ascii=False))

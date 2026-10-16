"""In-memory deterministic news deduplication/clustering interface.

Records are never deleted on a match: source-specific IDs, URLs, publisher
attribution, and revision variants remain available as provenance.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from threading import RLock
from typing import Any, Mapping, Protocol, Sequence

from .normalizer import normalize_title


@dataclass(frozen=True)
class DedupResult:
    disposition: str  # new, duplicate, update, canonical_match, near_duplicate
    matched_by: str
    cluster_id: str
    record: Mapping[str, Any]
    member_ids: tuple[str, ...]
    provenance: tuple[Mapping[str, Any], ...]


class Deduplicator(Protocol):
    def add(self, record: Mapping[str, Any]) -> DedupResult: ...
    def records(self, cluster_id: str) -> tuple[Mapping[str, Any], ...]: ...


def _candidate_keys(record: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[str, str]] = []
    provider = str(record.get("provider") or "").strip()
    source_id = str(record.get("source_id") or "").strip()
    canonical = str(record.get("canonical_url") or "").strip()
    news = record.get("news") if isinstance(record.get("news"), Mapping) else {}
    title = str(news.get("title") or "").strip()
    if provider and source_id:
        candidates.append(("provider_source_id", f"source:{provider}:{source_id}"))
    if canonical:
        candidates.append(("canonical_url", f"url:{canonical}"))
    title_key = normalize_title(title) if title else ""
    if title_key:
        digest = hashlib.sha256(title_key.encode("utf-8")).hexdigest()[:24]
        candidates.append(("title_fingerprint", f"title:{digest}"))
    if not candidates:
        rid = str(record.get("id") or "unknown")
        candidates.append(("record_id", f"id:{rid}"))
    return tuple(candidates)


def _provenance(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "provider": record.get("provider"),
        "publisher": record.get("publisher"),
        "source_id": record.get("source_id"),
        "source_url": record.get("source_url"),
        "canonical_url": record.get("canonical_url"),
        "published_at": record.get("published_at"),
    }


class InMemoryDeduplicator:
    """Thread-safe test/local implementation of the provider-neutral interface."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._cluster_members: dict[str, list[str]] = {}
        self._key_to_cluster: dict[str, str] = {}

    def add(self, record: Mapping[str, Any]) -> DedupResult:
        if not isinstance(record, Mapping) or not record.get("id"):
            raise ValueError("normalized record with stable id is required")
        current = deepcopy(dict(record))
        record_id = str(current["id"])
        candidates = _candidate_keys(current)
        with self._lock:
            if record_id in self._records:
                stored = self._records[record_id]
                cluster_id = str(stored.get("news", {}).get("cluster_id") or f"id:{record_id}")
                return self._result("duplicate", "record_id", cluster_id, stored)

            matches = [(kind, key, self._key_to_cluster[key])
                       for kind, key in candidates if key in self._key_to_cluster]
            if not matches:
                primary_key = candidates[0][1]
                cluster_id = primary_key
                disposition = "new"
                matched_by = "none"
            else:
                matched_by, _, cluster_id = matches[0]
                if matched_by == "provider_source_id":
                    disposition = "update"
                elif matched_by == "canonical_url":
                    disposition = "canonical_match"
                else:
                    disposition = "near_duplicate"
                # If different strong keys bridge existing clusters, merge their
                # memberships without dropping records or provenance.
                for _, _, other_cluster in matches[1:]:
                    if other_cluster != cluster_id:
                        self._merge(cluster_id, other_cluster)

            news = current.get("news")
            if not isinstance(news, dict):
                news = dict(news or {})
                current["news"] = news
            news["cluster_id"] = cluster_id
            if disposition == "update":
                news["is_update"] = True
            self._records[record_id] = current
            self._cluster_members.setdefault(cluster_id, []).append(record_id)
            for _, key in candidates:
                self._key_to_cluster[key] = cluster_id
            return self._result(disposition, matched_by, cluster_id, current)

    def records(self, cluster_id: str) -> tuple[Mapping[str, Any], ...]:
        with self._lock:
            canonical = self._canonical_cluster(cluster_id)
            return tuple(deepcopy(self._records[rid]) for rid in self._cluster_members.get(canonical, ()))

    def _canonical_cluster(self, cluster_id: str) -> str:
        # Current implementation rewrites aliases on merge; accept only known IDs.
        return self._key_to_cluster.get(cluster_id, cluster_id)

    def _merge(self, winner: str, loser: str) -> None:
        winner = self._canonical_cluster(winner)
        loser = self._canonical_cluster(loser)
        if winner == loser:
            return
        old_members = self._cluster_members.pop(loser, [])
        self._cluster_members.setdefault(winner, []).extend(old_members)
        for rid in old_members:
            self._records[rid]["news"]["cluster_id"] = winner
        for key, value in tuple(self._key_to_cluster.items()):
            if value == loser:
                self._key_to_cluster[key] = winner

    def _result(self, disposition: str, matched_by: str, cluster_id: str,
                record: Mapping[str, Any]) -> DedupResult:
        canonical = self._canonical_cluster(cluster_id)
        ids = tuple(self._cluster_members.get(canonical, ()))
        provenance = tuple(_provenance(self._records[rid]) for rid in ids)
        return DedupResult(disposition, matched_by, canonical, deepcopy(dict(record)), ids, provenance)

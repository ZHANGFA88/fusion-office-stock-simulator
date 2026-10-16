"""Provider-neutral news normalization and governance interfaces."""
from .dedup import Deduplicator, DedupResult, InMemoryDeduplicator
from .normalizer import (
    NewsNormalizationError,
    normalize_news,
    parse_source_time,
    public_news,
)
from .securities import Security, SecurityMatcher, StaticSecurityMatcher
from .prompt_spider import to_spider_event

__all__ = [
    "Deduplicator",
    "DedupResult",
    "InMemoryDeduplicator",
    "NewsNormalizationError",
    "Security",
    "SecurityMatcher",
    "StaticSecurityMatcher",
    "to_spider_event",
    "normalize_news",
    "parse_source_time",
    "public_news",
]

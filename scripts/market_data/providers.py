"""Small provider registry with explicit rights gates; no live adapters included."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence


@dataclass(frozen=True)
class ProviderPolicy:
    """Provider-use permissions as configured from a reviewed terms/contract."""
    permitted_markets: frozenset[str]
    authorized_scopes: frozenset[str] = frozenset()
    provider_enabled: bool = False
    policy_reference: str | None = None

    def permits(self, market: str, scope: str) -> bool:
        if not self.provider_enabled:
            return False
        if market.upper() not in {m.upper() for m in self.permitted_markets}:
            return False
        return scope in self.authorized_scopes


class QuoteProvider(Protocol):
    provider_id: str
    priority: int
    policy: ProviderPolicy

    def fetch(self, symbols: Sequence[str]) -> Mapping[str, object]:
        """Return mapped source rows or a provider-neutral error envelope."""
        ...


class ProviderRegistry:
    """Select only explicitly enabled providers permitted for this request scope."""

    def __init__(self) -> None:
        self._providers: dict[str, QuoteProvider] = {}

    def register(self, provider: QuoteProvider) -> None:
        if not provider.provider_id:
            raise ValueError("provider_id is required")
        if provider.provider_id in self._providers:
            raise ValueError(f"provider already registered: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def eligible(self, *, market: str, scope: str) -> tuple[QuoteProvider, ...]:
        selected = [
            provider for provider in self._providers.values()
            if provider.policy.permits(market, scope)
        ]
        return tuple(sorted(selected, key=lambda provider: (provider.priority, provider.provider_id)))

    def fallback_chain(self, *, market: str, scope: str) -> tuple[QuoteProvider, ...]:
        """Return an ordered chain; caller must keep provider provenance per record."""
        return self.eligible(market=market, scope=scope)

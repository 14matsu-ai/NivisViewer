"""Persistent, query-only MRU for the Browser search control."""

from __future__ import annotations

from collections.abc import Iterable


class BrowserSearchHistory:
    """Own normalized search queries without owning the active filter."""

    DEFAULT_LIMIT = 50
    MAX_LIMIT = 1000

    def __init__(
        self,
        entries: Iterable[object] = (),
        *,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        self._limit = self._normalize_limit(limit)
        self._entries: list[str] = []
        self.replace(entries)

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def entries(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def record(self, query: object) -> bool:
        normalized = self._normalize_query(query)
        if not normalized or self._limit == 0:
            return False
        key = normalized.casefold()
        updated = [
            existing
            for existing in self._entries
            if existing.casefold() != key
        ]
        updated.insert(0, normalized)
        del updated[self._limit :]
        if updated == self._entries:
            return False
        self._entries = updated
        return True

    def replace(self, entries: Iterable[object]) -> bool:
        normalized: list[str] = []
        seen: set[str] = set()
        if self._limit > 0:
            for entry in entries:
                query = self._normalize_query(entry)
                key = query.casefold()
                if not query or key in seen:
                    continue
                seen.add(key)
                normalized.append(query)
                if len(normalized) >= self._limit:
                    break
        if normalized == self._entries:
            return False
        self._entries = normalized
        return True

    def set_limit(self, limit: int) -> bool:
        normalized = self._normalize_limit(limit)
        changed = normalized != self._limit
        self._limit = normalized
        if len(self._entries) > normalized:
            del self._entries[normalized:]
            changed = True
        return changed

    def clear(self) -> bool:
        if not self._entries:
            return False
        self._entries.clear()
        return True

    @classmethod
    def _normalize_limit(cls, limit: object) -> int:
        if isinstance(limit, bool):
            return cls.DEFAULT_LIMIT
        try:
            value = int(limit)
        except (TypeError, ValueError):
            return cls.DEFAULT_LIMIT
        return max(0, min(cls.MAX_LIMIT, value))

    @staticmethod
    def _normalize_query(query: object) -> str:
        return query.strip() if isinstance(query, str) else ""


__all__ = ["BrowserSearchHistory"]

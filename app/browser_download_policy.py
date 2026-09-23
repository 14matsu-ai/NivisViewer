"""Small, bounded download-recovery policies; no filesystem or Qt calls."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Hashable


@dataclass
class RefreshBurst:
    """Trailing-edge debounce with a deadline that subsequent events cannot move."""
    quiet_ms: int = 350
    maximum_ms: int = 2000
    started_at: float | None = None

    def delay_ms(self, now: float) -> int:
        if self.started_at is None:
            self.started_at = float(now)
        elapsed = max(0.0, float(now) - self.started_at)
        return max(0, min(self.quiet_ms, ceil(self.maximum_ms - elapsed * 1000)))

    def reset(self) -> None:
        self.started_at = None


@dataclass
class _Retry:
    generation: int
    expires_at: float
    attempts: int = 0
    due_at: float | None = None


class DownloadRetryBudget:
    """Finite retry opportunities for failed identities, not an image job queue.

    Exhausted identities stay in the bounded ledger. Repeated FAILED callbacks
    must not restart a broken file's retry allowance. Admission stops when the
    ledger is full; a new directory or explicit Refresh resets it. A changed
    content fingerprint is a different identity. Delays count from a failure,
    not from a request that is still running.
    """
    def __init__(self, *, delays=(0.5, 1.0, 2.0, 4.0, 8.0),
                 lifetime: float = 30.0, capacity: int = 512) -> None:
        self.delays = tuple(float(value) for value in delays)
        if not self.delays or any(value <= 0 for value in self.delays):
            raise ValueError("Retry delays must be positive")
        if lifetime <= 0 or capacity < 1:
            raise ValueError("Retry lifetime and capacity must be positive")
        self.lifetime = float(lifetime)
        self.capacity = int(capacity)
        self._entries: dict[Hashable, _Retry] = {}

    def clear(self) -> None:
        self._entries.clear()

    def failed(self, key: Hashable, generation: int, now: float) -> bool:
        entry = self._entries.get(key)
        if entry is None:
            if len(self._entries) >= self.capacity:
                return False
            entry = _Retry(int(generation), float(now) + self.lifetime)
            self._entries[key] = entry
        entry.generation = int(generation)
        if now >= entry.expires_at or entry.attempts >= len(self.delays):
            entry.due_at = None
            return False
        if entry.due_at is None:
            entry.due_at = min(entry.expires_at, float(now) + self.delays[entry.attempts])
        return True

    def resolved(self, key: Hashable) -> None:
        self._entries.pop(key, None)

    def discard_pending(self, key: Hashable) -> None:
        entry = self._entries.get(key)
        if entry is not None:
            entry.due_at = None

    def next_delay_ms(self, now: float) -> int | None:
        due = []
        for entry in self._entries.values():
            if now >= entry.expires_at:
                entry.due_at = None
            if entry.due_at is not None:
                due.append(entry.due_at)
        return max(0, ceil((min(due) - now) * 1000)) if due else None

    def take_due(self, now: float, *, limit: int = 8) -> tuple[tuple[Hashable, int], ...]:
        ready = []
        for key, entry in self._entries.items():
            if now >= entry.expires_at:
                entry.due_at = None
            if entry.due_at is None or entry.due_at > now:
                continue
            if len(ready) >= max(0, int(limit)):
                break
            entry.due_at = None
            entry.attempts += 1
            ready.append((key, entry.generation))
        return tuple(ready)

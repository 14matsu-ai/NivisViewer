from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from .config_manager import ConfigManager


@dataclass(frozen=True)
class DestinationHistoryEntry:
    path: str
    display_label: str
    last_used_at: float
    exists: bool | None = None


class DestinationHistoryStore:
    KEY = "file_operation_destinations"

    def __init__(self, config: ConfigManager, *, limit: int = 15) -> None:
        self.config = config
        self.limit = max(1, min(20, int(limit)))

    def list(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries())

    def entries(self) -> tuple[DestinationHistoryEntry, ...]:
        raw = self.config.get(self.KEY, [])
        if not isinstance(raw, list):
            return ()
        result: list[DestinationHistoryEntry] = []
        seen: set[str] = set()
        for value in raw:
            if isinstance(value, str):
                path = value
                label = Path(value).name or value
                last_used = 0.0
            elif isinstance(value, dict):
                path = value.get("path", "")
                label = value.get("display_label", "")
                last_used = value.get("last_used_at", 0.0)
            else:
                continue
            if not isinstance(path, str) or not path:
                continue
            normalized = self._absolute(path)
            key = self._key(normalized)
            if key in seen:
                continue
            seen.add(key)
            try:
                timestamp = float(last_used)
            except (TypeError, ValueError):
                timestamp = 0.0
            result.append(
                DestinationHistoryEntry(
                    normalized,
                    (
                        str(label)
                        if isinstance(label, str) and label
                        else Path(normalized).name or normalized
                    ),
                    timestamp,
                )
            )
            if len(result) >= self.limit:
                break
        return tuple(result)

    def record(self, destination: str | Path) -> None:
        normalized = self._absolute(destination)
        key = self._key(normalized)
        entries = [
            DestinationHistoryEntry(
                normalized,
                Path(normalized).name or normalized,
                time.time(),
            )
        ]
        entries.extend(
            entry for entry in self.entries() if self._key(entry.path) != key
        )
        values = [
            {
                "path": entry.path,
                "display_label": entry.display_label,
                "last_used_at": entry.last_used_at,
                "exists": entry.exists,
            }
            for entry in entries[: self.limit]
        ]
        self.config.apply({self.KEY: values}, save=True)

    def clear(self) -> None:
        self.config.apply({self.KEY: []}, save=True)

    @staticmethod
    def _absolute(path: str | Path) -> str:
        return os.path.abspath(os.path.normpath(os.path.expanduser(os.fspath(path))))

    @classmethod
    def _key(cls, path: str | Path) -> str:
        return os.path.normcase(cls._absolute(path)).casefold()

from __future__ import annotations

from .i18n import tr


from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from threading import RLock
from typing import Callable, Iterable

from .archive_backend import ArchiveBackendError
from .seven_zip_process import SevenZipProcessRunner


@dataclass(frozen=True)
class SevenZipInfo:
    executable_path: str
    available: bool
    version_text: str | None
    error_message: str | None


Probe = Callable[[str], SevenZipInfo]


class SevenZipLocator:
    def __init__(
        self,
        *,
        application_dir: str | Path | None = None,
        environment: dict[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        probe: Probe | None = None,
        probe_timeout_seconds: float = 3.0,
    ) -> None:
        self.application_dir = Path(application_dir or Path(__file__).resolve().parents[1])
        self.environment = environment if environment is not None else os.environ
        self._which = which
        self._probe_override = probe
        self.probe_timeout_seconds = max(0.2, float(probe_timeout_seconds))
        self._lock = RLock()
        self._cache_key: str | None = None
        self._cached_info: SevenZipInfo | None = None

    def locate(
        self,
        explicit_path: str | Path | None = None,
        *,
        force: bool = False,
    ) -> SevenZipInfo:
        explicit = str(explicit_path or "").strip().strip('"')
        cache_key = explicit.casefold()
        with self._lock:
            if not force and cache_key == self._cache_key and self._cached_info is not None:
                return self._cached_info

        if explicit:
            candidates = (Path(explicit),)
        else:
            candidates = tuple(self._automatic_candidates())

        last_error: str | None = None
        seen: set[str] = set()
        for candidate in candidates:
            absolute = Path(os.path.abspath(os.path.normpath(os.fspath(candidate))))
            key = os.path.normcase(str(absolute)).casefold()
            if key in seen:
                continue
            seen.add(key)
            try:
                if not absolute.is_file():
                    last_error = tr('実行ファイルではありません: {p0}', p0=absolute)
                    continue
            except OSError as exc:
                last_error = str(exc)
                continue
            info = self.probe(str(absolute))
            if info.available:
                return self._store(cache_key, info)
            last_error = info.error_message
            if explicit:
                break

        return self._store(
            cache_key,
            SevenZipInfo(
                executable_path=(
                    str(Path(os.path.abspath(os.path.normpath(explicit))))
                    if explicit
                    else ""
                ),
                available=False,
                version_text=None,
                error_message=last_error or tr('7-Zipが見つかりません'),
            ),
        )

    def probe(self, executable_path: str | Path) -> SevenZipInfo:
        absolute = str(Path(executable_path).absolute())
        if self._probe_override is not None:
            return self._probe_override(absolute)
        try:
            result = SevenZipProcessRunner(absolute).run(
                ["i", "-sccUTF-8"],
                timeout_seconds=self.probe_timeout_seconds,
                maximum_stdout_bytes=2 * 1024 * 1024,
            )
        except (ArchiveBackendError, OSError, ValueError) as exc:
            return SevenZipInfo(absolute, False, None, str(exc))
        text = result.stdout.decode("utf-8", errors="replace")
        if result.return_code not in {0, 1} or "7-Zip" not in text:
            return SevenZipInfo(
                absolute,
                False,
                None,
                tr('7-Zipの情報取得コマンドを確認できませんでした'),
            )
        version_line = next(
            (line.strip() for line in text.splitlines() if "7-Zip" in line),
            None,
        )
        return SevenZipInfo(absolute, True, version_line, None)

    def reset(self) -> None:
        with self._lock:
            self._cache_key = None
            self._cached_info = None

    def _automatic_candidates(self) -> Iterable[Path]:
        for name in ("7z.exe", "7zz.exe"):
            yield self.application_dir / name
            yield self.application_dir / "tools" / "7-Zip" / name

        program_roots: list[str] = []
        for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            value = self.environment.get(variable, "")
            if value and value.casefold() not in {item.casefold() for item in program_roots}:
                program_roots.append(value)
        for root in program_roots:
            for name in ("7z.exe", "7zz.exe"):
                yield Path(root) / "7-Zip" / name

        for name in ("7z.exe", "7zz.exe"):
            located = self._which(name)
            if located:
                yield Path(located)

    def _store(self, cache_key: str, info: SevenZipInfo) -> SevenZipInfo:
        with self._lock:
            self._cache_key = cache_key
            self._cached_info = info
        return info

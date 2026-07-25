from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
from threading import RLock
from typing import Callable, Iterable

from .archive_backend import ArchiveBackendError
from .windows_file_association import WindowsFileAssociationResolver
from .winrar_process import WinRARProcessRunner


@dataclass(frozen=True)
class WinRARInfo:
    executable_path: str
    available: bool
    version_text: str | None
    error_message: str | None
    discovery_source: str | None = None
    installation_executable_path: str | None = None


Probe = Callable[[str], WinRARInfo]


class WinRARLocator:
    def __init__(
        self,
        *,
        association_resolver: WindowsFileAssociationResolver | None = None,
        environment: dict[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        probe: Probe | None = None,
        probe_timeout_seconds: float = 3.0,
    ) -> None:
        self.association_resolver = (
            association_resolver or WindowsFileAssociationResolver()
        )
        self.environment = environment if environment is not None else os.environ
        self._which = which
        self._probe_override = probe
        self.probe_timeout_seconds = max(0.2, float(probe_timeout_seconds))
        self._lock = RLock()
        self._cache: dict[tuple[str, str], WinRARInfo] = {}

    def locate(
        self,
        explicit_path: str | Path | None = None,
        *,
        extension: str = ".rar",
        force: bool = False,
    ) -> WinRARInfo:
        explicit = str(explicit_path or "").strip().strip('"')
        extension = extension.casefold()
        cache_key = (explicit.casefold(), extension)
        with self._lock:
            if not force and cache_key in self._cache:
                return self._cache[cache_key]

        last_error: str | None = None
        seen: set[str] = set()
        for candidate, source, installation_path in self._candidates(
            explicit, extension
        ):
            console = self._console_candidate(candidate)
            if console is None:
                last_error = f"WinRARの公式コンソールCLIが見つかりません: {candidate}"
                continue
            absolute = Path(os.path.abspath(os.path.normpath(os.fspath(console))))
            key = os.path.normcase(str(absolute)).casefold()
            if key in seen:
                continue
            seen.add(key)
            try:
                if not absolute.is_file():
                    last_error = f"実行ファイルではありません: {absolute}"
                    continue
            except OSError as exc:
                last_error = str(exc)
                continue
            info = self.probe(str(absolute))
            if info.available:
                final = WinRARInfo(
                    info.executable_path,
                    True,
                    info.version_text,
                    None,
                    source,
                    str(installation_path or candidate),
                )
                return self._store(cache_key, final)
            last_error = info.error_message

        return self._store(
            cache_key,
            WinRARInfo(
                "",
                False,
                None,
                last_error or "WinRARの公式コンソールCLIが見つかりません",
            ),
        )

    def probe(self, executable_path: str | Path) -> WinRARInfo:
        absolute = str(Path(executable_path).absolute())
        if self._probe_override is not None:
            return self._probe_override(absolute)
        try:
            result = WinRARProcessRunner(absolute).run(
                ["-iver"],
                timeout_seconds=self.probe_timeout_seconds,
                maximum_stdout_bytes=1024 * 1024,
            )
        except (ArchiveBackendError, OSError, ValueError) as exc:
            return WinRARInfo(absolute, False, None, str(exc))
        text = result.stdout.decode("ascii", errors="replace").strip()
        if result.return_code != 0 or not re.search(r"\d+\.\d+", text):
            return WinRARInfo(
                absolute,
                False,
                None,
                "WinRAR公式CLIのバージョンを確認できませんでした",
            )
        return WinRARInfo(absolute, True, text.splitlines()[0], None)

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()
        self.association_resolver.reset()

    def _candidates(
        self,
        explicit: str,
        extension: str,
    ) -> Iterable[tuple[Path, str, Path | None]]:
        if explicit:
            yield Path(explicit), "explicit", Path(explicit)

        association = self.association_resolver.resolve_archive_extension(extension)
        if (
            association.application_kind == "winrar"
            and association.executable_path
        ):
            associated = Path(association.executable_path)
            yield associated, "association", associated

        roots: list[str] = []
        for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            value = self.environment.get(variable, "")
            if value and value.casefold() not in {item.casefold() for item in roots}:
                roots.append(value)
        for root in roots:
            candidate = Path(root) / "WinRAR" / "WinRAR.exe"
            yield candidate, "standard", candidate

        located = self._which("WinRAR.exe")
        if located:
            candidate = Path(located)
            yield candidate, "path", candidate

    @staticmethod
    def _console_candidate(candidate: Path) -> Path | None:
        name = candidate.name.casefold()
        if name in {"unrar.exe", "rar.exe"}:
            return candidate
        if name != "winrar.exe":
            return None
        for sibling_name in ("UnRAR.exe", "Rar.exe"):
            sibling = candidate.with_name(sibling_name)
            try:
                if sibling.is_file():
                    return sibling
            except OSError:
                continue
        return None

    def _store(self, key: tuple[str, str], info: WinRARInfo) -> WinRARInfo:
        with self._lock:
            self._cache[key] = info
        return info

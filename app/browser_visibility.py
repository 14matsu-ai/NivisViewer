from __future__ import annotations

from dataclasses import dataclass
import stat


@dataclass(frozen=True)
class BrowserVisibilityPolicy:
    """Worker-side policy for filesystem entries shown by BrowserWindow."""

    show_hidden_items: bool = True
    show_unsupported_files: bool = True
    show_system_items: bool = False

    def allows(
        self,
        *,
        hidden: bool,
        system: bool,
        supported: bool,
        is_directory: bool,
    ) -> bool:
        if system and not self.show_system_items:
            return False
        if hidden and not self.show_hidden_items:
            return False
        if not is_directory and not supported and not self.show_unsupported_files:
            return False
        return True


LEGACY_SUPPORTED_ITEMS_POLICY = BrowserVisibilityPolicy(
    show_hidden_items=False,
    show_unsupported_files=False,
    show_system_items=False,
)


def filesystem_visibility_flags(name: str, file_attributes: int) -> tuple[bool, bool]:
    """Return hidden/system flags without performing additional filesystem I/O."""

    hidden_attribute = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
    system_attribute = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
    hidden = bool(file_attributes & hidden_attribute) or name.startswith(".")
    system = bool(file_attributes & system_attribute)
    return hidden, system

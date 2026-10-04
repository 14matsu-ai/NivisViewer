from __future__ import annotations

VECTOR_IMAGE_EXTENSIONS = frozenset({".svg", ".ai"})

PSD_EXTENSIONS = frozenset({".psd", ".psb"})
KRA_EXTENSIONS = frozenset({".kra"})
ORA_EXTENSIONS = frozenset({".ora"})
CLIP_EXTENSIONS = frozenset({".clip"})
XCF_EXTENSIONS = frozenset({".xcf"})
CREATIVE_PROJECT_EXTENSIONS = frozenset(
    KRA_EXTENSIONS | ORA_EXTENSIONS | CLIP_EXTENSIONS | XCF_EXTENSIONS
)

IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".avif",
        ".jxl",
        ".bmp",
        ".gif",
        ".tif",
        ".tiff",
        ".ico",
        ".psd",
        ".psb",
        ".kra",
        ".ora",
        ".clip",
        ".xcf",
        ".svg",
        ".ai",
    }
)
ZIP_ARCHIVE_EXTENSIONS = frozenset({".zip", ".cbz"})
EXTERNAL_ARCHIVE_EXTENSIONS = frozenset({".rar", ".cbr", ".7z", ".cb7"})
ARCHIVE_EXTENSIONS = frozenset(
    ZIP_ARCHIVE_EXTENSIONS | EXTERNAL_ARCHIVE_EXTENSIONS
)
PDF_EXTENSIONS = frozenset({".pdf"})
BOOK_FILE_EXTENSIONS = frozenset(
    IMAGE_EXTENSIONS | ARCHIVE_EXTENSIONS | PDF_EXTENSIONS
)

FORMAT_CATEGORIES = {
    "image": IMAGE_EXTENSIONS,
    "archive": ARCHIVE_EXTENSIONS,
    "pdf": PDF_EXTENSIONS,
}

# Runtime policy is resolved once at application startup. Keep capabilities
# above stable for file associations and configuration validation.
ENABLED_IMAGE_EXTENSIONS = set(IMAGE_EXTENSIONS - XCF_EXTENSIONS)
ENABLED_BOOK_FILE_EXTENSIONS = set(BOOK_FILE_EXTENSIONS - XCF_EXTENSIONS)


def configure_vector_loading(*, ai=True, svg=True):
    for suffix, enabled in (('.ai', ai), ('.svg', svg)):
        for extensions in (ENABLED_IMAGE_EXTENSIONS, ENABLED_BOOK_FILE_EXTENSIONS):
            if enabled:
                extensions.add(suffix)
            else:
                extensions.discard(suffix)


def configure_xcf_loading(enabled: bool) -> None:
    for extensions in (ENABLED_IMAGE_EXTENSIONS, ENABLED_BOOK_FILE_EXTENSIONS):
        if enabled:
            extensions.update(XCF_EXTENSIONS)
        else:
            extensions.difference_update(XCF_EXTENSIONS)


def xcf_loading_enabled() -> bool:
    return ".xcf" in ENABLED_IMAGE_EXTENSIONS


def normalize_extensions(extensions) -> tuple[str, ...]:
    supported = BOOK_FILE_EXTENSIONS
    normalized: set[str] = set()
    for extension in extensions:
        value = str(extension).strip().casefold()
        if value and not value.startswith("."):
            value = f".{value}"
        if value in supported:
            normalized.add(value)
    return tuple(sorted(normalized))

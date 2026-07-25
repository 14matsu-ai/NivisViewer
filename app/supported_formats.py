from __future__ import annotations

IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".gif",
        ".tif",
        ".tiff",
        ".ico",
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

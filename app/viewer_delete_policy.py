"""Selection of a displayed real image for Viewer Delete."""


def select_delete_page(
    page_indexes: tuple[int, ...], mode: str, cursor_page: int | None = None,
) -> int | None:
    if mode == "disabled" or not page_indexes:
        return None
    if len(page_indexes) == 1:
        return page_indexes[0]
    if mode == "single":
        return None
    if mode == "spread_front":
        return min(page_indexes)
    if mode == "spread_back":
        return max(page_indexes)
    if mode == "spread_cursor" and cursor_page in page_indexes:
        return cursor_page
    return None

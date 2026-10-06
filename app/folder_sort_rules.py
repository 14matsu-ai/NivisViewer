"""Lexical folder sort inheritance; no filesystem access or GUI dependencies."""

from __future__ import annotations

import os
from pathlib import Path

from .browser_sort import BrowserSortKey, BrowserSortOrder, BrowserSortPolicy


SORT_RULES_KEY = "browser_folder_sort_rules"
SCOPE_LABELS = {
    "folder": "このフォルダのみ",
    "subtree": "このフォルダと階下",
    "descendants": "このフォルダを除いた階下",
}


def folder_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path)))).casefold()


def normalize_folder_sort_rules(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rules: dict[str, dict[str, str]] = {}
    for raw in value:
        if not isinstance(raw, dict):
            continue
        path = raw.get("path")
        scope = raw.get("scope", "subtree")
        mode = raw.get("mode", "specified")
        if (not isinstance(path, str) or not path or "\0" in path
                or not os.path.isabs(path) or not isinstance(scope, str)
                or scope not in SCOPE_LABELS or not isinstance(mode, str)
                or mode not in {"specified", "default"}):
            continue
        key = raw.get("sort_key", "name")
        order = raw.get("sort_order", "ascending")
        if mode == "default":
            key, order = "name", "ascending"
        if not isinstance(key, str) or not isinstance(order, str):
            continue
        if key not in {item.value for item in BrowserSortKey} or order not in {
            item.value for item in BrowserSortOrder
        }:
            continue
        path = os.path.normpath(path)
        rules[folder_key(path)] = {
            "path": path, "scope": scope, "mode": mode,
            "sort_key": key, "sort_order": order,
        }
    return list(rules.values())


def matching_folder_sort_rule(
    path: str | Path | None, rules: list[dict[str, str]],
) -> dict[str, str] | None:
    if path is None:
        return None
    target = folder_key(path)
    match = None
    specificity = -1
    for rule in rules:
        parent = folder_key(rule["path"])
        same = target == parent
        below = not same and target.startswith(parent.rstrip(os.sep) + os.sep)
        scope = rule["scope"]
        applies = ((same and scope != "descendants")
                   or (below and scope != "folder"))
        if applies and len(parent) > specificity:
            match, specificity = rule, len(parent)
    return match


def folder_sort_policy(
    path: str | Path | None, rules: list[dict[str, str]], default: BrowserSortPolicy,
) -> BrowserSortPolicy:
    rule = matching_folder_sort_rule(path, rules)
    if rule is None or rule["mode"] == "default":
        return default
    return BrowserSortPolicy(
        BrowserSortKey(rule["sort_key"]), BrowserSortOrder(rule["sort_order"]),
        default.folders_first, default.random_seed,
    )

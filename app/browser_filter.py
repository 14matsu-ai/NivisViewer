"""Composable in-memory predicates for the Browser visible-item pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .browser_model import BrowserItem


class BrowserItemPredicate(Protocol):
    def matches(self, item: BrowserItem) -> bool: ...


class RatingFilterMode(str, Enum):
    OFF = "off"
    AT_LEAST = "at_least"
    EQUAL = "equal"
    UNRATED = "unrated"


def normalize_rating_filter_mode(value: object) -> RatingFilterMode:
    if isinstance(value, RatingFilterMode):
        return value
    try:
        return RatingFilterMode(str(value))
    except ValueError:
        return RatingFilterMode.OFF


@dataclass(frozen=True)
class BrowserSearchPredicate:
    """Phase-one plain-text predicate; replaceable by a future parser."""

    query: str = ""
    _normalized_query: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_normalized_query", self.query.strip().casefold())

    @property
    def normalized_query(self) -> str:
        return self._normalized_query

    def matches(self, item: BrowserItem) -> bool:
        query = self.normalized_query
        return not query or query in item.display_name.casefold()


@dataclass(frozen=True)
class BrowserRatingPredicate:
    mode: RatingFilterMode = RatingFilterMode.OFF
    reference: int = 0

    def matches(self, item: BrowserItem) -> bool:
        if self.mode is RatingFilterMode.OFF:
            return True
        if self.mode is RatingFilterMode.UNRATED:
            return item.rating is None
        if item.rating is None:
            return False
        reference = max(1, min(5, int(self.reference)))
        if self.mode is RatingFilterMode.EQUAL:
            return item.rating == reference
        return item.rating >= reference


@dataclass(frozen=True)
class BrowserFilterState:
    search_text: str = ""
    rating_mode: RatingFilterMode = RatingFilterMode.OFF
    rating_reference: int = 0
    _predicates: tuple[BrowserItemPredicate, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        mode = normalize_rating_filter_mode(self.rating_mode)
        try:
            reference = int(self.rating_reference)
        except (TypeError, ValueError):
            reference = 0
        if mode in {RatingFilterMode.AT_LEAST, RatingFilterMode.EQUAL}:
            reference = max(1, min(5, reference))
        else:
            reference = 0
        object.__setattr__(self, "search_text", str(self.search_text))
        object.__setattr__(self, "rating_mode", mode)
        object.__setattr__(self, "rating_reference", reference)
        object.__setattr__(
            self,
            "_predicates",
            (
                BrowserSearchPredicate(self.search_text),
                BrowserRatingPredicate(mode, reference),
            ),
        )

    @classmethod
    def normalized(
        cls,
        *,
        search_text: object = "",
        rating_mode: object = RatingFilterMode.OFF,
        rating_reference: object = 0,
    ) -> BrowserFilterState:
        mode = normalize_rating_filter_mode(rating_mode)
        try:
            reference = int(rating_reference)
        except (TypeError, ValueError):
            reference = 0
        if mode in {RatingFilterMode.AT_LEAST, RatingFilterMode.EQUAL}:
            reference = max(1, min(5, reference))
        else:
            reference = 0
        return cls(str(search_text), mode, reference)

    @property
    def active(self) -> bool:
        return bool(self.search_text.strip()) or self.rating_mode is not RatingFilterMode.OFF

    def predicates(self) -> tuple[BrowserItemPredicate, ...]:
        return self._predicates

    def matches(self, item: BrowserItem) -> bool:
        return all(predicate.matches(item) for predicate in self.predicates())


__all__ = [
    "BrowserFilterState",
    "BrowserItemPredicate",
    "BrowserRatingPredicate",
    "BrowserSearchPredicate",
    "RatingFilterMode",
    "normalize_rating_filter_mode",
]

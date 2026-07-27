from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable


class ViewerSlotState(str, Enum):
    EMPTY = "empty"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ViewerDisplaySlot:
    side: str
    page_index: int
    page_identity: str
    image_id: str
    request_id: int
    generation: int
    state: ViewerSlotState = ViewerSlotState.EMPTY
    error: str | None = None

    def transition(
        self,
        state: ViewerSlotState,
        *,
        error: str | None = None,
    ) -> ViewerDisplaySlot:
        return replace(self, state=state, error=error)


@dataclass(frozen=True)
class ViewerDisplayUnit:
    request_id: int
    generation: int
    focused_page_identity: str | None
    slots: tuple[ViewerDisplaySlot, ...]

    @classmethod
    def create(
        cls,
        *,
        request_id: int,
        generation: int,
        focused_page_identity: str | None,
        pages: Iterable[tuple[int, str, str]],
    ) -> ViewerDisplayUnit:
        raw_pages = tuple(pages)
        sides = ("center",) if len(raw_pages) == 1 else ("left", "right")
        slots = tuple(
            ViewerDisplaySlot(
                side=sides[position],
                page_index=page_index,
                page_identity=page_identity,
                image_id=image_id,
                request_id=request_id,
                generation=generation,
                state=ViewerSlotState.LOADING,
            )
            for position, (page_index, page_identity, image_id) in enumerate(raw_pages)
        )
        return cls(request_id, generation, focused_page_identity, slots)

    @classmethod
    def empty(cls) -> ViewerDisplayUnit:
        return cls(0, 0, None, ())

    def slot_for(self, page_index: int, image_id: str) -> ViewerDisplaySlot | None:
        return next(
            (
                slot
                for slot in self.slots
                if slot.page_index == page_index and slot.image_id == image_id
            ),
            None,
        )

    def transition(
        self,
        *,
        page_index: int,
        image_id: str,
        generation: int,
        state: ViewerSlotState,
        error: str | None = None,
    ) -> ViewerDisplayUnit:
        if generation != self.generation:
            return self
        changed = False
        slots: list[ViewerDisplaySlot] = []
        for slot in self.slots:
            if slot.page_index == page_index and slot.image_id == image_id:
                slots.append(slot.transition(state, error=error))
                changed = True
            else:
                slots.append(slot)
        return replace(self, slots=tuple(slots)) if changed else self

    def cancel_loading(self) -> ViewerDisplayUnit:
        return replace(
            self,
            slots=tuple(
                slot.transition(ViewerSlotState.CANCELLED)
                if slot.state is ViewerSlotState.LOADING
                else slot
                for slot in self.slots
            ),
        )

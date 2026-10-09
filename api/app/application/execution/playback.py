"""Common execution history reducer used by full and checkpoint replay."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.domain.models.playback import PlaybackBoundary

_KINDS = ("run", "step", "approval", "artifact", "message")


def reduce_facts(
    facts: list[dict[str, Any]],
    boundary: tuple[int, int] | PlaybackBoundary,
    *,
    initial_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fold public facts through a consistent committed boundary.

    Formal and progress watermarks are independent partial-order components.
    When a typed boundary is supplied, observed order additionally restricts
    the fold to the exact committed journal prefix.
    """
    if isinstance(boundary, PlaybackBoundary):
        formal_limit = boundary.formal_position
        progress_limit = boundary.progress_position
        order_limit: int | None = boundary.observed_order
    else:
        if len(boundary) != 2 or any(value < 0 for value in boundary):
            raise ValueError("playback boundary must contain nonnegative dual watermarks")
        formal_limit, progress_limit = boundary
        order_limit = None

    state = deepcopy(initial_state) if initial_state is not None else {}
    for kind in _KINDS:
        state.setdefault(kind, {})
    replacements = state.setdefault("replacement_step_ids", {})

    # Repositories supply durable order. Sorting here makes the pure adapter
    # safe for callers that include it, while retaining bundle order for ties.
    ordered = sorted(
        enumerate(facts),
        key=lambda item: (item[1].get("observed_order", 0), item[0]),
    )
    seen: set[tuple[int | None, str, str, str]] = set()
    for _index, fact in ordered:
        formal, progress = fact["position"]
        observed_order = fact.get("observed_order")
        if formal > formal_limit or progress > progress_limit:
            continue
        if order_limit is not None and (observed_order is None or observed_order > order_limit):
            continue
        kind = fact["kind"]
        if kind not in _KINDS:
            raise ValueError(f"unsupported playback fact kind: {kind}")
        entity_id = str(fact["id"])
        patch = fact["patch"]
        marker = (observed_order, kind, entity_id, repr(sorted(patch.items())))
        if marker in seen:
            continue
        seen.add(marker)
        if kind == "step" and patch.get("removed") is True:
            state["step"].pop(entity_id, None)
            replacement = patch.get("replacement_step_id")
            if replacement:
                replacements[entity_id] = replacement
            continue
        state[kind].setdefault(entity_id, {}).update(patch)
    return state


__all__ = ["reduce_facts"]

"""Keeping a recording free of noise: repeated edits to one field become one FILL.

The page already merges keystrokes: it reports a field once per committed edit, never per
key. A person can still commit the same field twice in a row (type, tab away, come back,
correct it); only the final value matters, so the later fill replaces the earlier one and
keeps its place and id.
"""

from collections.abc import Sequence

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.domain.recording import DraftStep


def merge_fill(steps: Sequence[DraftStep], draft: DraftStep) -> tuple[tuple[DraftStep, ...], bool]:
    """The steps with the draft appended, or replacing the fill it repeats; and whether it did."""
    last = steps[-1] if steps else None
    repeats = (
        last is not None
        and draft.action is ActionType.FILL
        and last.action is ActionType.FILL
        and draft.element_key is not None
        and draft.element_key == last.element_key
    )
    if last is not None and repeats:
        merged = draft.model_copy(update={"index": last.index, "step_id": last.step_id})
        return (*steps[:-1], merged), True
    return (*steps, draft), False

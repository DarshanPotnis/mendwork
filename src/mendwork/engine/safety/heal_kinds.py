"""How controls are used, and which changes of control kind a heal may accept.

Submitting, activating, toggling, typing, and choosing are different behaviours, whatever element
renders them. A heal never trades one for another, and a change of element within the same
behaviour (a button that became a link) needs a checkpoint that proves the effect.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from mendwork.engine.domain.enums import ActionType
from mendwork.engine.replay.identity import effective_type

_SUBMIT_TYPES: Final = frozenset({"submit", "image"})
_TEXT_INPUT_TYPES: Final = frozenset(
    {"text", "email", "search", "tel", "url", "password", "number"}
)
_DATE_INPUT_TYPES: Final = frozenset({"date", "time", "datetime-local", "month", "week"})
_TOGGLE_ROLES: Final = frozenset(
    {"checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio", "option"}
)
_ACTIVATE_ROLES: Final = frozenset({"button", "link", "menuitem", "tab"})
_TEXT_ROLES: Final = frozenset({"textbox", "searchbox", "spinbutton"})
_CHOICE_ROLES: Final = frozenset({"listbox", "combobox"})


class InteractionClass(StrEnum):
    """What activating or editing a control does, whatever element renders it."""

    SUBMIT = "submit"
    """Sends its form."""
    ACTIVATE = "activate"
    """Runs its behaviour or follows its link: buttons, links, menu items, tabs."""
    TOGGLE = "toggle"
    """Flips a state: checkboxes, radios, switches, options."""
    TEXT_ENTRY = "text_entry"
    DATE_LIKE = "date_like"
    CHOICE = "choice"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ElementKind:
    """The parts of an element that decide how it is activated."""

    tag: str
    role: str | None
    input_type: str | None
    has_href: bool
    text_entry: bool

    def describe(self) -> str:
        """The kind in a word, such as ``button`` or ``link``."""
        return self.role or self.tag


@dataclass(frozen=True, slots=True)
class KindComparison:
    """Whether a found element can stand in for the recorded one, and what changed."""

    compatible: bool
    change: str | None = None
    """For a compatible change of kind, such as ``button → link``."""
    needs_effect_checkpoint: bool = False
    reason: str | None = None
    """For an incompatible one, why."""


def interaction_class(kind: ElementKind) -> InteractionClass:
    """How a control is used, from its tag, effective type, role, and link."""
    tag = kind.tag.lower()
    kind_type = effective_type(tag, kind.input_type)
    if tag in {"button", "input"} and kind_type in _SUBMIT_TYPES:
        return InteractionClass.SUBMIT
    if tag == "select":
        return InteractionClass.CHOICE
    if tag == "input":
        return _input_class(kind_type)
    if tag == "textarea" or kind.text_entry or kind.role in _TEXT_ROLES:
        return InteractionClass.TEXT_ENTRY
    if kind.role in _TOGGLE_ROLES:
        return InteractionClass.TOGGLE
    if kind.role in _CHOICE_ROLES:
        return InteractionClass.CHOICE
    if tag == "button" or kind.role in _ACTIVATE_ROLES or (tag in {"a", "area"} and kind.has_href):
        return InteractionClass.ACTIVATE
    return InteractionClass.OTHER


def _input_class(kind_type: str | None) -> InteractionClass:
    if kind_type in _DATE_INPUT_TYPES:
        return InteractionClass.DATE_LIKE
    if kind_type in {"checkbox", "radio"}:
        return InteractionClass.TOGGLE
    if kind_type == "button":
        return InteractionClass.ACTIVATE
    if kind_type in _TEXT_INPUT_TYPES:
        return InteractionClass.TEXT_ENTRY
    return InteractionClass.OTHER


def action_accepts(action: ActionType, cls: InteractionClass) -> bool:
    """Whether an action can be performed on a control of this class at all."""
    match action:
        case ActionType.FILL:
            return cls in {InteractionClass.TEXT_ENTRY, InteractionClass.DATE_LIKE}
        case ActionType.SELECT:
            return cls is InteractionClass.CHOICE
        case ActionType.CLICK:
            return cls not in {
                InteractionClass.TEXT_ENTRY,
                InteractionClass.DATE_LIKE,
                InteractionClass.CHOICE,
            }
        case ActionType.PRESS:
            return True
        case ActionType.NAVIGATE:
            return False


def compare_kinds(recorded: ElementKind, found: ElementKind) -> KindComparison:
    """Whether a found control is used the same way as the recorded one.

    Submitting, activating, toggling, typing, and choosing are different behaviours, so a
    change of class is refused: a link cannot send a form, and a button cannot flip a
    checkbox. Within submitting or activating, a change of element (a button that became a
    link) is allowed, but only a checkpoint that observes the effect can prove the activation
    was equivalent, so the caller must require one. Dates keep their exact input type, and
    toggles and other controls their exact tag, role, and type.
    """
    recorded_class = interaction_class(recorded)
    found_class = interaction_class(found)
    if recorded_class is not found_class:
        return KindComparison(
            compatible=False,
            reason=(
                f"it is a {found.describe()} that would {_verb(found_class)}, where the "
                f"recorded {recorded.describe()} would {_verb(recorded_class)}"
            ),
        )
    # A fingerprint recorded without a role (password and date inputs) is compared by tag and
    # type, exactly as Rung 0's identity check compares it.
    same_role = recorded.role is None or recorded.role == found.role
    same_element = recorded.tag.lower() == found.tag.lower() and same_role
    same_type = effective_type(recorded.tag, recorded.input_type) == effective_type(
        found.tag, found.input_type
    )
    match recorded_class:
        case InteractionClass.SUBMIT | InteractionClass.ACTIVATE:
            if same_element:
                return KindComparison(compatible=True)
            return KindComparison(
                compatible=True,
                change=f"{recorded.describe()} → {found.describe()}",
                needs_effect_checkpoint=True,
            )
        case InteractionClass.TEXT_ENTRY | InteractionClass.CHOICE:
            return KindComparison(compatible=True)
        case InteractionClass.DATE_LIKE | InteractionClass.TOGGLE | InteractionClass.OTHER:
            if same_element and same_type:
                return KindComparison(compatible=True)
            return KindComparison(
                compatible=False,
                reason=(
                    f"it is a {_typed(found)} where a {_typed(recorded)} was recorded, which "
                    "behaves differently"
                ),
            )


def _verb(cls: InteractionClass) -> str:
    return {
        InteractionClass.SUBMIT: "submit a form",
        InteractionClass.ACTIVATE: "be activated",
        InteractionClass.TOGGLE: "flip a state",
        InteractionClass.TEXT_ENTRY: "take typed text",
        InteractionClass.DATE_LIKE: "take a date or time",
        InteractionClass.CHOICE: "offer a choice",
        InteractionClass.OTHER: "do something else",
    }[cls]


def _typed(kind: ElementKind) -> str:
    kind_type = effective_type(kind.tag, kind.input_type)
    suffix = f" ({kind_type})" if kind_type is not None else ""
    return f"{kind.describe()}{suffix}"

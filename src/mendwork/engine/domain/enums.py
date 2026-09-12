"""Closed vocabularies used across the workflow format."""

from enum import StrEnum


class ActionType(StrEnum):
    """What a step does. A download is a CLICK verified by a download_completed checkpoint."""

    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    PRESS = "press"


class RiskLevel(StrEnum):
    """What a step's action changes, which decides how carefully a heal must be treated.

    Classified by consequence, not by mechanism: submitting a filter form reads data, while
    clicking a plain button can delete it.
    """

    SAFE = "safe"
    """Reads or navigates only: links, filters, downloads, opening details."""
    CAUTION = "caution"
    """Changes session or unsaved form state, reversibly: fills, sign in, sign out."""
    IRREVERSIBLE = "irreversible"
    """Changes stored data or affects others: submitting orders, paying, deleting, sending."""


class InputKind(StrEnum):
    """The type of a declared run input, which decides how its values are validated."""

    TEXT = "text"
    DATE = "date"
    URL = "url"


class ValueKind(StrEnum):
    """Where a step's value comes from."""

    LITERAL = "literal"
    INPUT = "input"
    SECRET = "secret"  # noqa: S105 - the name of a value source, not a credential


class SelectorStrategy(StrEnum):
    """How a selector finds an element, in the order a recorder ranks them."""

    TEST_ID = "test_id"
    ROLE_NAME = "role_name"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    TEXT = "text"
    CSS = "css"


class UrlMatchMode(StrEnum):
    """How a URL pattern is compared with a URL; always explicit, never guessed."""

    EXACT = "exact"
    PREFIX = "prefix"
    REGEX = "regex"


class CheckpointKind(StrEnum):
    """What a checkpoint observes after a step's action."""

    URL_MATCHES = "url_matches"
    ELEMENT_VISIBLE = "element_visible"
    TEXT_PRESENT = "text_present"
    DOWNLOAD_COMPLETED = "download_completed"
    RESPONSE_RECEIVED = "response_received"
    NO_ERROR_BANNER = "no_error_banner"
    FIELD_HAS_VALUE = "field_has_value"


class ChangeKind(StrEnum):
    """Why a workflow version other than the first exists."""

    MANUAL_EDIT = "manual_edit"
    ROLLBACK = "rollback"


class AriaRole(StrEnum):
    """WAI-ARIA roles, exactly the set Playwright's role locator accepts.

    A test compares this list with Playwright's own type, so the two cannot drift apart.
    """

    ALERT = "alert"
    ALERTDIALOG = "alertdialog"
    APPLICATION = "application"
    ARTICLE = "article"
    BANNER = "banner"
    BLOCKQUOTE = "blockquote"
    BUTTON = "button"
    CAPTION = "caption"
    CELL = "cell"
    CHECKBOX = "checkbox"
    CODE = "code"
    COLUMNHEADER = "columnheader"
    COMBOBOX = "combobox"
    COMPLEMENTARY = "complementary"
    CONTENTINFO = "contentinfo"
    DEFINITION = "definition"
    DELETION = "deletion"
    DIALOG = "dialog"
    DIRECTORY = "directory"
    DOCUMENT = "document"
    EMPHASIS = "emphasis"
    FEED = "feed"
    FIGURE = "figure"
    FORM = "form"
    GENERIC = "generic"
    GRID = "grid"
    GRIDCELL = "gridcell"
    GROUP = "group"
    HEADING = "heading"
    IMG = "img"
    INSERTION = "insertion"
    LINK = "link"
    LIST = "list"
    LISTBOX = "listbox"
    LISTITEM = "listitem"
    LOG = "log"
    MAIN = "main"
    MARQUEE = "marquee"
    MATH = "math"
    MENU = "menu"
    MENUBAR = "menubar"
    MENUITEM = "menuitem"
    MENUITEMCHECKBOX = "menuitemcheckbox"
    MENUITEMRADIO = "menuitemradio"
    METER = "meter"
    NAVIGATION = "navigation"
    NONE = "none"
    NOTE = "note"
    OPTION = "option"
    PARAGRAPH = "paragraph"
    PRESENTATION = "presentation"
    PROGRESSBAR = "progressbar"
    RADIO = "radio"
    RADIOGROUP = "radiogroup"
    REGION = "region"
    ROW = "row"
    ROWGROUP = "rowgroup"
    ROWHEADER = "rowheader"
    SCROLLBAR = "scrollbar"
    SEARCH = "search"
    SEARCHBOX = "searchbox"
    SEPARATOR = "separator"
    SLIDER = "slider"
    SPINBUTTON = "spinbutton"
    STATUS = "status"
    STRONG = "strong"
    SUBSCRIPT = "subscript"
    SUPERSCRIPT = "superscript"
    SWITCH = "switch"
    TAB = "tab"
    TABLE = "table"
    TABLIST = "tablist"
    TABPANEL = "tabpanel"
    TERM = "term"
    TEXTBOX = "textbox"
    TIME = "time"
    TIMER = "timer"
    TOOLBAR = "toolbar"
    TOOLTIP = "tooltip"
    TREE = "tree"
    TREEGRID = "treegrid"
    TREEITEM = "treeitem"

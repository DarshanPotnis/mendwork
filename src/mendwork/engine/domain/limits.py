"""Size bounds that belong to the workflow format (schema version 1).

These are not runtime tuning. A workflow that is valid in one deployment must be valid in
every deployment, so the bounds live with the format rather than in Settings, and
changing one is a schema change. They exist so a file cannot bloat without limit and so
every consumer can rely on the same worst case.
"""

from typing import Final

IDENTIFIER_MAX_LENGTH: Final = 64
SHORT_TEXT_MAX_LENGTH: Final = 64
TEXT_MAX_LENGTH: Final = 256
LONG_TEXT_MAX_LENGTH: Final = 1024
LITERAL_VALUE_MAX_LENGTH: Final = 4096

NEARBY_TEXT_MAX_ITEMS: Final = 8
SELECTORS_MAX_ITEMS: Final = 10
SELECTOR_SCOPE_MAX_DEPTH: Final = 2
CHECKPOINTS_MAX_ITEMS: Final = 10
STEPS_MAX_ITEMS: Final = 500
DECLARATIONS_MAX_ITEMS: Final = 50

TIMEOUT_MS_MAX: Final = 600_000
HTTP_STATUS_MIN: Final = 100
HTTP_STATUS_MAX: Final = 599

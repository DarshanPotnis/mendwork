"""The RunIdGenerator port: identifiers for new runs."""

from typing import Protocol

from mendwork.engine.domain.runs import RunId


class RunIdGenerator(Protocol):
    """Creates unique, sortable, path-safe run ids."""

    def new_run_id(self) -> RunId:
        """A run id no earlier run has used."""
        ...

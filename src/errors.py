"""Error hierarchy for the pasta core.

These are raised by the pure core (`commands`, `fsm`, `serialize`) and by the
storage shell (`store`). The server translates them into FastMCP tool errors.
Each carries a human-readable message; `IllegalCommandError` also carries the
set of commands that *are* legal right now, so the caller can recover.
"""

from __future__ import annotations


class PastaError(Exception):
    """Base class for every expected (non-bug) failure in pasta."""


class NotFoundError(PastaError):
    """A workspace, page, or element id does not resolve."""


class ValidationError(PastaError):
    """Command arguments failed validation (missing/of the wrong type/not an enum member)."""


class IllegalCommandError(PastaError):
    """A command is not legal for this page right now.

    `legal` is the list of command names that are currently legal, so the caller
    can be told what it *can* do instead.
    """

    def __init__(self, message: str, legal: list[str] | None = None) -> None:
        super().__init__(message)
        self.legal: list[str] = legal or []


class ConflictError(PastaError):
    """A structural/consistency rule was violated (e.g. a reparent that would create a cycle, or a
    stale-read anchor in a reorder)."""


class ProductionTypeInTestError(PastaError):
    """A test touched a production page type while in test mode.

    Production page types are off-limits to the test suite (see `src.pagetypes._registry.set_test_mode`):
    they do not resolve, are not listed, and cannot be instantiated. Tests must exercise page-type
    capabilities on the hand-authored `test-*` fixtures (`src.testtypes`), never on production
    types.
    """

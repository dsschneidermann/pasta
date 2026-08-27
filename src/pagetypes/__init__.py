"""Page-type registry: page types expressed as data, not code.

A `PageType` fixes a page's sections, field kinds, legal commands, and status FSM.
`createPage` initializes from it, `commands.py` enforces it, and `describePageType`
reports it.

Each page type is declared in its own module beside this one, so it can be read and
changed on its own. What they share lives in `core`, grouped by what it is, and every
name is bound here - this module is the package's table of contents, the registry it
files the page types into, and the test seams.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from ..errors import ProductionTypeInTestError

from .core.specs import (
    ADD_BLOCK,
    ADD_ELEMENT,
    ADD_LINK,
    BLOCKS,
    BLOCK_ARRAY,
    COMPOUND,
    ELEMENT_TRANSITION,
    INLINE_RUNS,
    INLINE_RUN_GRID,
    INLINE_RUN_LISTS,
    LIST,
    PROSE,
    REMOVE_BLOCK,
    REMOVE_ELEMENT,
    REORDER_BLOCK,
    REORDER_ELEMENT,
    SCALAR,
    SET_ELEMENT_FIELD,
    SET_PROSE,
    SET_SCALAR,
    SET_TITLE,
    TABLE_ALIGN,
    TITLE_ELEMENT_FIELDS,
    TRANSITION,
    _ALIGN_VALUES,
    _MARKDOWN_TOKENS,
    AutoChildSpec,
    ChildStateGuard,
    ElementFSMSpec,
    FSMSpec,
    ParentStateGuard,
    RefCheck,
)
from .core.args import (
    ArgSpec,
    CommandSpec,
    _INDEX,
    _PRECEDING,
    BlockKindSpec,
    ElementBlocksSpec,
    _array,
    _boolean,
    _code_block,
    _divider_block,
    _heading_runs,
    _heading_text,
    _integer,
    _list_block,
    _object,
    _paragraph_runs,
    _paragraph_text,
    _quote_block,
    _reject_duplicate_blocks,
    _same_named,
    _table_block,
    _text,
    add_link_cmd,
    set_title_cmd,
    standard_blocks,
)
from .core.fields import (
    FieldSpec,
    SectionSpec,
    _blocks,
    _list,
    _prose,
    _scalar,
)
from .core.commands import (
    _a,
    _cap,
    _setter_label,
    _singular,
    blocks_cmds,
    element_blocks_cmds,
    element_cmds,
    is_field_setter,
    list_cmds,
    set_element_field_cmd,
    set_prose_cmd,
    set_scalar_cmd,
    transition_cmd,
    transition_on_add_cmd,
)
from .core.pagetype import (
    PageType,
    initial_sections,
    status_transitions,
)
from .core.validation import (
    _block_ref_ids,
    _reject_markdown,
    _validate_run,
    _validate_runs,
    collect_ref_ids,
    validate_block,
    validate_blocks,
    validate_inline_content,
    validate_pagetype_field_setters,
    validate_pagetype_setter_descriptions,
    validate_table,
)

# The package's exports, private helpers included: the page-type modules reach their
# helpers back through here, so a name absent from this list is absent from the
# package. Declared rather than implied so the re-exports above read as intentional.
__all__ = [
    "ADD_BLOCK",
    "ADD_ELEMENT",
    "ADD_LINK",
    "ArgSpec",
    "AutoChildSpec",
    "BLOCKS",
    "BLOCK_ARRAY",
    "BlockKindSpec",
    "COMPOUND",
    "ChildStateGuard",
    "CommandSpec",
    "ELEMENT_TRANSITION",
    "ElementBlocksSpec",
    "ElementFSMSpec",
    "FSMSpec",
    "FieldSpec",
    "INLINE_RUNS",
    "INLINE_RUN_GRID",
    "INLINE_RUN_LISTS",
    "LIST",
    "PROSE",
    "PageType",
    "ParentStateGuard",
    "REMOVE_BLOCK",
    "REMOVE_ELEMENT",
    "REORDER_BLOCK",
    "REORDER_ELEMENT",
    "RefCheck",
    "SCALAR",
    "SET_ELEMENT_FIELD",
    "SET_PROSE",
    "SET_SCALAR",
    "SET_TITLE",
    "SectionSpec",
    "TABLE_ALIGN",
    "TITLE_ELEMENT_FIELDS",
    "TRANSITION",
    "_ALIGN_VALUES",
    "_INDEX",
    "_MARKDOWN_TOKENS",
    "_PRECEDING",
    "_a",
    "_array",
    "_block_ref_ids",
    "_blocks",
    "_boolean",
    "_cap",
    "_code_block",
    "_divider_block",
    "_heading_runs",
    "_heading_text",
    "_integer",
    "_list",
    "_list_block",
    "_object",
    "_paragraph_runs",
    "_paragraph_text",
    "_prose",
    "_quote_block",
    "_reject_duplicate_blocks",
    "_reject_markdown",
    "_same_named",
    "_scalar",
    "_setter_label",
    "_singular",
    "_table_block",
    "_text",
    "_validate_run",
    "_validate_runs",
    "add_link_cmd",
    "blocks_cmds",
    "collect_ref_ids",
    "element_blocks_cmds",
    "element_cmds",
    "initial_sections",
    "is_field_setter",
    "list_cmds",
    "set_element_field_cmd",
    "set_prose_cmd",
    "set_scalar_cmd",
    "set_title_cmd",
    "standard_blocks",
    "status_transitions",
    "transition_cmd",
    "transition_on_add_cmd",
    "validate_block",
    "validate_blocks",
    "validate_inline_content",
    "validate_pagetype_field_setters",
    "validate_pagetype_setter_descriptions",
    "validate_table",
]

# --- The page types ----------------------------------------------------------
# These imports stay below every re-export above: each page-type module reaches
# its helpers back through this package, so the names have to be bound before these
# imports run. A re-export moved below this block raises ImportError on the first
# import of src.pagetypes.
# Imported last: a page-type module reads these declarations back out of the package as it
# builds, so anything it uses has to be declared above this point.
from .architecture import _ARCHITECTURE
from .decision_record import _DECISION_RECORD
from .bug_report import _BUG_REPORT
from .simple_change import _SIMPLE_CHANGE
from .feature import (
    _FEATURE_BRIEF,
    _FEATURE_SPEC,
    _IMPLEMENTATION_PLAN,
    _TESTING_PLAN,
)
from .epic import _AGENT_PLAN, _EPIC
from .document import _DOCUMENT
from .toc import _TOC


REGISTRY: dict[str, PageType] = {
    _ARCHITECTURE.tag: _ARCHITECTURE,
    _DECISION_RECORD.tag: _DECISION_RECORD,
    _BUG_REPORT.tag: _BUG_REPORT,
    _SIMPLE_CHANGE.tag: _SIMPLE_CHANGE,
    _FEATURE_BRIEF.tag: _FEATURE_BRIEF,
    _FEATURE_SPEC.tag: _FEATURE_SPEC,
    _IMPLEMENTATION_PLAN.tag: _IMPLEMENTATION_PLAN,
    _TESTING_PLAN.tag: _TESTING_PLAN,
    _EPIC.tag: _EPIC,
    _AGENT_PLAN.tag: _AGENT_PLAN,
    _DOCUMENT.tag: _DOCUMENT,
    _TOC.tag: _TOC,
}


# --- Test-only page types ----------------------------------------------------
# The `test-*` types (src.testtypes) are hand-authored, minimal capability fixtures - each
# demonstrates one part of the page-type system so tests exercise the full surface without pinning
# to (or cloning) any production type's shape. They are RESOLVABLE by `get_page_type` - so the
# store, renderer, and pure core all operate on a test page the same as any other - but HIDDEN from
# discovery (the `describePageType` listing and doc-gen enumeration) unless this test-only flag is
# set. Never set in production; flip it for the scope of a block with `expose_test_types()`.
_expose_test_types = False


def _test_registry() -> dict[str, PageType]:
    # Lazy import: src.testtypes imports the spec classes from THIS module, so importing it
    # at top level would be a cycle. Resolved here at call time, once both modules are loaded.
    from ..testtypes import TEST_REGISTRY

    return TEST_REGISTRY


@contextmanager
def expose_test_types() -> Generator[None]:
    """Test-only: reveal the hand-authored test-only types to `registered_tags` and
    `discoverable_registry` (hence the `describePageType` listing and doc-gen enumeration) for the
    duration of the block. Resolution via `get_page_type` is always on and is unaffected."""
    global _expose_test_types
    previous = _expose_test_types
    _expose_test_types = True
    try:
        yield
    finally:
        _expose_test_types = previous


# --- Test mode: production page types are off-limits to the test suite -------
# Separate from `_expose_test_types` (which gates only the DISCOVERY of the test-* fixtures): under
# test mode the PRODUCTION page types become inaccessible so a test can only ever exercise the
# hand-authored test-* fixtures. They stop RESOLVING (`get_page_type`), stop being LISTED
# (`registered_tags` / `discoverable_registry`, hence the describePageType listing + doc-gen), and a
# page of one cannot be CREATED - every such attempt raises `ProductionTypeInTestError`, steering the
# author to a test-* fixture. Flipped on for the whole suite by tests/conftest.py; never set in
# normal operation, where the guard is entirely inert.
_test_mode = False


def set_test_mode(on: bool = True) -> None:
    """Test-only: enter (or leave) test mode, in which production page types are off-limits - they do
    not resolve, are not listed, and cannot be instantiated (see `ProductionTypeInTestError`).
    tests/conftest.py flips this on (via a session-scoped autouse fixture, so it takes effect AFTER
    collection) for the whole run. Never called in normal operation."""
    global _test_mode
    _test_mode = on


def guard_production_type(tag: str) -> None:
    """Raise if `tag` names a production page type while in test mode - the shared guard behind both
    resolution (`get_page_type`) and creation (`commands.create_page`)."""
    if _test_mode and tag in REGISTRY:
        raise ProductionTypeInTestError(
            f"Production page type {tag!r} is off-limits in tests. Test new capabilities on a " +
            f"test-* page instead (always prefer an existing one; see src/testtypes.py)."
        )


def get_page_type(tag: str) -> PageType | None:
    """Resolve a page type by tag. The hand-authored test-only types resolve too (see
    `expose_test_types`), so the store and pure core operate on them; only their *discovery* is
    flag-gated. In test mode a PRODUCTION tag raises `ProductionTypeInTestError` instead of resolving
    (an unknown tag still returns None) - tests operate on the test-* fixtures, not production types."""
    test_type = _test_registry().get(tag)
    if test_type is not None:
        return test_type
    guard_production_type(tag)
    return REGISTRY.get(tag)


def registered_tags() -> list[str]:
    """The advertised page-type tags. The test-only types are excluded unless `_expose_test_types`
    is set - this is what keeps `describePageType`'s listing production-only in normal operation. In
    test mode the production types are hidden too (they are off-limits), so the listing shows only the
    test-* fixtures the `_expose_test_types` flag reveals."""
    tags = [] if _test_mode else list(REGISTRY.keys())
    if _expose_test_types:
        tags += list(_test_registry().keys())
    return tags


def discoverable_registry() -> dict[str, PageType]:
    """The registry that doc generation enumerates: production only, plus the hand-authored test-only
    types when `_expose_test_types` is set. Default (flag off) keeps generated docs production-only.
    In test mode the production types are hidden (off-limits), leaving only the test-* fixtures the
    `_expose_test_types` flag reveals."""
    registry: dict[str, PageType] = {} if _test_mode else dict(REGISTRY)
    if _expose_test_types:
        registry.update(_test_registry())
    return registry


def is_auto_child_type(parent_type: PageType | None, child_type: str) -> bool:
    """Whether `child_type` is an auto-created (pinned, protected) child of `parent_type`."""
    return parent_type is not None and any(spec.type == child_type for spec in parent_type.auto_children)

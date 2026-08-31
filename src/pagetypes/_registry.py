"""The page-type registry and its accessors.

Holds `REGISTRY` - the tag -> `PageType` map - the load-time validator, the resolution and
listing accessors, and the test seams. Building blocks come from the concrete
`pagetypes.core.*` submodules and each page type from its own module, so this module sits
below them in the dependency graph and nothing imports back up into the package init.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from ..errors import ProductionTypeInTestError

from .core.pagetype import PageType
from .core.specs import WorkspaceGuidanceSpec
from .core.validation import validate_page_types

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


def validate_registry() -> None:
    """Validate every registered page type once, raising one aggregated ValueError on any
    declaration error. The single entry point the primary flows (server start, HMR reload) call
    so a misconfigured type fails loudly at load rather than surfacing piecemeal later."""
    validate_page_types(REGISTRY)


# --- Test-only page types ----------------------------------------------------
# The `test-*` types (src.testtypes) are hand-authored, minimal capability fixtures - each
# demonstrates one part of the page-type system so tests exercise the full surface without pinning
# to (or cloning) any production type's shape. They are RESOLVABLE by `get_page_type` - so the
# store, renderer, and pure core all operate on a test page the same as any other - but HIDDEN from
# discovery (the `describePageType` listing and doc-gen enumeration) unless this test-only flag is
# set. Never set in production; flip it for the scope of a block with `expose_test_types()`.
_expose_test_types = False


def _test_registry() -> dict[str, PageType]:
    # Imported at call time, not at module top: testtypes builds on this package's core building
    # blocks, so a top-level import would have the pagetypes package and testtypes importing each
    # other. Resolved once, when both modules are loaded.
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


def workspace_guidance_fields() -> dict[str, WorkspaceGuidanceSpec]:
    """Every declared workspace-guidance field mapped to a representative spec (the first to declare
    it) - the fields a workspace may configure. Reads the production registry, or the test fixtures
    under test mode, so a fixture's field is never offered in production."""
    registry = _test_registry() if _test_mode else REGISTRY
    fields: dict[str, WorkspaceGuidanceSpec] = {}
    for page_type in registry.values():
        for spec in page_type.workspace_guidance:
            fields.setdefault(spec.field, spec)
    return fields

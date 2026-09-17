"""The page-type registry and its accessors.

Holds `REGISTRY` - the tag -> `PageType` map - the load-time validator, the resolution and
listing accessors, and the test seams. Building blocks come from the concrete
`pagetypes.core.*` submodules and each page type from its own module, so this module sits
below them in the dependency graph and nothing imports back up into the package init.
"""

from __future__ import annotations

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


# Read page types through `registered_pagetypes()` rather than from this map: the accessor is what
# applies test mode, handing back these types in production and the hand-authored fixtures under
# test, so resolution, the describePageType listing and doc generation all agree on which types
# exist. Under test mode this map is empty (see `set_test_mode`), so a direct reader finds nothing
# rather than a type the mode says is off-limits. Name REGISTRY directly only where the production
# types specifically are the point, as the validator below does.
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


def declaration_errors() -> str | None:
    """The same check as `validate_registry`, asked as a question: the aggregated error text, or
    None when every page type in play is well-formed.

    Two differences, and both are what a serving gate needs. It answers instead of raising, so a
    caller can refuse one request rather than fail a load. And it reads `registered_pagetypes()`
    rather than REGISTRY, so it reports on the types actually being served - which under hot reload
    is the half that reloaded, while the module that validated at load may be the stale one."""
    try:
        validate_page_types(registered_pagetypes())
    except ValueError as exc:
        return str(exc)
    return None


# --- Test-only page types ----------------------------------------------------
# The `test-*` types (src.testtypes) are hand-authored, minimal capability fixtures - each
# demonstrates one part of the page-type system so tests exercise the full surface without pinning
# to (or cloning) any production type's shape. Under test mode they stand in for the production
# registry, so the store, renderer, and pure core operate on a test page the same as any other;
# outside it they are unreachable.
def _test_registry() -> dict[str, PageType]:
    # Imported at call time, not at module top: testtypes builds on this package's core building
    # blocks, so a top-level import would have the pagetypes package and testtypes importing each
    # other. Resolved once, when both modules are loaded.
    from ..testtypes import TEST_REGISTRY

    return TEST_REGISTRY


# --- Test mode: the fixtures stand in for the production page types ----------
# `set_test_mode` below is the single explanation of what the mode does. While it is on the
# production types live here rather than in REGISTRY.
_test_mode = False
_stashed_registry: dict[str, PageType] = {}


def set_test_mode(on: bool = True) -> None:
    """Test-only: enter (or leave) test mode, in which the hand-authored test-* fixtures stand in for
    the production page types - production types do not resolve, are not listed, and cannot be
    instantiated (see `ProductionTypeInTestError`), steering the author to a test-* fixture.

    Entering EMPTIES `REGISTRY` into a private stash, and leaving puts it back: the production types
    are then unreachable through the map itself rather than only behind the accessors, so a caller
    holding it directly cannot depend on a page type the mode says is off-limits. The map is mutated
    in place, never rebound, so a reference taken before the switch stays the live one.
    tests/conftest.py flips this on for the whole run at import, ahead of collection, so a test module
    resolves its fixtures at module level. Never called in normal operation."""
    global _test_mode
    if on == _test_mode:
        return
    _test_mode = on
    source, target = (REGISTRY, _stashed_registry) if on else (_stashed_registry, REGISTRY)
    target.update(source)
    source.clear()


def guard_production_type(tag: str) -> None:
    """Raise if `tag` names a production page type while in test mode - the shared guard behind both
    resolution (`get_page_type`) and creation (`commands.create_page`). It reads the stash, which is
    where the production types are while the mode is on."""
    if _test_mode and tag in _stashed_registry:
        raise ProductionTypeInTestError(
            f"Production page type {tag!r} is off-limits in tests. Test new capabilities on a " +
            f"test-* page instead (always prefer an existing one; see src/testtypes.py)."
        )


def is_test_mode() -> bool:
    """Whether the hand-authored test-* fixtures are standing in for the production page types.

    The supported way to ask which registry is in play, for a caller that has to branch on it."""
    return _test_mode


def registered_pagetypes() -> dict[str, PageType]:
    """The page types that exist right now: the production registry, or the hand-authored test-*
    fixtures under test mode. The one map every consumer reads, so what resolves is exactly what is
    advertised."""
    return _test_registry() if _test_mode else REGISTRY


def get_page_type(tag: str) -> PageType | None:
    """Resolve a page type by tag, or None when no such type exists. In test mode a production tag
    raises `ProductionTypeInTestError` rather than returning None, so a test reaching for one is sent
    to a test-* fixture instead of left with a missing type."""
    guard_production_type(tag)
    return registered_pagetypes().get(tag)


def is_auto_child_type(parent_type: PageType | None, child_type: str) -> bool:
    """Whether `child_type` is an auto-created (pinned, protected) child of `parent_type`."""
    return parent_type is not None and any(spec.type == child_type for spec in parent_type.auto_children)


def workspace_guidance_fields() -> dict[str, WorkspaceGuidanceSpec]:
    """Every declared workspace-guidance field mapped to a representative spec (the first to declare
    it) - the fields a workspace may configure. Reads whichever registry is in play, so a fixture's
    field is never offered in production."""
    fields: dict[str, WorkspaceGuidanceSpec] = {}
    for page_type in registered_pagetypes().values():
        for spec in page_type.workspace_guidance:
            fields.setdefault(spec.field, spec)
    return fields

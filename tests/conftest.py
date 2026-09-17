"""Pytest configuration for the pasta suite: put the whole run in *test mode*.

Test mode (``src.pagetypes._registry.set_test_mode``) puts the hand-authored ``test-*`` fixtures
(``src.testtypes``) in place of the PRODUCTION page types, which become off-limits: they do not
resolve (``get_page_type``), are not listed (``registered_pagetypes`` - hence the
``describePageType`` listing and doc-gen enumeration), and a page of one cannot be created. Any
attempt raises ``ProductionTypeInTestError``, steering the author to exercise new capabilities on a
fixture instead - always preferring an existing one. Entering the mode empties ``REGISTRY`` itself,
so a test cannot depend on a production type by reading the map directly either.

The flag is set here at import, ahead of collection, because a test module resolves the fixture page
types it works on at module level. Nothing restores it: the flag lives for the process, and the
process is the run. A test that needs the production types asks for the ``production_mode`` fixture
below.
"""

import pytest

from src.pagetypes._registry import set_test_mode

set_test_mode(True)


@pytest.fixture
def production_mode():
    """Leave test mode for one test, so it sees the production registry a live server serves.
    Restored afterwards so the setting does not leak into the rest of the suite."""
    set_test_mode(False)
    yield
    set_test_mode(True)


@pytest.fixture
def invalid_declarations(monkeypatch):
    """Leave the page-type registry in the state a half-finished edit leaves it in: a field setter
    whose target field has been deleted, which is what `validate_pagetype_setter_descriptions`
    rejects. Patches `registered_pagetypes` rather than the gates themselves, so a gate under test
    runs the real validator over a genuinely invalid registry. Returns the error text it must
    surface."""
    from src.pagetypes import _registry
    from src.pagetypes.core.commands import set_scalar_cmd
    from src.pagetypes.core.fields import SectionSpec, _scalar
    from src.pagetypes.core.pagetype import PageType
    from src.pagetypes.core.specs import FSMSpec

    orphan = PageType(
        tag="xtest-orphan-setter", name="Orphan setter", description="ad-hoc",
        sections=(SectionSpec("report", "Report", (_scalar("component", description="x"),)),),
        commands=(set_scalar_cmd("report", "platform"),),
        fsm=FSMSpec(name="XOrphan", initial="open", states=("open",)),
    )
    monkeypatch.setattr(_registry, "registered_pagetypes", lambda: {orphan.tag: orphan})
    return "field setter 'setPlatform' targets unknown field 'report.platform'"

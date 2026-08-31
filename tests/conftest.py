"""Pytest configuration for the pasta suite: put the whole run in *test mode*.

Test mode (``src.pagetypes._registry.set_test_mode``) makes the PRODUCTION page types off-limits: they do
not resolve (``get_page_type``), are not listed (``registered_tags`` / ``discoverable_registry`` -
hence the ``describePageType`` listing and doc-gen enumeration), and a page of one cannot be created.
Any attempt raises ``ProductionTypeInTestError``, steering the author to exercise new capabilities on
the hand-authored ``test-*`` fixtures (``src.testtypes``) instead - always preferring an existing
one. This is separate from the ``_expose_test_types`` flag, which still gates only the *discovery* of
those fixtures.

The flag is flipped by a **session-scoped autouse** fixture rather than at import time
(``pytest_configure`` / module top-level) on purpose: it must take effect only AFTER collection.
``src.statecharts`` binds every production status-machine at import time via ``get_page_type``;
that module is imported (and cached) during collection while test mode is still off, so the import
succeeds and the guard then yields clean per-test failures instead of a collection error.
"""

import pytest

from src.pagetypes._registry import set_test_mode


@pytest.fixture(autouse=True, scope="session")
def _forbid_production_types_in_tests():
    set_test_mode(True)
    try:
        yield
    finally:
        set_test_mode(False)

"""Tests for the hand-authored test-only page types and the registry's one seam.

These guard the mechanism itself (src.testtypes + the `_test_mode` flag in
src.pagetypes._registry): `registered_pagetypes()` hands back the capability fixtures under test
mode and the production types otherwise, and it is the single map that resolution, the
`describePageType` listing and doc-gen enumeration all read - so a fixture is reachable in tests
and nowhere else. Their internal SHAPE is asserted by the tests that exercise each capability; their
structural well-formedness is checked in test_pagetypes (parametrized over the test registry
alongside production). This file owns only the seam and the membership of the set.
"""

import pytest

from src.errors import ProductionTypeInTestError, ValidationError
from src.pagetypes import _registry
from src.pagetypes._registry import get_page_type, is_test_mode, registered_pagetypes
from src.store import Store
from src.testtypes import TEST_REGISTRY

# The five capability fixtures - each demonstrates one part of the page-type system. This set is
# deliberately NOT derived from production: the fixtures are purpose-built, not clones.
TEST_TAGS = {"test-fields", "test-blocks", "test-element-blocks", "test-flow", "test-lifecycle",
             "test-child"}


# --- the fixture set ---------------------------------------------------------
def test_registry_is_the_five_capability_fixtures():
    assert set(TEST_REGISTRY) == TEST_TAGS


@pytest.mark.parametrize("tag", sorted(TEST_TAGS))
def test_each_fixture_is_tagged_and_has_a_valid_initial_state(tag):
    page_type = TEST_REGISTRY[tag]
    assert page_type.tag == tag
    assert page_type.fsm.initial in page_type.fsm.states


# --- one accessor, one map per mode ------------------------------------------
def test_test_mode_hands_back_the_fixtures():
    assert registered_pagetypes() == TEST_REGISTRY


def test_production_hands_back_the_production_types(production_mode):
    registry = registered_pagetypes()
    assert "feature-brief" in registry
    assert not any(tag.startswith("test-") for tag in registry)


def test_is_test_mode_is_on_for_the_suite():
    """conftest enters the mode at import, so every test but the one below sees it on."""
    assert is_test_mode() is True


def test_is_test_mode_is_off_for_a_live_server(production_mode):
    """The mode is left for one test - which is what a live server sees - and restored after, so
    the predicate has to follow the flag rather than a value captured at import."""
    assert is_test_mode() is False


# --- the mode empties the map, it does not merely gate it --------------------
def test_test_mode_empties_the_production_registry():
    """Reaching past the accessor into REGISTRY finds nothing while the mode is on, so a test cannot
    depend on a production page type by any route."""
    assert _registry.REGISTRY == {}


def test_leaving_test_mode_puts_the_production_registry_back(production_mode):
    assert set(_registry.REGISTRY) == set(registered_pagetypes())
    assert "feature-brief" in _registry.REGISTRY


def test_the_registry_is_restored_into_the_same_map(production_mode):
    # Emptied and refilled in place rather than rebound, so a reference taken before the switch is
    # still the live one - which is what lets a caller compare the two by identity.
    assert registered_pagetypes() is _registry.REGISTRY


# --- resolution reads that same map ------------------------------------------
def test_fixtures_resolve_in_test_mode():
    for tag in TEST_TAGS:
        assert get_page_type(tag) is TEST_REGISTRY[tag]
    assert get_page_type("test-nope") is None


def test_fixtures_do_not_resolve_in_production(production_mode):
    for tag in TEST_TAGS:
        assert get_page_type(tag) is None


def test_production_type_is_off_limits_in_test_mode():
    with pytest.raises(ProductionTypeInTestError):
        get_page_type("feature-brief")


# --- what resolves is what is listed -----------------------------------------
def test_fixture_page_cannot_be_created_in_production(tmp_path, production_mode):
    """Resolution and the listing are one map, so naming a test-* tag on a live server gets the
    unknown-type error rather than a page of a type nothing ever advertised or validated."""
    store = Store(tmp_path)
    workspace = store.create_workspace("demo")
    with pytest.raises(ValidationError):
        store.create_page(workspace.id, "test-fields", "A fixture page")


def test_fixture_page_is_creatable_in_test_mode(tmp_path):
    store = Store(tmp_path)
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "A fixture page").page
    assert page.type == "test-fields"
    assert "test-fields" in registered_pagetypes()

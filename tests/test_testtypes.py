"""Tests for the hand-authored test-only page types and the resolution-vs-discovery seam.

These guard the mechanism itself (src.testtypes + the `_expose_test_types` flag in
src.pagetypes): the five capability fixtures are RESOLVABLE everywhere but HIDDEN from
discovery (the `describePageType` listing and doc-gen enumeration) unless the test-only flag is
set. Their internal SHAPE is asserted by the tests that exercise each capability; their structural
well-formedness is checked in test_pagetypes (parametrized over the test registry alongside
production). This file owns only the seam and the membership of the set.
"""

import pytest

from src.pagetypes import (
    discoverable_registry,
    expose_test_types,
    get_page_type,
    registered_tags,
)
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


# --- resolution is always on -------------------------------------------------
def test_fixtures_resolve_regardless_of_flag():
    for tag in TEST_TAGS:
        assert get_page_type(tag) is TEST_REGISTRY[tag]          # resolves with the flag off
    with expose_test_types():
        for tag in TEST_TAGS:
            assert get_page_type(tag) is TEST_REGISTRY[tag]      # and with it on
    assert get_page_type("test-nope") is None


# --- discovery is hidden by default ------------------------------------------
def test_listing_hides_fixtures_by_default():
    assert not any(tag.startswith("test-") for tag in registered_tags())
    assert not any(tag.startswith("test-") for tag in discoverable_registry())


# --- the flag reveals discovery, and always restores -------------------------
def test_expose_flag_reveals_then_restores():
    assert not any(tag.startswith("test-") for tag in registered_tags())
    with expose_test_types():
        assert TEST_TAGS <= set(registered_tags())
        assert TEST_TAGS <= set(discoverable_registry())
    assert not any(tag.startswith("test-") for tag in registered_tags())      # restored


def test_expose_flag_restores_even_on_exception():
    with pytest.raises(RuntimeError):
        with expose_test_types():
            assert any(tag.startswith("test-") for tag in registered_tags())
            raise RuntimeError("boom")
    assert not any(tag.startswith("test-") for tag in registered_tags())


# --- the accepted 'resolvable but unlisted' boundary -------------------------
def test_fixture_is_creatable_by_explicit_tag_yet_never_advertised(tmp_path):
    """A caller who names a test-* tag CAN create a fixture page (resolution is always on), but the
    describePageType listing never advertises it - the accepted cost of 'resolvable but unlisted'."""
    store = Store(tmp_path)
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "A fixture page").page
    assert page.type == "test-fields"                            # created via the store
    assert "test-fields" not in registered_tags()               # yet unlisted (flag off)

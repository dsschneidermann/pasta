"""Unit tests for the pure doc generator (src.docsgen)."""

import importlib

import pytest

from src import statecharts
from src.pagetypes._registry import set_test_mode
from src.commands import create_page, legal_commands
from src.docsgen import (
    _bullet,
    _field_line,
    _seed_page,
    all_state_docs,
    reachable_states,
    render_states_index,
    state_docs,
)
from src.pagetypes.core.pagetype import get_pagetype_command, get_pagetype_field
from src.pagetypes.core.specs import status_guidance
from src.pagetypes._registry import REGISTRY, get_page_type
from src.statecharts import page_machine_qualname

# Migration note: the PURE FSM-analysis tests below (reachable_states, ignore_requirements) pin to
# the hand-authored test fixtures (src.testtypes), so enriching a production FSM never churns
# them. The `state_docs`-pipeline tests further down stay on the production REGISTRY on purpose:
# state_docs resolves each type's diagram class via `page_machine_qualname`, which is bound (in
# src.statecharts) for production types ONLY. Documenting a fixture is a deliberate non-goal, so
# those tests - and the registry-wide coverage/qualname tests - must track production.


@pytest.fixture(autouse=True)
def _production_mode():
    # Doc generation runs against the production registry, so this whole module runs with test mode
    # off. Restored afterwards so the setting does not leak into later test modules.
    set_test_mode(False)
    yield
    set_test_mode(True)


def _counter():
    state = {"n": 0}

    def factory(prefix: str) -> str:
        state["n"] += 1
        return f"{prefix}:{state['n']}" if prefix else f"el{state['n']}"

    return factory


# --- reachable_states --------------------------------------------------------
def test_reachable_states_lifecycle_shortest_paths():
    paths = reachable_states(get_page_type("test-lifecycle").fsm)
    assert paths["draft"] == []                       # initial
    assert paths["planning"] == ["beginPlanning"]
    assert paths["building"] == ["beginPlanning", "beginImplementation"]
    assert paths["review"] == ["beginPlanning", "beginImplementation", "submitForReview"]
    assert paths["abandoned"] == ["abandon"]          # BFS finds the direct draft->abandoned edge


def test_reachable_states_single_state_blocks():
    assert reachable_states(get_page_type("test-blocks").fsm) == {"active": []}


def test_every_declared_state_is_reachable():
    # Doc coverage depends on this: an unreachable state gets no page and no :events: path.
    for tag, page_type in REGISTRY.items():
        reachable = reachable_states(page_type.fsm)
        assert set(reachable) == set(page_type.fsm.states), f"{tag} has an unreachable state"


# --- ignore_requirements (the "skip validation" support) ---------------------
def test_ignore_requirements_surfaces_content_gated_transition():
    lifecycle = get_page_type("test-lifecycle")
    page = create_page(lifecycle, "F", None, _counter())   # empty draft
    assert legal_commands(page, lifecycle)["beginPlanning"] is False          # requires summary.body
    assert legal_commands(page, lifecycle, ignore_requirements=True)["beginPlanning"] is True


def test_ignore_requirements_does_not_bypass_legal_in_lock():
    child = get_page_type("test-child")
    ready_child = _seed_page(child, "ready")           # ready locks step edits (legal_in=draft)
    assert legal_commands(ready_child, child, ignore_requirements=True)["addStep"] is False


# --- page_machine_qualname ---------------------------------------------------
def test_page_machine_qualname_resolves_for_every_registered_type():
    for tag, page_type in REGISTRY.items():
        qualname = page_machine_qualname(tag)
        module_path, _, name = qualname.rpartition(".")
        resolved = getattr(importlib.import_module(module_path), name)
        # The resolved class must be the page's own status machine.
        assert resolved is statecharts.machine_class(page_type.fsm)


def test_page_machine_qualname_unknown_tag_raises():
    with pytest.raises(KeyError):
        page_machine_qualname("no-such-type")


# --- registry-wide coverage + index ------------------------------------------
def test_all_state_docs_covers_every_reachable_state():
    expected = {
        f"{tag}-{state}"
        for tag, page_type in REGISTRY.items()
        for state in reachable_states(page_type.fsm)
    }
    assert set(all_state_docs()) == expected


def test_states_index_lists_every_generated_page():
    index = render_states_index()
    assert index.startswith("# Page-type states")
    assert "```{toctree}" in index
    for stem in all_state_docs():
        assert f"{stem}.md" in index


def test_no_dead_overview_link_in_state_pages():
    # The per-type overview pages were removed, so nothing should link to a "type overview".
    for markdown in all_state_docs().values():
        assert "type overview" not in markdown


def test_all_state_docs_is_idempotent():
    # Doc generation is pure over the registry: regenerating yields byte-identical output.
    assert all_state_docs() == all_state_docs()


def test_generated_docs_carry_the_instruction_on_the_field_not_the_setter():
    # The instruction reaches the docs through the field line (rendered as an indented block, so it
    # arrives line by line); the setter's own line is the short description.
    brief = get_page_type("feature-brief")
    instruction = get_pagetype_field(brief, "summary", "body").description
    assert instruction and "\n" in instruction                # a wrapped multi-line authoring instruction
    docs = "\n".join(all_state_docs().values())
    for line in instruction.splitlines():
        assert line in docs                                   # still printed, from the Sections listing
    assert get_pagetype_command(brief, "setSummary").description == "set the summary"
    assert "- `setSummary(statusRevisionToken, text)` *(set_prose)* - set the summary" in docs


def test_generated_docs_do_not_repeat_the_instruction_under_every_setter():
    # The instruction's first line appears at most once per document (the Sections listing), where it
    # used to appear again under Commands and under Authoring commands.
    brief = get_page_type("feature-brief")
    first_line = get_pagetype_field(brief, "summary", "body").description.splitlines()[0]
    for doc in all_state_docs().values():
        if brief.tag in doc:
            assert doc.count(first_line) <= 1


# --- per-state guidance on the generated page --------------------------------
def test_state_page_opens_with_its_status_guidance():
    # The text an agent gets on entering a status is the text a human reads on its page.
    guidance = status_guidance(get_page_type("feature-brief").fsm, "review")
    assert guidance                                        # one of the documented states
    docs = state_docs(get_page_type("feature-brief"))
    for line in guidance.splitlines():
        assert line in docs["feature-brief-review"]
    assert "The `review` state of the `feature-brief` page type." not in docs["feature-brief-review"]


def test_state_page_without_guidance_keeps_the_placeholder():
    # Pins the narrow scope: a sibling state, and a type with no guidance at all.
    brief = state_docs(get_page_type("feature-brief"))
    assert "The `draft` state of the `feature-brief` page type." in brief["feature-brief-draft"]
    document = state_docs(get_page_type("document"))
    assert "The `active` state of the `document` page type." in document["document-active"]


def test_bullet_keeps_a_single_line_description_inline():
    assert _bullet("- `removeStep`", "remove a step", []) == "- `removeStep` - remove a step"
    assert _bullet("- `removeStep`", "remove a step", ["legal in `draft`"]) == (
        "- `removeStep` - remove a step · legal in `draft`")


def test_bullet_indents_a_multiline_instruction_past_its_marker():
    # Indented to the item's content column: its marker's indent + 2.
    top = _bullet("- `addStep`", "first line\nsecond line", [])
    assert top == "- `addStep`\n  first line\n  second line"
    nested = _bullet("  - `items` *(list)*", "first line\nsecond line", [])
    assert nested == "  - `items` *(list)*\n    first line\n    second line"


def test_bullet_keeps_notes_on_the_header_line():
    rendered = _bullet("  - `kind` *(scalar)*", "narrowest kind that fits\nstay consistent", ["one of `a`, `b`"])
    assert rendered.splitlines()[0] == "  - `kind` *(scalar)* · one of `a`, `b`"
    assert rendered.splitlines()[1:] == ["    narrowest kind that fits", "    stay consistent"]


def test_field_line_renders_a_descriptionless_field_as_a_bare_bullet():
    # No description means no continuation block and no trailing separator.
    field = {"key": "commit", "kind": "scalar", "description": "",
             "choices": None, "elementFields": None, "elementStates": None,
             "elementBlocks": None}
    assert _field_line(field) == "  - `commit` *(scalar)*"


def test_field_line_notes_block_element_fields():
    field = {"key": "items", "kind": "list", "description": "",
             "choices": None, "elementFields": ["text", "detail"], "elementStates": None,
             "elementBlocks": [{"field": "detail", "kinds": ["paragraph", "code"]}]}
    assert _field_line(field) == (
        "  - `items` *(list)* · element fields: `text`, `detail` · "
        "block element fields: `detail` (paragraph, code)"
    )


def test_field_line_notes_block_kinds():
    """A blocks field teaches its vocabulary in the generated docs, because a reader can no
    longer infer it from a list of per-kind command names."""
    field = {"key": "body", "kind": "blocks", "description": "",
             "choices": None, "elementFields": None, "elementStates": None,
             "elementBlocks": None,
             "blockKinds": ["paragraph", "heading", "code"]}
    assert _field_line(field) == (
        "  - `body` *(blocks)* · block kinds: `paragraph`, `heading`, `code`"
    )


def test_field_line_omits_block_kinds_for_a_non_blocks_field():
    field = {"key": "items", "kind": "list", "description": "",
             "choices": None, "elementFields": ["text"], "elementStates": None,
             "elementBlocks": None, "blockKinds": None}
    assert _field_line(field) == "  - `items` *(list)* · element fields: `text`"


def test_generated_docs_name_no_deleted_block_command():
    """The per-kind commands and every block set are gone; a generated page still naming one
    would send an authoring agent at a command that does not exist."""
    deleted = ("addParagraph", "addHeading", "addDivider", "addQuote", "addTable",
               "addDetailParagraph", "addDetailCode", "addNoteCode", "addDesignCode",
               "addDecisionCode", "addDecisionBlock", "setParagraph", "setHeading",
               "setDetailParagraph", "setDetailCode",
               "setBodyBlock", "setDetailsBlock", "setDecisionBlock", "setConsequencesBlock",
               "setDesignBlock", "setDecisionsBlock", "setDataModelsBlock", "setStepDetailBlock")
    pages = all_state_docs()
    assert pages
    for name, text in pages.items():
        for command in deleted:
            assert command not in text, f"{name} still names {command}"

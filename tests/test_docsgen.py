"""Unit tests for the pure doc generator (src.docsgen).

Every example runs on the hand-authored fixtures (src.testtypes), which are what
`registered_pagetypes()` hands back under test mode - so doc generation is exercised over the same
registry the rest of the suite uses, and each capability is pinned on the fixture built for it:
test-lifecycle for a rich FSM and a wrapped field instruction, test-flow for status guidance,
test-blocks for the single-state case, test-child for a legal_in content lock.
"""

import importlib
from types import ModuleType

import pytest

from src import docsgen, testcharts
from src.commands import create_page, legal_commands
from src.docsgen import (
    _bullet,
    _field_line,
    _seed_page,
    all_state_docs,
    page_machine_qualname,
    reachable_states,
    render_states_index,
    state_docs,
)
from src.fsm import machine_class
from src.pagetypes.core.pagetype import get_pagetype_command, get_pagetype_field
from src.pagetypes.core.specs import status_guidance
from src.pagetypes._registry import get_page_type, registered_pagetypes


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
    for tag, page_type in registered_pagetypes().items():
        reachable = reachable_states(page_type.fsm)
        assert set(reachable) == set(page_type.fsm.states), f"{tag} has an unreachable state"


# --- ignore_requirements (the "skip validation" support) ---------------------
def test_ignore_requirements_surfaces_content_gated_transition():
    lifecycle = get_page_type("test-lifecycle")
    page = create_page(lifecycle, "F", None, _counter())   # empty draft
    assert legal_commands(page, lifecycle)["beginPlanning"] is False          # requires summary.body
    assert legal_commands(page, lifecycle, ignore_requirements=True)["beginPlanning"] is True


def test_ignore_requirements_keeps_the_legal_in_lock():
    child = get_page_type("test-child")
    ready_child = _seed_page(child, "ready")           # ready locks step edits (legal_in=draft)
    assert legal_commands(ready_child, child, ignore_requirements=True)["addStep"] is False


# --- page_machine_qualname ---------------------------------------------------
def test_page_machine_qualname_resolves_for_every_registered_type():
    for tag, page_type in registered_pagetypes().items():
        qualname = page_machine_qualname(tag)
        module_path, _, name = qualname.rpartition(".")
        resolved = getattr(importlib.import_module(module_path), name)
        # The resolved class must be the page's own status machine.
        assert resolved is machine_class(page_type.fsm)


def test_page_machine_qualname_names_the_fixture_bindings():
    # Under test mode the fixtures are the registry, so their diagram classes are the ones bound
    # for them rather than the production module's.
    assert page_machine_qualname("test-flow") == f"{testcharts.__name__}.TestFlowMachine"


def test_page_machine_qualname_unknown_tag_raises():
    with pytest.raises(KeyError):
        page_machine_qualname("no-such-type")


def test_page_machine_qualname_names_the_production_bindings(production_mode):
    # Outside test mode the production types are the ones being documented, so their bindings are
    # what the renderer resolves against.
    from src import statecharts

    assert page_machine_qualname("architecture") == f"{statecharts.__name__}.ArchitectureMachine"


def test_page_machine_qualname_resolves_for_every_production_type(production_mode):
    """The same guarantee as the fixture loop above, over the types the documentation site actually
    publishes. The bindings are written out one per type rather than derived from the registry, so
    this is what fails when a page type is added and its binding is not."""
    for tag, page_type in registered_pagetypes().items():
        qualname = page_machine_qualname(tag)
        module_path, _, name = qualname.rpartition(".")
        resolved = getattr(importlib.import_module(module_path), name)
        assert resolved is machine_class(page_type.fsm), tag


def test_page_machine_qualname_missing_binding_raises(monkeypatch):
    # A registered type whose machine was never bound fails rather than naming a path that will
    # not import. No real registry reaches this, so an empty module stands in for the bindings.
    monkeypatch.setattr(docsgen, "_bindings_module", lambda: ModuleType("unbound-fixture"))
    with pytest.raises(KeyError, match="No page-status machine is bound"):
        page_machine_qualname("test-flow")


# --- registry-wide coverage + index ------------------------------------------
def test_all_state_docs_covers_every_reachable_state():
    expected = {
        f"{tag}-{state}"
        for tag, page_type in registered_pagetypes().items()
        for state in reachable_states(page_type.fsm)
    }
    assert set(all_state_docs()) == expected


def test_states_index_lists_every_generated_page():
    index = render_states_index()
    assert index.startswith("# Page-type states")
    assert "```{toctree}" in index
    for stem in all_state_docs():
        assert f"{stem}.md" in index


def test_all_state_docs_is_idempotent():
    # Doc generation is pure over the registry: regenerating yields byte-identical output.
    assert all_state_docs() == all_state_docs()


# --- a state page's own content ----------------------------------------------
def test_state_page_lists_its_transitions_with_destinations_and_agency():
    docs = state_docs(get_page_type("test-flow"))
    assert "- **close** → [closed](test-flow-closed.md) *(agent)*" in docs["test-flow-open"]


def test_terminal_state_page_says_it_has_no_transitions_out():
    docs = state_docs(get_page_type("test-blocks"))
    assert "`active` is a terminal state - it has no outgoing transitions." in docs["test-blocks-active"]


def test_state_page_carries_the_diagram_and_the_path_that_reaches_it():
    docs = state_docs(get_page_type("test-flow"))
    diagram = f"```{{statemachine-diagram}} {testcharts.__name__}.TestFlowMachine"
    assert diagram in docs["test-flow-closed"]
    assert ":events: open, close" in docs["test-flow-closed"]


def test_state_page_footers_every_state_and_marks_the_current_one():
    docs = state_docs(get_page_type("test-flow"))
    assert ("**All states:** [draft](test-flow-draft.md) · **open** · [closed](test-flow-closed.md)"
            in docs["test-flow-open"])


# --- a field's authoring instruction on the generated page -------------------
def test_generated_docs_carry_the_instruction_on_the_field_not_the_setter():
    # The instruction reaches the docs through the field line (rendered as an indented block, so it
    # arrives line by line); the setter's own line is the short description.
    lifecycle = get_page_type("test-lifecycle")
    instruction = get_pagetype_field(lifecycle, "summary", "body").description
    assert instruction and "\n" in instruction             # a wrapped multi-line authoring instruction
    docs = "\n".join(state_docs(lifecycle).values())
    for line in instruction.splitlines():
        assert line in docs                                # printed, from the Sections listing
    assert get_pagetype_command(lifecycle, "setSummary").description == "set the summary"
    assert "- `setSummary(statusRevisionToken, text)` *(set_prose)* - set the summary" in docs


def test_generated_docs_print_the_instruction_once_per_state_page():
    # One printing per page, in the Sections listing.
    lifecycle = get_page_type("test-lifecycle")
    first_line = get_pagetype_field(lifecycle, "summary", "body").description.splitlines()[0]
    for doc in state_docs(lifecycle).values():
        assert doc.count(first_line) == 1


# --- per-state guidance on the generated page --------------------------------
def test_state_page_opens_with_its_status_guidance():
    # The text an agent gets on entering a status is the text a human reads on its page.
    flow = get_page_type("test-flow")
    guidance = status_guidance(flow.fsm, "open")
    assert guidance                                        # one of the documented states
    docs = state_docs(flow)
    for line in guidance.splitlines():
        assert line in docs["test-flow-open"]


def test_state_page_without_guidance_keeps_the_placeholder():
    # Pins the narrow scope: a sibling state, and a type with no guidance at all.
    flow = state_docs(get_page_type("test-flow"))
    assert "The `draft` state of the `test-flow` page type." in flow["test-flow-draft"]
    fields = state_docs(get_page_type("test-fields"))
    assert "The `active` state of the `test-fields` page type." in fields["test-fields-active"]


# --- the markdown helpers ----------------------------------------------------
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


def test_field_line_renders_a_list_field_from_its_element_fields():
    field = {"key": "items", "kind": "list", "description": "",
             "choices": None, "elementFields": ["text"], "elementStates": None,
             "elementBlocks": None, "blockKinds": None}
    assert _field_line(field) == "  - `items` *(list)* · element fields: `text`"

"""Unit tests for registry integrity - the specs must be internally consistent.

The generic, parametrized structural invariants run over BOTH the production REGISTRY and the
hand-authored test registry (TEST_REGISTRY) - every page type, production or fixture, must be
well-formed. `test_expected_types_registered` stays pinned to the production SET so a genuinely
new or removed production type still fails loudly. The content-specific assertions further down
pin to the test fixtures (src.testtypes), so enriching a production type never breaks them.
"""

from textwrap import dedent

import pytest

from src.commands import is_field_setter
from src.errors import ValidationError
from src.pagetypes import (
    ADD_BLOCK,
    ADD_ELEMENT,
    ADD_LINK,
    COMPOUND,
    ELEMENT_TRANSITION,
    INLINE_RUNS,
    INLINE_RUN_GRID,
    INLINE_RUN_LISTS,
    REORDER_BLOCK,
    REORDER_ELEMENT,
    REMOVE_BLOCK,
    REMOVE_ELEMENT,
    SET_ELEMENT_FIELD,
    SET_PROSE,
    SET_SCALAR,
    SET_TITLE,
    TABLE_ALIGN,
    TRANSITION,
    BLOCKS,
    BLOCK_ARGS,
    BLOCK_ARRAY,
    LIST,
    PROSE,
    REGISTRY,
    BlockKindSpec,
    STANDARD_BLOCK_KINDS,
    ElementBlocksSpec,
    set_prose_cmd,
    SectionSpec,
    PageType,
    CommandSpec,
    FieldSpec,
    _array,
    _blocks,
    _list,
    _prose,
    _text,
    blocks_cmds,
    collect_ref_ids,
    element_blocks_cmds,
    list_cmds,
    get_page_type,
    initial_sections,
    status_transitions,
    validate_block,
    validate_blocks,
    validate_inline_content,
    validate_pagetype_field_setters,
    validate_table,
    FSMSpec,
)
from src.pagetypes import _stage_guidance
from src.testtypes import TEST_REGISTRY

def _kinds(*names: str) -> tuple[BlockKindSpec, ...]:
    """A field vocabulary from bare kind names - the same normalization a declaration does."""
    return tuple(BlockKindSpec(name) for name in names)


# Structural invariants must hold for EVERY page type - production and hand-authored fixture alike.
ALL_TYPES = {**REGISTRY, **TEST_REGISTRY}

# Commands that target a real section.field
CONTENT_TARGETING = {
    SET_SCALAR, SET_PROSE, ADD_ELEMENT, SET_ELEMENT_FIELD, ELEMENT_TRANSITION,
    REORDER_ELEMENT, REORDER_BLOCK, REMOVE_ELEMENT, ADD_BLOCK, REMOVE_BLOCK,
}
# Commands that edit a `blocks` field (must target a BLOCKS field). reorder_block belongs here;
# reorder_element (its list-field twin) does not - it targets a LIST field.
BLOCK_TARGETING = {ADD_BLOCK, REMOVE_BLOCK, REORDER_BLOCK}
# List-element commands whose element_map fields must be declared on the (LIST) field
ELEMENT_MAPPING = {ADD_ELEMENT, SET_ELEMENT_FIELD, ELEMENT_TRANSITION}


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_fsm_is_well_formed(tag: str):
    page_type = ALL_TYPES[tag]
    fsm = page_type.fsm
    assert fsm.initial in fsm.states
    # The status transition table is DERIVED from the type's transition/compound commands.
    for _event, source, dest, agency in status_transitions(page_type):
        assert source in fsm.states, f"{tag}: transition source {source} not a state"
        assert dest in fsm.states, f"{tag}: transition dest {dest} not a state"
        assert agency in {"agent", "human", "either"}


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_transition_commands_declare_source_and_dest(tag: str):
    """The single-home rule: every transition/compound command declares its source state(s) via
    legal_in and a real destination via dest, and no command maps one event to two different dests."""
    page_type = ALL_TYPES[tag]
    states = set(page_type.fsm.states)
    event_dests: dict[str, str] = {}
    for command in page_type.commands:
        if command.kind in (TRANSITION, COMPOUND) and command.event is not None:
            assert command.legal_in, f"{tag}.{command.name} has no legal_in source state(s)"
            assert command.dest in states, f"{tag}.{command.name} dest {command.dest} not a state"
            for source in command.legal_in:
                assert source in states, f"{tag}.{command.name} source {source} not a state"
            prior = event_dests.setdefault(command.event, command.dest)
            assert prior == command.dest, \
                f"{tag}: event {command.event} maps to two dests ({prior}, {command.dest})"


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_content_commands_target_real_fields(tag: str):
    page_type = ALL_TYPES[tag]
    for command in page_type.commands:
        if command.kind in CONTENT_TARGETING:
            field_spec = page_type.field_spec(command.section, command.field)
            assert field_spec is not None, f"{tag}.{command.name} targets missing {command.section}.{command.field}"
            if command.kind in ELEMENT_MAPPING:
                # every mapped element field must be declared on the list field
                assert field_spec.kind == LIST
                for element_field, _arg in command.element_map:
                    assert element_field in field_spec.element_fields


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_transition_commands_reference_real_events(tag: str):
    page_type = ALL_TYPES[tag]
    events = {event for event, *_ in page_type.fsm.transitions}
    for command in page_type.commands:
        if command.kind in (TRANSITION, COMPOUND) and command.event is not None:
            assert command.event in events, f"{tag}.{command.name} fires unknown event {command.event}"


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_initial_sections_cover_every_field(tag: str):
    page_type = ALL_TYPES[tag]
    sections = initial_sections(page_type)
    for section in page_type.sections:
        assert section.key in sections
        for field_spec in section.fields:
            assert field_spec.key in sections[section.key]


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_requires_reference_real_fields(tag: str):
    """Every required-content precondition must point at a field the page type actually has."""
    page_type = ALL_TYPES[tag]
    for command in page_type.commands:
        for section_key, field_key in command.requires:
            assert page_type.field_spec(section_key, field_key) is not None, (
                f"{tag}.{command.name} requires missing field {section_key}.{field_key}"
            )


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_element_transitions_reference_real_element_events(tag: str):
    """An element-transition command must fire an event on a field that has an element FSM."""
    page_type = ALL_TYPES[tag]
    for command in page_type.commands:
        if command.kind == ELEMENT_TRANSITION:
            field_spec = page_type.field_spec(command.section, command.field)
            assert field_spec is not None and field_spec.element_fsm is not None, (
                f"{tag}.{command.name} drives an element FSM on a field that has none"
            )
            events = {event for event, *_ in field_spec.element_fsm.transitions}
            assert command.event in events, f"{tag}.{command.name} fires unknown element event {command.event}"


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_block_commands_target_blocks_fields(tag: str):
    """add/set/move/remove-block commands must target a `blocks` field - or, when element-scoped, a
    `list` field declaring that element field as block-bearing, for a kind that field accepts."""
    page_type = ALL_TYPES[tag]
    for command in page_type.commands:
        if command.kind not in BLOCK_TARGETING:
            continue
        field_spec = page_type.field_spec(command.section, command.field)
        if command.element_field is None:
            assert field_spec is not None and field_spec.kind == BLOCKS, (
                f"{tag}.{command.name} targets {command.section}.{command.field}, which is not a blocks field"
            )
            continue
        assert field_spec is not None and field_spec.kind == LIST, (
            f"{tag}.{command.name} is element-scoped but {command.section}.{command.field} is not a list field"
        )
        blocks_spec = field_spec.element_blocks_spec(command.element_field)
        assert blocks_spec is not None, (
            f"{tag}.{command.name} targets element field {command.element_field}, "
            f"which {command.section}.{command.field} does not declare as block-bearing"
        )
        # The kind is data in the argument now, so what a command may write is the vocabulary
        # its block argument carries - which must be exactly the element field's declaration.
        accepted = [kind.kind for kind in blocks_spec.vocabulary()]
        for arg in command.args:
            if arg.block_kinds is None:
                continue
            assert [kind.kind for kind in arg.block_kinds] == accepted, (
                f"{tag}.{command.name} accepts {[k.kind for k in arg.block_kinds]}, but "
                f"{command.element_field} declares {accepted}"
            )


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_every_ordered_field_has_a_reorder_command(tag: str):
    """The 'extend to all fields' contract: every list field exposes a reorder_element command,
    every blocks field a reorder_block command, and every block-bearing element field its own
    element-scoped reorder_block - each targeting that exact field."""
    page_type = ALL_TYPES[tag]
    # An element-scoped reorder belongs to its element field, not to the list field it sits on.
    reorder_kind_by_target = {
        (command.section, command.field): command.kind
        for command in page_type.commands
        if command.kind in (REORDER_ELEMENT, REORDER_BLOCK) and command.element_field is None
    }
    element_block_reorders = {
        (command.section, command.field, command.element_field)
        for command in page_type.commands
        if command.kind == REORDER_BLOCK and command.element_field is not None
    }
    for section in page_type.sections:
        for field_spec in section.fields:
            target = (section.key, field_spec.key)
            if field_spec.kind == LIST:
                assert reorder_kind_by_target.get(target) == REORDER_ELEMENT, \
                    f"{tag}.{section.key}.{field_spec.key} (list) has no reorder_element command"
                for element_field in field_spec.block_element_fields():
                    assert (*target, element_field) in element_block_reorders, (
                        f"{tag}.{section.key}.{field_spec.key}.{element_field} (element blocks) "
                        f"has no reorder_block command"
                    )
            elif field_spec.kind == BLOCKS:
                assert reorder_kind_by_target.get(target) == REORDER_BLOCK, \
                    f"{tag}.{section.key}.{field_spec.key} (blocks) has no reorder_block command"


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_every_add_command_supports_positioned_insert(tag: str):
    """Every top-level add_element / add_block command accepts the optional index + precedingId
    (internal compound sub-steps like the flow's _addCommit are append-only and not surfaced here)."""
    page_type = ALL_TYPES[tag]
    for command in page_type.commands:
        if command.kind in (ADD_ELEMENT, ADD_BLOCK):
            arg_names = {arg.name for arg in command.args}
            assert {"index", "precedingId"} <= arg_names, \
                f"{tag}.{command.name} lacks positioned-insert args (index, precedingId)"


def test_list_cmds_threads_ref_check_onto_the_add_only():
    """Only the add carries the check: the remove and reorder name an element already on this
    page. `singular=` keeps the derived noun off the plural rule's 'dispatche'."""
    from src.pagetypes import ArgSpec, RefCheck, list_cmds
    ref = RefCheck(arg="workstreamId", scope="parent", section="workstreams", field="items")
    add, remove, reorder = list_cmds("dispatches", singular="dispatch",
                                     add_args=(ArgSpec("workstreamId"),), ref_check=ref)
    assert (add.name, remove.name, reorder.name) == ("addDispatch", "removeDispatch", "reorderDispatch")
    assert add.ref_check is ref
    assert remove.ref_check is None and reorder.ref_check is None


@pytest.mark.parametrize("tag", list(ALL_TYPES))
def test_field_setter_description_is_short_and_not_the_instruction(tag: str):
    """A field setter carries a short line saying what it sets, never its field's authoring
    instruction - that text lives once on the FieldSpec and reaches an agent through the `sections`
    listing and the `instruction` key of a `next` field edge."""
    page_type = ALL_TYPES[tag]
    for command in page_type.commands:
        if is_field_setter(command):
            field_spec = page_type.field_spec(command.section, command.field)
            assert field_spec is not None, f"{tag}.{command.name} targets a missing field"
            assert command.description, f"{tag}.{command.name} has no description"
            assert "\n" not in command.description, f"{tag}.{command.name} description is multi-line"
            assert command.description != field_spec.description, (
                f"{tag}.{command.name} still carries the {command.section}.{command.field} instruction"
            )


def _drift_type(setter_description: str, field_description: str):
    """A one-setter page type whose setter and field descriptions are both caller-controlled, so a
    test can pick which branch of the field-setter validation fires."""
    from src.pagetypes import CommandSpec, FSMSpec, PageType, SectionSpec, _prose, _text
    return PageType(
        tag="xtest-drift", name="Drift", description="ad-hoc",
        sections=(SectionSpec("summary", "Summary",
                              (_prose("body", description=field_description),)),),
        commands=(CommandSpec("setSummary", SET_PROSE, setter_description,
                              section="summary", field="body", args=(_text(),)),),
        fsm=FSMSpec(name="XDrift", initial="active", states=("active",)),
    )


def test_field_setter_with_an_empty_description_is_rejected():
    # The factories used to pass "" and rely on the mirror; nothing may ship description-less now.
    with pytest.raises(ValueError):
        _ = _drift_type("", "line one\nline two")


def test_field_setter_with_a_multiline_description_is_rejected():
    # An authoring instruction is a wrapped multi-line block; a setter takes one short line.
    with pytest.raises(ValueError):
        _ = _drift_type("line one\nline two", "line one\nline two")


def test_field_setter_repeating_a_single_line_field_instruction_is_rejected():
    # The equality branch, which is the only thing that catches a one-line instruction.
    with pytest.raises(ValueError):
        _ = _drift_type("the whole instruction", "the whole instruction")


def test_field_setter_targeting_an_unknown_field_is_still_rejected():
    # The pre-existing check must survive the guard rewrite it sat beside.
    from src.pagetypes import CommandSpec, FSMSpec, PageType, SectionSpec, _prose, _text
    with pytest.raises(ValueError):
        _ = PageType(
            tag="xtest-ghost", name="Ghost", description="ad-hoc",
            sections=(SectionSpec("summary", "Summary", (_prose("body", description="x"),)),),
            commands=(CommandSpec("setGhost", SET_PROSE, "set the ghost",
                                  section="summary", field="missing", args=(_text(),)),),
            fsm=FSMSpec(name="XGhost", initial="active", states=("active",)),
        )


# ============================================================================
# content-specific assertions - pinned to the hand-authored fixtures (src.testtypes),
# so enriching a production type never breaks them.
# ============================================================================
BLK = get_page_type("test-blocks")     # the full blocks / inline-run surface
CHILD = get_page_type("test-child")    # element-FSM lists + a blocks decisions field
LIFE = get_page_type("test-lifecycle")


def test_blocks_fixture_has_full_block_surface():
    """A blocks field's whole surface is three commands, whatever its vocabulary. The fixture
    accepts every standard kind and still declares exactly one add."""
    names = {command.name for command in BLK.commands}
    assert {"addBody", "reorderBlock", "removeBlock", "addLink", "setTitle"} == names


def test_add_link_on_every_authorable_type_but_not_toc():
    # add_link_cmd() is added to every authorable production page type; the command-less toc is the
    # sole exception - it has no authoring surface at all, so it must NOT carry addLink.
    for tag, page_type in REGISTRY.items():
        command = page_type.command("addLink")
        if tag == "toc":
            assert command is None, "toc cannot be authored - it must not carry addLink"
        else:
            assert command is not None and command.kind == ADD_LINK, f"{tag} is missing addLink"
    # a single `active` state, no transitions
    assert BLK.fsm.initial == "active" and BLK.fsm.transitions == ()


def test_set_title_on_every_authorable_type_but_not_toc():
    # set_title_cmd() - the universal rename alias - is added to every authorable production page type
    # alongside addLink; the command-less toc is the sole exception with no authoring surface.
    for tag, page_type in REGISTRY.items():
        command = page_type.command("setTitle")
        if tag == "toc":
            assert command is None, "toc cannot be authored - it must not carry setTitle"
        else:
            assert command is not None and command.kind == SET_TITLE, f"{tag} is missing setTitle"


def test_reorder_split_into_two_kinds_with_anchored_args():
    # The list-field and blocks-field reorders are two parallel kinds; both carry (id, toIndex, precedingId).
    child_names = {command.name for command in CHILD.commands}
    assert "reorderStep" in child_names and "moveStep" not in child_names and "reorderSteps" not in child_names
    assert CHILD.command("reorderStep").kind == REORDER_ELEMENT
    assert [arg.name for arg in CHILD.command("reorderStep").args] == ["stepId", "toIndex", "precedingId"]
    assert BLK.command("reorderBlock").kind == REORDER_BLOCK
    assert [arg.name for arg in BLK.command("reorderBlock").args] == ["blockId", "toIndex", "precedingId"]


def test_blocks_body_is_an_inline_run_blocks_field():
    """The kind is data in the argument now, so the paragraph's inline-run shape is reached
    through the field's vocabulary rather than through a per-kind command."""
    field = BLK.field_spec("body", "body")
    assert field.kind == BLOCKS
    add = BLK.command("addBody")
    assert add.kind == ADD_BLOCK and add.args[0].content == BLOCK_ARRAY
    paragraph = next(kind for kind in field.block_vocabulary() if kind.kind == "paragraph")
    assert paragraph.body_args()[0].content == INLINE_RUNS


def test_element_fsms_declare_checkmark_done():
    """The checkbox mapping lives on the element FSM (ElementFSMSpec): checkmark_done names the [x]
    state, `initial` is the [ ] state, and an element FSM without checkmark_done renders no box. A
    page-status FSMSpec has no checkmark_done at all - page states are never checkboxes."""
    step_fsm = CHILD.field_spec("steps", "items").element_fsm
    check_fsm = CHILD.field_spec("checks", "items").element_fsm
    question_fsm = LIFE.field_spec("questions", "items").element_fsm
    assert step_fsm.checkmark_done == "done"         # initial "todo" -> [ ], "done" -> [x]
    assert check_fsm.checkmark_done == "passed"      # "pending" -> [ ], "passed" -> [x], "failed" -> no box
    assert question_fsm.checkmark_done is None       # open/answered render without a box
    assert not hasattr(LIFE.fsm, "checkmark_done")   # a page-status FSM is not a checkbox FSM


def test_auto_children_are_specs_and_pinned_detection():
    from src.pagetypes import AutoChildSpec, is_auto_child_type
    # auto_children are AutoChildSpec instances naming the pinned child types (the test-child fixture)
    assert all(isinstance(spec, AutoChildSpec) for spec in LIFE.auto_children)
    assert {spec.type for spec in LIFE.auto_children} == {"test-child"}
    # is_auto_child_type: true for a declared auto-child; false otherwise, for a childless type, or None
    assert is_auto_child_type(LIFE, "test-child") is True
    assert is_auto_child_type(LIFE, "test-fields") is False
    assert is_auto_child_type(CHILD, "test-child") is False
    assert is_auto_child_type(None, "test-child") is False


# --- inline-run grammar validation (pure, not tied to any page type) ---------
def test_validate_inline_runs_accepts_the_run_grammar():
    validate_inline_content(INLINE_RUNS, [
        "plain",
        {"text": "bold", "bold": True},
        {"text": "linked", "href": "https://x"},
        {"code": "x = 1"},
        {"ref": "architecture:abc"},
    ])


def test_validate_inline_runs_rejects_markdown_in_a_text_run():
    with pytest.raises(ValidationError):
        validate_inline_content(INLINE_RUNS, ["**bold**"])            # bare string
    with pytest.raises(ValidationError):
        validate_inline_content(INLINE_RUNS, [{"text": "a `code` b"}])  # inside a text run


def test_validate_inline_runs_rejects_malformed_runs():
    with pytest.raises(ValidationError):
        validate_inline_content(INLINE_RUNS, [{"ref": "x", "text": "y"}])   # ref must stand alone
    with pytest.raises(ValidationError):
        validate_inline_content(INLINE_RUNS, [{"text": "x", "bogus": 1}])   # unknown key
    with pytest.raises(ValidationError):
        validate_inline_content(INLINE_RUNS, [123])                         # not a run


def test_validate_run_lists_and_grid_and_align():
    validate_inline_content(INLINE_RUN_LISTS, [["a"], [{"text": "b", "italic": True}]])
    validate_inline_content(INLINE_RUN_GRID, [[["r0c0"], ["r0c1"]]])
    validate_inline_content(TABLE_ALIGN, ["left", "center", "right", None])
    with pytest.raises(ValidationError):
        validate_inline_content(TABLE_ALIGN, ["middle"])


def test_validate_table_width_consistency():
    validate_table(["h0", "h1"], [["a", "b"], ["c", "d"]], ["left", None])
    with pytest.raises(ValidationError):
        validate_table(["h0", "h1"], [["a"]], None)                 # row too narrow
    with pytest.raises(ValidationError):
        validate_table(["h0", "h1"], [["a", "b"]], ["left"])        # align width mismatch


def test_collect_ref_ids_across_shapes():
    # INLINE_RUNS: refs are gathered; non-ref runs (str/text/code) are ignored.
    assert collect_ref_ids(INLINE_RUNS, ["x", {"ref": "a:1"}, {"text": "t"}, {"code": "c"}]) == ["a:1"]
    # INLINE_RUN_LISTS: list items / quote paragraphs / table header cells.
    assert collect_ref_ids(INLINE_RUN_LISTS, [["x", {"ref": "a:1"}], [{"ref": "a:2"}]]) == ["a:1", "a:2"]
    # INLINE_RUN_GRID: table rows of cells.
    assert collect_ref_ids(INLINE_RUN_GRID, [[[{"ref": "a:1"}], ["x"]], [[{"ref": "a:2"}]]]) == ["a:1", "a:2"]
    # TABLE_ALIGN carries no runs; and a non-string ref is ignored (left for grammar validation).
    assert collect_ref_ids(TABLE_ALIGN, ["left", "center"]) == []
    assert collect_ref_ids(INLINE_RUNS, [{"ref": 123}]) == []


# --- FSMSpec.state_guidance --------------------------------------------------
def test_state_guidance_normalizes_authored_text():
    # Authored as an indented block; it must arrive as written, wrap breaks kept.
    spec = FSMSpec(name="G", initial="a", states=("a", "b"),
                   state_guidance=(("b", "\n    line one\n    line two\n  "),))
    assert spec.guidance_for("b") == "line one\nline two"


def test_guidance_for_returns_none_for_undeclared_state():
    # None rather than "", so the caller can tell undeclared from empty.
    spec = FSMSpec(name="G", initial="a", states=("a", "b"),
                   state_guidance=(("b", "some guidance"),))
    assert spec.guidance_for("a") is None


def test_state_guidance_rejects_unknown_state():
    # A typo in a state name fails at import rather than silently never appearing.
    with pytest.raises(ValueError, match="unknown state"):
        FSMSpec(name="G", initial="a", states=("a",), state_guidance=(("nope", "x"),))


def test_state_guidance_rejects_duplicate_state():
    with pytest.raises(ValueError, match="twice"):
        FSMSpec(name="G", initial="a", states=("a",),
                state_guidance=(("a", "x"), ("a", "y")))


def test_every_production_guidance_text_comes_from_the_stage_guidance_module():
    # A page-type module declaring its own inline text is the thing this rules out: the
    # constants are normalized here the same way FSMSpec normalizes what it is handed.
    constants = {dedent(value.strip("\n")).rstrip()
                 for name, value in vars(_stage_guidance).items()
                 if name.isupper() and isinstance(value, str)}
    declared = [(tag, state, text)
                for tag, page_type in REGISTRY.items()
                for state, text in page_type.fsm.state_guidance]
    assert declared, "no production page type declares stage guidance"
    for tag, state, text in declared:
        assert text in constants, f"{tag}.{state} guidance is not a _stage_guidance constant"


# --- A block kind's declared vocabulary ---------------------------------------
def test_block_kind_spec_resolves_body_args():
    # A standard kind takes its body from the shared table; an override replaces that body
    # outright, which is what lets one kind name mean different things in different fields.
    assert BlockKindSpec("code").body_args() == BLOCK_ARGS["code"]
    override = BlockKindSpec("paragraph", args=(_text(),))
    assert override.body_args() == (_text(),)
    assert override.body_args() != BLOCK_ARGS["paragraph"]


def test_block_kind_spec_rejects_a_bad_declaration():
    # A kind the shared table does not know has no body to fall back on, so it must declare one -
    # and declaring one is exactly how a custom kind like `decision` is defined.
    with pytest.raises(ValueError, match="not a standard kind"):
        BlockKindSpec("decision")
    custom = BlockKindSpec("decision", args=(_text("questionId"), _text()))
    assert custom.body_args() == (_text("questionId"), _text())
    with pytest.raises(ValueError, match="non-empty name"):
        BlockKindSpec("")


def test_field_spec_block_vocabulary():
    # An undeclared blocks field accepts every standard kind; a declared one accepts exactly
    # what it names, in the order it names them.
    assert _blocks("body").block_vocabulary() == STANDARD_BLOCK_KINDS
    assert [kind.kind for kind in _blocks("body").block_vocabulary()] == [
        "paragraph", "heading", "code", "list", "quote", "table", "divider"]
    restricted = _blocks("body", block_kinds=("code", "paragraph"))
    assert [kind.kind for kind in restricted.block_vocabulary()] == ["code", "paragraph"]


def test_field_spec_rejects_a_bad_block_vocabulary():
    with pytest.raises(ValueError, match="only valid on a blocks field"):
        FieldSpec(key="body", kind=PROSE, block_kinds=("code",))
    with pytest.raises(ValueError, match="twice"):
        _blocks("body", block_kinds=("code", "code"))


# --- Block-bearing element fields --------------------------------------------
def test_element_blocks_spec_is_hashable():
    # FieldSpec is reachable from the FSMSpec that keys fsm._machine_class's lru_cache, so a
    # declaration that cannot be hashed would break every page type at once.
    assert {ElementBlocksSpec("detail", ("code",))} == {ElementBlocksSpec("detail", ("code",))}
    field = FieldSpec(key="items", kind=LIST, element_fields=("text", "detail"),
                      element_blocks=(ElementBlocksSpec("detail", ("code",)),))
    assert len({field, field}) == 1


def test_element_blocks_rejects_a_bad_declaration():
    # Every defect fails where the type is declared, not when someone tries to author the field.
    with pytest.raises(ValueError, match="only valid on a list field"):
        FieldSpec(key="body", kind=PROSE,
                  element_blocks=(ElementBlocksSpec("detail", ("code",)),))
    with pytest.raises(ValueError, match="not one of element_fields"):
        FieldSpec(key="items", kind=LIST, element_fields=("text",),
                  element_blocks=(ElementBlocksSpec("nope", ("code",)),))
    with pytest.raises(ValueError, match="twice"):
        FieldSpec(key="items", kind=LIST, element_fields=("text", "detail"),
                  element_blocks=(ElementBlocksSpec("detail", ("code",)),
                                  ElementBlocksSpec("detail", ("paragraph",))))
    # The unknown-kind check lives on BlockKindSpec now - the kind is what is malformed.
    with pytest.raises(ValueError, match="not a standard kind"):
        FieldSpec(key="items", kind=LIST, element_fields=("text", "detail"),
                  element_blocks=(ElementBlocksSpec("detail", ("paragraph", "nope")),))
    # A field declared to hold blocks but accepting none could never be authored.
    with pytest.raises(ValueError, match="no block kinds"):
        FieldSpec(key="items", kind=LIST, element_fields=("text", "detail"),
                  element_blocks=(ElementBlocksSpec("detail", ()),))


def test_block_element_fields_names_the_declared_fields():
    field = _list("items", element_fields=("text", "snippet", "detail"),
                  element_blocks=(ElementBlocksSpec("snippet", ("code",)),
                                  ElementBlocksSpec("detail", ("paragraph",))))
    assert field.block_element_fields() == ("snippet", "detail")     # declared order
    snippet = field.element_blocks_spec("snippet")
    assert snippet is not None and [kind.kind for kind in snippet.vocabulary()] == ["code"]
    assert field.element_blocks_spec("text") is None                 # a scalar element field
    # A list declaring none reports an empty tuple - what keeps every consumer's scalar path intact.
    assert _list("items", element_fields=("text",)).block_element_fields() == ()


def test_validate_block_accepts_one_declared_block():
    validate_block({"kind": "code", "language": "py", "source": "x = 1"}, _kinds("code"))


def test_validate_block_rejects_an_undeclared_kind():
    with pytest.raises(ValidationError, match="not accepted here"):
        validate_block({"kind": "table", "header": [], "rows": []}, _kinds("code"))


def test_validate_block_reads_a_per_field_arg_override():
    """The case the whole vocabulary design exists for: one kind name, two body shapes.

    A field declaring `paragraph` as a plain text arg accepts `text` and rejects the standard
    `inlines`, and a field declaring the standard kind does the reverse. If any consumer fell
    back to the global BLOCK_ARGS this would pass in one direction only.
    """
    plain = (BlockKindSpec("paragraph", args=(_text(),)),)
    validate_block({"kind": "paragraph", "text": "just prose"}, plain)
    with pytest.raises(ValidationError, match="unknown keys"):
        validate_block({"kind": "paragraph", "inlines": ["prose"]}, plain)
    validate_block({"kind": "paragraph", "inlines": ["prose"]}, _kinds("paragraph"))
    with pytest.raises(ValidationError, match="unknown keys"):
        validate_block({"kind": "paragraph", "text": "just prose"}, _kinds("paragraph"))


def test_validate_block_accepts_a_kind_the_standard_table_does_not_know():
    """A custom kind exists only as a name plus declared args - feature-spec's `decision`."""
    decision = (BlockKindSpec("decision", args=(_text("questionId"), _text())),)
    validate_block({"kind": "decision", "questionId": "q:1", "text": "we chose X"}, decision)
    with pytest.raises(ValidationError, match="requires 'questionId'"):
        validate_block({"kind": "decision", "text": "we chose X"}, decision)


def test_validate_blocks_accepts_declared_kinds():
    validate_blocks([{"kind": "code", "language": "py", "source": "x = 1"}], _kinds("code"))
    validate_blocks([], _kinds("code"))                          # an empty array is legal
    validate_blocks(
        [{"kind": "paragraph", "inlines": ["prose ", {"code": "x"}, {"ref": "a:1"}]}],
        _kinds("paragraph", "code"))


def test_validate_blocks_rejects_a_bad_entry():
    with pytest.raises(ValidationError, match="array of blocks"):
        validate_blocks("nope", _kinds("code"))
    with pytest.raises(ValidationError, match="must be an object"):
        validate_blocks(["nope"], _kinds("code"))
    with pytest.raises(ValidationError, match="not accepted here"):
        validate_blocks([{"kind": "table"}], _kinds("code"))
    with pytest.raises(ValidationError, match="unknown keys"):
        validate_blocks([{"kind": "code", "language": "py", "source": "x", "nope": 1}],
                        _kinds("code"))
    with pytest.raises(ValidationError, match="requires 'source'"):
        validate_blocks([{"kind": "code", "language": "py"}], _kinds("code"))
    # The same inline grammar the per-kind command enforced, reached through the same validator.
    with pytest.raises(ValidationError, match="Markdown syntax"):
        validate_blocks([{"kind": "paragraph", "inlines": ["a **bold** word"]}],
                        _kinds("paragraph"))
    # The table width cross-check lives inside the block validator, not on a command.
    with pytest.raises(ValidationError, match="header"):
        validate_blocks([{"kind": "table", "header": [["a"], ["b"]], "rows": [[["1"]]]}],
                        _kinds("table"))


def test_collect_ref_ids_finds_a_ref_inside_a_block():
    # Without this the store's inline-ref precheck cannot see a ref carried inside a block.
    kinds = _kinds("paragraph", "code")
    assert collect_ref_ids(BLOCK_ARRAY, [
        {"kind": "paragraph", "inlines": ["see ", {"ref": "a:1"}]},
        {"kind": "code", "language": "py", "source": "x = 1"},
    ], kinds) == ["a:1"]
    assert collect_ref_ids(BLOCK_ARRAY, "not-a-list", kinds) == []


def test_collect_ref_ids_reads_runs_off_an_overridden_kind():
    """An override moves a kind's runs to a different arg, so reading BLOCK_ARGS would miss them."""
    kinds = (BlockKindSpec("note", args=(_array("body", content=INLINE_RUNS),)),)
    assert collect_ref_ids(
        BLOCK_ARRAY, [{"kind": "note", "body": ["see ", {"ref": "a:1"}]}], kinds) == ["a:1"]


def test_collect_ref_ids_without_a_vocabulary_yields_nothing():
    """It runs before grammar validation, so it guesses at nothing and raises at nothing:
    no vocabulary, an undeclared kind and a malformed entry all yield no ids."""
    assert collect_ref_ids(BLOCK_ARRAY, [{"kind": "paragraph", "inlines": [{"ref": "a:1"}]}]) == []
    assert collect_ref_ids(
        BLOCK_ARRAY, [{"kind": "nope", "inlines": [{"ref": "a:1"}]}], _kinds("paragraph")) == []
    assert collect_ref_ids(BLOCK_ARRAY, ["nope", 7, None], _kinds("paragraph")) == []


def _arg_names(command):
    return tuple(arg.name for arg in command.args)


def test_list_cmds_adds_an_optional_blocks_arg_per_declared_field():
    steps = _list("items", element_fields=("text", "detail"),
                  element_blocks=(ElementBlocksSpec("detail", ("paragraph", "code")),))
    add, _remove, _reorder = _resolved(
        "steps", "Steps", steps,
        list_cmds("steps", label="step", add_args=(_text(),), element_blocks=("detail",)))
    assert _arg_names(add) == ("text", "detail", "index", "precedingId")
    detail = add.args[1]
    assert detail.required is False and detail.type == "array"
    assert detail.content == BLOCK_ARRAY
    assert [kind.kind for kind in detail.block_kinds or ()] == ["paragraph", "code"]
    # Never written raw onto the element - the add converts it into id'd blocks instead.
    assert "detail" not in dict(add.element_map)


def _resolved(section_key, title, field_spec, commands):
    """`commands` as the page type owning `field_spec` resolves them.

    A factory hands back block arguments with no vocabulary; the page type fills them in, so
    that is what every consumer sees and what these tests assert against.
    """
    return PageType(
        tag="xtest-resolved-factory", name="Resolved factory", description="ad-hoc",
        sections=(SectionSpec(section_key, title, (field_spec,)),),
        commands=commands,
        fsm=FSMSpec(name="XTestResolvedFactory", initial="active", states=("active",)),
    ).commands


def test_blocks_cmds_is_three_commands_named_from_the_label():
    """One add per blocks field, named by _setter_label - the section for a `body` field, the
    field key otherwise - beside the remove and reorder it already had."""
    body = _blocks("body")
    add, remove, reorder = _resolved("body", "Body", body, blocks_cmds("body"))
    assert (add.name, remove.name, reorder.name) == ("addBody", "removeBlock", "reorderBlock")
    assert _arg_names(add) == ("blocks", "index", "precedingId")
    assert _arg_names(remove) == ("blockId",)
    assert _arg_names(reorder) == ("blockId", "toIndex", "precedingId")
    # The add's array carries the field's vocabulary, so the schema and the validator read the
    # same declaration.
    assert add.args[0].content == BLOCK_ARRAY
    assert add.args[0].block_kinds == body.block_vocabulary()
    assert add.args[0].required
    # It writes no raw argument onto anything - it converts the array into id'd blocks.
    assert dict(add.element_map) == {}


def test_blocks_cmds_label_and_name_overrides():
    models = _blocks("models", block_kinds=("code",))
    add, remove, reorder = _resolved(
        "dataModels", "Data models", models,
        blocks_cmds("dataModels", field="models", label="dataModels",
                    remove_name="removeDataModel", reorder_name="reorderDataModel"))
    assert (add.name, remove.name, reorder.name) == (
        "addDataModels", "removeDataModel", "reorderDataModel")
    # A restricted field offers exactly what it declares.
    assert [kind.kind for kind in add.args[0].block_kinds or ()] == ["code"]
    named, _remove, _reorder = blocks_cmds("body", add_name="addProse")
    assert named.name == "addProse"


def test_element_blocks_cmds_leads_with_the_element_id():
    """The element noun leads and the declared field key follows, with no pluralizing."""
    steps = _list("items", element_fields=("detail", "status"),
                  element_blocks=(ElementBlocksSpec("detail", ("paragraph", "code")),))
    add, remove, reorder = _resolved(
        "steps", "Steps", steps, element_blocks_cmds("steps", "detail"))
    assert (add.name, remove.name, reorder.name) == (
        "addStepDetail", "removeStepDetail", "reorderStepDetail")
    assert _arg_names(add) == ("stepId", "blocks", "index", "precedingId")
    assert _arg_names(remove) == ("stepId", "blockId")
    assert _arg_names(reorder) == ("stepId", "blockId", "toIndex", "precedingId")
    # element_field is the single seam that routes each command one level deeper.
    for command in (add, remove, reorder):
        assert command.element_field == "detail"
        assert command.section == "steps" and command.field == "items"
    assert [kind.kind for kind in add.args[1].block_kinds or ()] == ["paragraph", "code"]


def test_validate_block_and_validate_blocks_are_one_grammar():
    """A block legal to create is legal to set, and the reverse - both run validate_block.

    The add and the set are the two paths a block can arrive by; if they ever validated
    separately they could drift, and a block would be settable but not creatable.
    """
    kinds = _kinds(*[spec.kind for spec in STANDARD_BLOCK_KINDS])
    samples = [
        {"kind": "paragraph", "inlines": ["ok"]},
        {"kind": "heading", "level": 2, "inlines": ["ok"]},
        {"kind": "code", "language": "py", "source": "x = 1"},
        {"kind": "divider"},
    ]
    bad = [
        {"kind": "paragraph", "nope": 1},
        {"kind": "code", "language": "py"},
        {"kind": "not-a-kind"},
        "not-an-object",
    ]
    for entry in samples:
        validate_block(entry, kinds)
        validate_blocks([entry], kinds)
    for entry in bad:
        with pytest.raises(ValidationError) as one:
            validate_block(entry, kinds)
        with pytest.raises(ValidationError) as many:
            validate_blocks([entry], kinds)
        assert str(one.value) == str(many.value)


def test_the_block_surface_is_three_commands_per_field():
    """The headline outcome, and the only place it is pinned.

    One add per blocks field - seven page-level fields plus one block-bearing element field - and
    no in-place edit at all: a block is replaced by removing it and adding at its slot.
    """
    adds = [command.name
            for page_type in REGISTRY.values()
            for command in page_type.commands
            if command.kind == ADD_BLOCK]
    assert len(adds) == 8
    document = {command.name for command in REGISTRY["document"].commands}
    assert document == {"addBody", "removeBlock", "reorderBlock", "addLink", "setTitle"}


def test_block_command_names_match_the_declared_surface():
    """Every production add name, and the remove/reorder names it sits beside - which are
    byte-identical to what they were before the sets were dropped."""
    names = {tag: {command.name for command in page_type.commands}
             for tag, page_type in REGISTRY.items()}
    assert {"addBody", "removeBlock", "reorderBlock"} <= names["document"]
    assert {"addDetails", "removeNote", "reorderNote"} <= names["architecture"]
    assert {"addDecision", "removeDecisionBlock", "reorderDecisionBlock",
            "addConsequences", "removeConsequence",
            "reorderConsequence"} <= names["decision-record"]
    assert {"addDesign", "removeDesignBlock", "reorderDesignBlock",
            "addDecisions", "removeDecision", "reorderDecision"} <= names["feature-spec"]
    assert {"addDataModels", "removeDataModel", "reorderDataModel",
            "addStepDetail", "removeStepDetail",
            "reorderStepDetail"} <= names["implementation-plan"]
    # Names stay unique within a type.
    for tag, page_type in REGISTRY.items():
        declared = [command.name for command in page_type.commands]
        assert len(declared) == len(set(declared)), f"{tag} declares a duplicate command name"


def test_two_do_eligible_setters_for_one_field_are_rejected():
    """A `do` field edge names one command, so a second would be silently dropped rather than
    raise. This is what makes the singular key safe against a future page type."""
    body = _blocks("body")
    with pytest.raises(ValueError, match="two field setters"):
        PageType(
            tag="xtest-two-setters", name="Two setters", description="ad-hoc",
            sections=(SectionSpec("body", "Body", (body,)),),
            commands=(set_prose_cmd("body"), *blocks_cmds("body")),
            fsm=FSMSpec(name="XTestTwoSetters", initial="active", states=("active",)),
        )
    # Every registered type passes it - the five collapsing blocks fields were the only ones
    # that ever carried more than one.
    for page_type in {**REGISTRY, **TEST_REGISTRY}.values():
        validate_pagetype_field_setters(page_type)


def test_a_block_argument_is_resolved_from_its_field():
    """A command factory names the section and field it builds for; the page type is the first
    thing holding both, so it is what turns that into a vocabulary."""
    body = _blocks("body", block_kinds=("code", "paragraph"))
    page_type = PageType(
        tag="xtest-resolved", name="Resolved", description="ad-hoc",
        sections=(SectionSpec("body", "Body", (body,)),),
        commands=(
            CommandSpec("addBody", ADD_BLOCK, "add blocks to the body",
                        section="body", field="body",
                        args=(_array("blocks", content=BLOCK_ARRAY), _text("precedingId"))),
            CommandSpec("removeBlock", REMOVE_BLOCK, "remove a block",
                        section="body", field="body", args=(_text("blockId"),)),
        ),
        fsm=FSMSpec(name="XTestResolved", initial="active", states=("active",)))
    add, remove = page_type.commands
    assert add.args[0].block_kinds == body.block_vocabulary()
    assert [kind.kind for kind in add.args[0].block_kinds or ()] == ["code", "paragraph"]
    # An argument that carries no blocks is returned untouched, on either command.
    assert add.args[1].block_kinds is None and remove.args[0].block_kinds is None


def _targeted_vocabulary(page_type, command, arg):
    """The vocabulary `arg` should carry, worked out from the sections independently of the
    resolution step - so the two can be compared rather than one trusting the other."""
    field_spec = page_type.field_spec(command.section, command.field)
    assert field_spec is not None
    element_field = command.element_field or (
        arg.name if command.kind == ADD_ELEMENT else None)
    if element_field is None:
        return field_spec.block_vocabulary()
    spec = field_spec.element_blocks_spec(element_field)
    assert spec is not None
    return spec.vocabulary()


def test_resolution_reproduces_the_declared_vocabularies():
    """Every block-carrying argument in the whole registry carries exactly the vocabulary of the
    field it targets. Run while the factories still supply the kinds, this says the resolver reads
    the right field; run after they stop, it says the resolver is the only supplier."""
    checked = 0
    for page_type in ALL_TYPES.values():
        for command in page_type.commands:
            for arg in command.args:
                if arg.content != BLOCK_ARRAY:
                    continue
                assert arg.block_kinds == _targeted_vocabulary(page_type, command, arg), (
                    f"{page_type.tag}.{command.name} argument '{arg.name}'")
                checked += 1
    assert checked >= 8       # 7 page-level fields, plus the element-scoped add


def test_a_block_argument_that_cannot_be_resolved_is_rejected_at_import():
    """The only place a bad block declaration can be caught.

    Every consumer reads block_kinds as "not a block argument" when it is None, so an
    unresolved argument would accept any block, describe itself as an untyped array and lose
    its cross-page ref check - silently, with nothing raising.
    """
    def page_type(section_spec, command):
        return PageType(
            tag="xtest-unresolvable", name="Unresolvable", description="ad-hoc",
            sections=(section_spec,), commands=(command,),
            fsm=FSMSpec(name="XTestUnresolvable", initial="active", states=("active",)))

    add = CommandSpec("addBody", ADD_BLOCK, "add blocks to the body",
                      section="body", field="body",
                      args=(_array("blocks", content=BLOCK_ARRAY),))
    with pytest.raises(ValueError, match="not a declared field"):
        page_type(SectionSpec("body", "Body", (_blocks("other"),)), add)
    with pytest.raises(ValueError, match="not a blocks field"):
        page_type(SectionSpec("body", "Body", (_prose("body"),)), add)

    items = _list("items", element_fields=("text", "detail"))
    element_add = CommandSpec("addItemDetail", ADD_BLOCK, "add blocks to an item's detail",
                              section="items", field="items", element_field="detail",
                              args=(_text("itemId"), _array("blocks", content=BLOCK_ARRAY)))
    with pytest.raises(ValueError, match="block-bearing element field"):
        page_type(SectionSpec("items", "Items", (items,)), element_add)

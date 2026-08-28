"""Unit tests for the pure write path (src.commands)."""

import pytest

from src.commands import (
    apply_command,
    BatchContext,
    create_page,
    field_setter_edges,
    is_field_setter,
    legal_commands,
    resolve_anchored_slot,
    transition_guidance,
)
from src.errors import ConflictError, IllegalCommandError, NotFoundError, ValidationError
from src.model import Page
from src.pagetypes import (
    FSMSpec,
    PageType,
    ElementBlocksSpec,
    SectionSpec,
    _blocks,
    _list,
    _prose,
    _table_block,
    _text,
    add_link_cmd,
    blocks_cmds,
    get_page_type,
    initial_sections,
    blocks_cmds,
    list_cmds,
    set_prose_cmd,
    set_title_cmd,
    standard_blocks,
    transition_cmd,
)

# Hand-authored capability fixtures (src.testtypes) - purpose-built so enriching a production
# type never churns these command-surface assertions.
FIELDS = get_page_type("test-fields")   # scalar / enum / prose / list / set-element-field
FLOW = get_page_type("test-flow")       # a 3-state status FSM + a compound `close`


def make_counter():
    """Deterministic id factory: pages get `<prefix>:N`, elements get `elN`."""
    state = {"n": 0}

    def factory(prefix: str) -> str:
        state["n"] += 1
        return f"{prefix}:{state['n']}" if prefix else f"el{state['n']}"

    return factory


def new_fields():
    return create_page(FIELDS, "A fixture", None, make_counter())


# --- create_page -------------------------------------------------------------
def test_create_page_initial_state():
    page = create_page(FIELDS, "A fixture", None, make_counter())
    assert page.type == "test-fields"
    assert page.status == "active"         # FSM initial
    assert page.sections["basics"]["label"] is None
    assert page.sections["basics"]["body"] == ""
    assert page.sections["items"]["items"] == []


def test_create_page_rejects_blank_title():
    with pytest.raises(ValidationError):
        _ = create_page(FIELDS, "   ", None, make_counter())


def test_apply_command_backfills_a_section_missing_from_the_stored_page():
    # Simulates a page type gaining a new section/field after some of its pages already exist:
    # the stored page predates "items" and must not raise when a command targets it.
    page = create_page(FIELDS, "A fixture", None, make_counter())
    del page.sections["items"]
    result = apply_command(page, FIELDS, "addItem", {"text": "hi"}, make_counter())
    assert [item["text"] for item in result.page.sections["items"]["items"]] == ["hi"]
    assert result.created_id is not None


def test_add_link_appends_edge_and_is_always_legal():
    # addLink is on every authorable type and always legal; the pure core appends the edge
    # to Page.links (cross-page validation - existence, dedup - is the store's job).
    source = get_page_type("test-fields")
    page = create_page(source, "Src", None, make_counter())
    assert legal_commands(page, source)["addLink"] is True
    linked = apply_command(page, source, "addLink",
                           {"toId": "test-flow:t", "role": "relates-to"}, make_counter())
    assert linked.page.links == [{"to": "test-flow:t", "role": "relates-to"}]
    assert linked.created_id is None


def test_set_title_renames_and_is_always_legal():
    # setTitle is the universal rename alias - on every authorable type and always legal; the pure
    # core sets Page.title (uniqueness/cross-page checks don't apply - a title is a display label).
    source = get_page_type("test-fields")
    page = create_page(source, "Old name", None, make_counter())
    assert legal_commands(page, source)["setTitle"] is True
    renamed = apply_command(page, source, "setTitle", {"title": "New name"}, make_counter())
    assert renamed.page.title == "New name"
    assert renamed.created_id is None


def test_set_title_rejects_blank_title():
    # A blank/whitespace-only title is rejected with the same message as renamePage / create_page.
    source = get_page_type("test-fields")
    page = create_page(source, "Keep me", None, make_counter())
    with pytest.raises(ValidationError):
        _ = apply_command(page, source, "setTitle", {"title": "   "}, make_counter())


# --- scalar / enum -----------------------------------------------------------
def test_set_scalar_ok():
    result = apply_command(new_fields(), FIELDS, "setLabel", {"label": "core"}, make_counter())
    assert result.page.sections["basics"]["label"] == "core"


def test_set_scalar_enum_ok():
    result = apply_command(new_fields(), FIELDS, "setKind", {"kind": "alpha"}, make_counter())
    assert result.page.sections["basics"]["kind"] == "alpha"


def test_set_scalar_enum_rejects_bad_value():
    with pytest.raises(ValidationError):
        _ = apply_command(new_fields(), FIELDS, "setKind", {"kind": "widget"}, make_counter())


# --- prose -------------------------------------------------------------------
def test_set_prose():
    result = apply_command(new_fields(), FIELDS, "setBody", {"text": "The core."}, make_counter())
    assert result.page.sections["basics"]["body"] == "The core."


# --- list add / remove / set-element-field -----------------------------------
def test_add_element_returns_created_id_and_appends():
    result = apply_command(new_fields(), FIELDS, "addItem", {"text": "Item text."}, make_counter())
    items = result.page.sections["items"]["items"]
    assert len(items) == 1
    assert items[0]["text"] == "Item text."
    assert result.created_id == items[0]["id"]


def test_add_element_optional_arg_defaults_to_none():
    result = apply_command(new_fields(), FIELDS, "addItem", {"text": "x"}, make_counter())
    item = result.page.sections["items"]["items"][0]
    assert item["text"] == "x"
    assert item["note"] is None            # optional arg omitted


def test_remove_element_ok_and_missing_raises():
    factory = make_counter()
    added = apply_command(new_fields(), FIELDS, "addItem", {"text": "x"}, factory)
    removed = apply_command(added.page, FIELDS, "removeItem", {"itemId": added.created_id}, factory)
    assert removed.page.sections["items"]["items"] == []
    with pytest.raises(NotFoundError):
        _ = apply_command(removed.page, FIELDS, "removeItem", {"itemId": "nope"}, factory)


def test_set_element_field_sets_a_flag_in_place():
    factory = make_counter()
    added = apply_command(new_fields(), FIELDS, "addItem", {"text": "x"}, factory)
    flagged = apply_command(added.page, FIELDS, "flagItem", {"itemId": added.created_id}, factory)
    item = flagged.page.sections["items"]["items"][0]
    assert item["flagged"] is True
    assert flagged.created_id is None      # a set, not an add


# --- argument validation -----------------------------------------------------
def test_unknown_command_raises():
    with pytest.raises(ValidationError):
        _ = apply_command(new_fields(), FIELDS, "setNonsense", {}, make_counter())


def test_missing_required_arg_raises():
    with pytest.raises(ValidationError):
        _ = apply_command(new_fields(), FIELDS, "setBody", {}, make_counter())


def test_unknown_arg_raises():
    with pytest.raises(ValidationError):
        _ = apply_command(new_fields(), FIELDS, "setBody", {"text": "ok", "extra": 1}, make_counter())


# --- purity ------------------------------------------------------------------
def test_apply_command_does_not_mutate_input():
    page = new_fields()
    apply_command(page, FIELDS, "setBody", {"text": "changed"}, make_counter())
    assert page.sections["basics"]["body"] == ""   # original untouched


# --- anchored reorder + positioned insert (a plain, always-editable list) -----
def _fields_with_items(factory, *labels):
    """A fixture page carrying one item per label; returns (page, {label: itemId})."""
    page = create_page(FIELDS, "Fixture", None, factory)
    ids: dict[str, str] = {}
    for label in labels:
        result = apply_command(page, FIELDS, "addItem", {"text": label}, factory)
        page, ids[label] = result.page, result.created_id
    return page, ids


def _item_ids(page):
    return [item["id"] for item in page.sections["items"]["items"]]


def test_relocate_moves_a_single_item_without_dropping_or_duplicating():
    factory = make_counter()
    page, s = _fields_with_items(factory, "A", "B", "C")
    # Move C between A and B. After C is removed the list is [A, B]; slot 1's predecessor is A.
    moved = apply_command(page, FIELDS, "reorderItem",
                          {"itemId": s["C"], "toIndex": 1, "precedingId": s["A"]}, factory)
    assert _item_ids(moved.page) == [s["A"], s["C"], s["B"]]


def test_relocate_front_insert_names_no_predecessor():
    factory = make_counter()
    page, s = _fields_with_items(factory, "A", "B", "C")
    front = apply_command(page, FIELDS, "reorderItem",
                          {"itemId": s["C"], "toIndex": 0, "precedingId": None}, factory)
    assert _item_ids(front.page) == [s["C"], s["A"], s["B"]]
    # toIndex 0 with a non-null predecessor is a contradiction -> rejected.
    with pytest.raises(ConflictError):
        _ = apply_command(page, FIELDS, "reorderItem",
                      {"itemId": s["C"], "toIndex": 0, "precedingId": s["A"]}, factory)


def test_relocate_rejects_a_stale_predecessor():
    factory = make_counter()
    page, s = _fields_with_items(factory, "A", "B", "C")
    # After C is removed the slot-1 predecessor is A, not B -> the caller's read was stale.
    with pytest.raises(ConflictError):
        _ = apply_command(page, FIELDS, "reorderItem",
                      {"itemId": s["C"], "toIndex": 1, "precedingId": s["B"]}, factory)


def test_relocate_rejects_unknown_id_and_out_of_range_index():
    factory = make_counter()
    page, s = _fields_with_items(factory, "A", "B", "C")
    with pytest.raises(NotFoundError):
        _ = apply_command(page, FIELDS, "reorderItem",
                      {"itemId": "nope", "toIndex": 0, "precedingId": None}, factory)
    # After A is removed the list has length 2; toIndex 5 is outside [0, 2].
    with pytest.raises(ValidationError):
        _ = apply_command(page, FIELDS, "reorderItem",
                      {"itemId": s["A"], "toIndex": 5, "precedingId": s["C"]}, factory)


def test_relocate_to_current_position_is_a_noop():
    factory = make_counter()
    page, s = _fields_with_items(factory, "A", "B", "C")
    same = apply_command(page, FIELDS, "reorderItem",
                         {"itemId": s["B"], "toIndex": 1, "precedingId": s["A"]}, factory)
    assert _item_ids(same.page) == [s["A"], s["B"], s["C"]]


def test_a_multi_move_reorder_is_a_sequence_of_relocates():
    # Mirrors a mutatePageBatch: each relocate is decided against the previous command's result.
    factory = make_counter()
    page, s = _fields_with_items(factory, "A", "B", "C")
    step1 = apply_command(page, FIELDS, "reorderItem",
                          {"itemId": s["C"], "toIndex": 0, "precedingId": None}, factory)  # [C, A, B]
    step2 = apply_command(step1.page, FIELDS, "reorderItem",
                          {"itemId": s["A"], "toIndex": 2, "precedingId": s["B"]}, factory)  # [C, B, A]
    assert _item_ids(step2.page) == [s["C"], s["B"], s["A"]]


def test_positioned_add_item_is_guarded_and_append_is_unchanged():
    factory = make_counter()
    page, s = _fields_with_items(factory, "A", "B")
    inserted = apply_command(page, FIELDS, "addItem",
                             {"text": "X", "index": 1, "precedingId": s["A"]}, factory)
    assert _item_ids(inserted.page)[:2] == [s["A"], inserted.created_id]
    # A stale predecessor for the same slot is rejected.
    with pytest.raises(ConflictError):
        _ = apply_command(inserted.page, FIELDS, "addItem",
                      {"text": "Y", "index": 1, "precedingId": s["B"]}, factory)
    # No index -> plain append (unchanged behaviour).
    appended = apply_command(page, FIELDS, "addItem", {"text": "Z"}, factory)
    assert appended.page.sections["items"]["items"][-1]["text"] == "Z"
    # precedingId without an index is meaningless -> rejected.
    with pytest.raises(ValidationError):
        _ = apply_command(page, FIELDS, "addItem", {"text": "Q", "precedingId": s["A"]}, factory)


def test_resolve_anchored_slot_is_the_shared_guard():
    # The primitive that backs block/element reorder AND page reorder (store.reorder_page).
    ids = ["a", "b", "c"]
    assert resolve_anchored_slot(ids, 2, "b", "ctx") == 2       # slot right after b
    assert resolve_anchored_slot(ids, 0, None, "ctx") == 0      # front names no predecessor
    assert resolve_anchored_slot(ids, 3, "c", "ctx") == 3       # end
    with pytest.raises(ConflictError):
        resolve_anchored_slot(ids, 2, "a", "ctx")              # stale predecessor (b, not a)
    with pytest.raises(ConflictError):
        resolve_anchored_slot(ids, 0, "a", "ctx")              # front must not name a predecessor
    with pytest.raises(ValidationError):
        resolve_anchored_slot(ids, 5, "c", "ctx")              # index out of range


def test_resolve_anchored_slot_skips_batch_created_ids():
    # Inside a batch, ids created by earlier commands are opaque: the guard walks LEFT past them to
    # match precedingId against the first committed id, so a chained insert/reorder can anchor on a
    # committed id that sits in front of the batch's own not-yet-committed run.
    ctx = BatchContext(frozenset({"n1"}))
    assert resolve_anchored_slot(["id0", "n1"], 2, "id0", "ctx", ctx) == 2
    # index stays strict (Q12): the skip never changes the returned slot or the range check.
    with pytest.raises(ValidationError):
        resolve_anchored_slot(["id0", "n1"], 5, "id0", "ctx", ctx)
    # A wrong committed predecessor is still rejected: the walk stops at the first committed id.
    with pytest.raises(ConflictError):
        resolve_anchored_slot(["id0", "C", "n1"], 3, "id0", "ctx", ctx)
    # A batch-created run at the FRONT resolves to None (no predecessor); precedingId=None accepted.
    assert resolve_anchored_slot(["n1"], 1, None, "ctx", ctx) == 1
    # No batch context (the default) is byte-for-byte the strict guard: the stale predecessor rejected.
    with pytest.raises(ConflictError):
        resolve_anchored_slot(["id0", "n1"], 2, "id0", "ctx")


# ============================================================================
# test-flow - transitions, the compound `close`, and legality
# ============================================================================
def new_flow(factory=None):
    return create_page(FLOW, "A change", None, factory or make_counter())


def test_transition_changes_status():
    result = apply_command(new_flow(), FLOW, "open", {}, make_counter())
    assert result.page.status == "open"


def test_illegal_transition_raises_with_legal_set():
    with pytest.raises(IllegalCommandError) as exc:
        _ = apply_command(new_flow(), FLOW, "reopen", {}, make_counter())   # reopen is only legal from closed
    assert "open" in exc.value.legal
    assert "reopen" not in exc.value.legal


def test_compound_close_records_commit_and_transitions():
    factory = make_counter()
    page = new_flow(factory)
    opened = apply_command(page, FLOW, "open", {}, factory)
    assert opened.page.status == "open"
    closed = apply_command(opened.page, FLOW, "close",
                           {"sha": "abc123", "message": "fix null deref"}, factory)
    assert closed.page.status == "closed"
    commits = closed.page.sections["resolution"]["commits"]
    assert len(commits) == 1
    assert commits[0]["sha"] == "abc123"
    assert commits[0]["message"] == "fix null deref"
    assert commits[0]["url"] is None
    assert closed.created_id == commits[0]["id"]


def test_compound_close_illegal_from_draft():
    factory = make_counter()
    page = new_flow(factory)
    with pytest.raises(IllegalCommandError):
        _ = apply_command(page, FLOW, "close", {"sha": "a", "message": "m"}, factory)  # not open yet


def test_legal_commands_content_always_transitions_gated():
    legal = legal_commands(new_flow(), FLOW)
    assert legal["setSummary"] is True        # content
    assert legal["open"] is True              # legal from draft
    assert legal["close"] is False            # not legal from draft


def test_terminal_state_locks_authoring_but_keeps_transitions():
    # `closed` is declared terminal on test-flow yet retains its `reopen` transition, so it is the
    # "transitions still allowed if any" case: authoring locked, the transition still legal.
    page = new_flow()
    page.status = "closed"
    legal = legal_commands(page, FLOW)
    assert legal["setSummary"] is False       # authoring: locked by the terminal rule
    assert legal["addLink"] is False          # authoring: locked (would be legal in any non-terminal state)
    assert legal["reopen"] is True            # transition: still legal from a terminal state
    # The write path enforces the same lock: authoring is rejected, the transition applies.
    with pytest.raises(IllegalCommandError):
        _ = apply_command(page, FLOW, "setSummary", {"text": "nope"}, make_counter())
    assert apply_command(page, FLOW, "reopen", {}, make_counter()).page.status == "open"


def test_legal_in_naming_a_terminal_state_overrides_the_authoring_lock():
    """test-flow's `reorderCommit` names the terminal `closed` in its legal_in, so it stays legal -
    and applies - there, while the legal_in=None authoring commands around it stay locked."""
    factory = make_counter()
    opened = apply_command(new_flow(factory), FLOW, "open", {}, factory).page
    first = apply_command(opened, FLOW, "close", {"sha": "aaa", "message": "first"}, factory)
    page = first.page
    assert page.status == "closed"                    # terminal
    # A second commit to reorder against, recorded via reopen -> close (the only writer of the list).
    page = apply_command(page, FLOW, "reopen", {}, factory).page
    second = apply_command(page, FLOW, "close", {"sha": "bbb", "message": "second"}, factory)
    page = second.page
    assert page.status == "closed"

    legal = legal_commands(page, FLOW)
    assert legal["reorderCommit"] is True             # opted in: names `closed` in legal_in
    assert legal["setSummary"] is False               # legal_in=None: still locked by the terminal rule
    assert legal["setTitle"] is False                 # legal_in=None: still locked
    assert legal["addLink"] is False                  # legal_in=None: still locked

    # The write path agrees - the opted-in command applies in the terminal state.
    moved = apply_command(page, FLOW, "reorderCommit",
                          {"commitId": second.created_id, "toIndex": 0, "precedingId": None}, factory)
    assert [commit["sha"] for commit in moved.page.sections["resolution"]["commits"]] == ["bbb", "aaa"]


def test_legal_in_override_is_per_state_not_merely_declared():
    """Carrying a legal_in is not enough - it must name the terminal state. `setNote` is legal_in
    the non-terminal `open` only and stays locked in `done`; `setLog` names `done` and survives."""
    page_type = PageType(
        tag="xtest-terminal-optin", name="Opt-in", description="ad-hoc",
        sections=(SectionSpec("note", "Note", (_prose("body", description="a working note"),)),
                  SectionSpec("log", "Log", (_prose("body", description="a durable log entry"),))),
        commands=(set_prose_cmd("note", legal_in=("open",)),        # never names the terminal state
                  set_prose_cmd("log", legal_in=("open", "done")),  # names it - opts in
                  transition_cmd("finish", "open -> done")),
        fsm=FSMSpec(name="XOptIn", initial="open", states=("open", "done"), terminal_states=("done",)),
    )
    page = create_page(page_type, "A page", None, make_counter())
    legal_open = legal_commands(page, page_type)
    assert legal_open["setNote"] is True and legal_open["setLog"] is True   # both fine before finishing

    done = apply_command(page, page_type, "finish", {}, make_counter()).page
    assert done.status == "done"
    legal_done = legal_commands(done, page_type)
    assert legal_done["setNote"] is False      # declares legal_in, but not for `done` - stays locked
    assert legal_done["setLog"] is True        # names `done` - opted in
    with pytest.raises(IllegalCommandError):
        _ = apply_command(done, page_type, "setNote", {"text": "nope"}, make_counter())
    written = apply_command(done, page_type, "setLog", {"text": "landed as abc123"}, make_counter())
    assert written.page.sections["log"]["body"] == "landed as abc123"


# ============================================================================
# test-lifecycle - lifecycle FSM + required-field preconditions + questions
# ============================================================================
LIFE = get_page_type("test-lifecycle")


def new_life(factory=None):
    return create_page(LIFE, "Dark mode", None, factory or make_counter())


def test_create_lifecycle_initial_state():
    page = new_life()
    assert page.type == "test-lifecycle"
    assert page.status == "draft"                 # FSM initial
    assert page.sections["summary"]["body"] == ""
    assert page.sections["parts"]["items"] == []
    assert page.sections["questions"]["items"] == []


def test_begin_planning_blocked_until_summary_set():
    factory = make_counter()
    page = new_life(factory)
    # Blocked while the summary is empty; the error names the missing field.
    with pytest.raises(IllegalCommandError) as exc:
        _ = apply_command(page, LIFE, "beginPlanning", {}, factory)
    assert "summary.body" in str(exc.value)
    assert "beginPlanning" not in exc.value.legal   # not currently legal
    assert "setSummary" in exc.value.legal          # but you can author toward it

    # Whitespace-only prose does not count as populated.
    blank = apply_command(page, LIFE, "setSummary", {"text": "   "}, factory).page
    assert legal_commands(blank, LIFE)["beginPlanning"] is False

    # A real summary unlocks the transition.
    ready = apply_command(page, LIFE, "setSummary", {"text": "A dark theme."}, factory).page
    assert legal_commands(ready, LIFE)["beginPlanning"] is True
    assert apply_command(ready, LIFE, "beginPlanning", {}, factory).page.status == "planning"


def test_begin_implementation_requires_a_part():
    factory = make_counter()
    page = apply_command(new_life(factory), LIFE, "setSummary", {"text": "x"}, factory).page
    page = apply_command(page, LIFE, "beginPlanning", {}, factory).page
    assert page.status == "planning"
    # No parts identified yet -> blocked, and the error says which field.
    with pytest.raises(IllegalCommandError) as exc:
        _ = apply_command(page, LIFE, "beginImplementation", {}, factory)
    assert "parts.items" in str(exc.value)
    # Add one -> unlocked.
    page = apply_command(page, LIFE, "addPart", {"name": "Renderer"}, factory).page
    assert apply_command(page, LIFE, "beginImplementation", {}, factory).page.status == "building"


def test_full_lifecycle():
    factory = make_counter()
    page = new_life(factory)
    page = apply_command(page, LIFE, "setSummary", {"text": "A dark theme."}, factory).page
    page = apply_command(page, LIFE, "beginPlanning", {}, factory).page
    page = apply_command(page, LIFE, "addPart", {"name": "Renderer"}, factory).page
    page = apply_command(page, LIFE, "beginImplementation", {}, factory).page
    page = apply_command(page, LIFE, "submitForReview", {}, factory).page
    assert page.status == "review"
    # A reviewer bounces it back, then it returns and ships.
    page = apply_command(page, LIFE, "requestChanges", {}, factory).page
    assert page.status == "building"
    page = apply_command(page, LIFE, "submitForReview", {}, factory).page
    # The ship child-state guard is a STORE concern; the pure core does not evaluate it.
    page = apply_command(page, LIFE, "ship", {}, factory).page
    assert page.status == "done"


def test_abandon_reaches_abandoned_from_multiple_states():
    factory = make_counter()
    # From draft.
    assert apply_command(new_life(factory), LIFE, "abandon", {}, factory).page.status == "abandoned"
    # From building (an event with several source states, OR-combined in the FSM).
    page = apply_command(new_life(factory), LIFE, "setSummary", {"text": "x"}, factory).page
    page = apply_command(page, LIFE, "beginPlanning", {}, factory).page
    page = apply_command(page, LIFE, "addPart", {"name": "R"}, factory).page
    page = apply_command(page, LIFE, "beginImplementation", {}, factory).page
    assert apply_command(page, LIFE, "abandon", {}, factory).page.status == "abandoned"


def test_done_is_terminal():
    done = create_page(LIFE, "y", None, make_counter())
    done.status = "done"
    legal = legal_commands(done, LIFE)
    # `done` is a declared terminal state AND a dead-end (no outgoing transition): transitions are
    # unavailable by topology, authoring is locked by the terminal rule - so nothing at all is legal.
    for name in ("beginPlanning", "beginImplementation", "submitForReview",
                 "requestChanges", "reopenPlanning", "ship", "abandon"):
        assert legal[name] is False                       # no transition leaves `done`
    for name in ("setSummary", "addPart", "askQuestion", "escalateQuestion", "addLink"):
        assert legal[name] is False                       # authoring locked in a terminal state
    assert not any(legal.values())                        # fully locked


def test_answer_question_sets_field_in_place():
    factory = make_counter()
    asked = apply_command(new_life(factory), LIFE, "askQuestion",
                          {"text": "Which contrast ratio?"}, factory)
    question_id = asked.created_id
    question = asked.page.sections["questions"]["items"][0]
    assert question["id"] == question_id
    assert "answer" not in question                 # unanswered on creation

    answered = apply_command(asked.page, LIFE, "answerQuestion",
                             {"questionId": question_id, "answer": "WCAG AA"}, factory)
    items = answered.page.sections["questions"]["items"]
    assert len(items) == 1                           # updated in place, not appended
    assert items[0]["id"] == question_id             # same element id
    assert items[0]["answer"] == "WCAG AA"
    assert answered.created_id is None               # a set, not an add


def test_answer_unknown_question_raises():
    factory = make_counter()
    with pytest.raises(NotFoundError):
        _ = apply_command(new_life(factory), LIFE, "answerQuestion",
                      {"questionId": "nope", "answer": "x"}, factory)


def test_answer_question_fires_element_fsm():
    factory = make_counter()
    asked = apply_command(new_life(factory), LIFE, "askQuestion", {"text": "Which ratio?"}, factory)
    qid = asked.created_id
    assert asked.page.sections["questions"]["items"][0]["status"] == "open"   # element FSM initial
    answered = apply_command(asked.page, LIFE, "answerQuestion", {"questionId": qid, "answer": "AA"}, factory)
    q = answered.page.sections["questions"]["items"][0]
    assert q["status"] == "answered" and q["answer"] == "AA"
    # answering again is illegal - the element FSM has no answer edge out of `answered`.
    with pytest.raises(IllegalCommandError):
        _ = apply_command(answered.page, LIFE, "answerQuestion", {"questionId": qid, "answer": "again"}, factory)


def test_escalate_question_sets_needs_human():
    factory = make_counter()
    asked = apply_command(new_life(factory), LIFE, "askQuestion", {"text": "?"}, factory)
    qid = asked.created_id
    escalated = apply_command(asked.page, LIFE, "escalateQuestion", {"questionId": qid}, factory)
    assert escalated.page.sections["questions"]["items"][0]["needsHuman"] is True


# ============================================================================
# test-child - step/check element-FSMs, the "ready" content lock, and a cross-page ref
# ============================================================================
CHILD = get_page_type("test-child")


def new_child(factory):
    return create_page(CHILD, "Child", "test-lifecycle:x", factory)


def test_step_lifecycle_and_ready_lock():
    factory = make_counter()
    child = new_child(factory)
    added = apply_command(child, CHILD, "addStep", {"text": "Do it"}, factory)
    step_id = added.created_id
    assert added.page.sections["steps"]["items"][0]["status"] == "todo"
    done = apply_command(added.page, CHILD, "markStepDone", {"stepId": step_id}, factory)
    assert done.page.sections["steps"]["items"][0]["status"] == "done"
    # In `ready`, structural edits are locked but the element-status marks stay legal
    # - progress is still recordable against a finalized plan.
    ready = apply_command(done.page, CHILD, "markReady", {}, factory)
    legal = legal_commands(ready.page, CHILD)
    assert {name for name, ok in legal.items() if ok} == {
        "reopen", "markStepDone", "markStepSkipped", "markStepTodo",
        "markCheckPassed", "markCheckFailed", "markCheckSkipped", "addLink", "setTitle"}
    with pytest.raises(IllegalCommandError):
        _ = apply_command(ready.page, CHILD, "addStep", {"text": "late"}, factory)


def test_check_pass_and_fail_are_terminal():
    factory = make_counter()
    child = new_child(factory)
    c = apply_command(child, CHILD, "addCheck", {"text": "renders"}, factory)
    check_id = c.created_id
    assert c.page.sections["checks"]["items"][0]["status"] == "pending"
    passed = apply_command(c.page, CHILD, "markCheckPassed", {"checkId": check_id}, factory)
    assert passed.page.sections["checks"]["items"][0]["status"] == "passed"
    # passed is terminal - you cannot then fail it.
    with pytest.raises(IllegalCommandError):
        _ = apply_command(passed.page, CHILD, "markCheckFailed", {"checkId": check_id}, factory)


def test_skip_is_legal_only_from_the_initial_state():
    factory = make_counter()
    child = new_child(factory)
    s = apply_command(child, CHILD, "addStep", {"text": "build"}, factory)
    step_id = s.created_id
    c = apply_command(s.page, CHILD, "addCheck", {"text": "renders"}, factory)
    check_id = c.created_id
    done = apply_command(c.page, CHILD, "markStepDone", {"stepId": step_id}, factory)
    # A done step has no skip edge - skip fires only from todo.
    with pytest.raises(IllegalCommandError):
        _ = apply_command(done.page, CHILD, "markStepSkipped", {"stepId": step_id}, factory)
    passed = apply_command(done.page, CHILD, "markCheckPassed", {"checkId": check_id}, factory)
    # skip fires only from pending, so a passed check cannot be skipped either.
    with pytest.raises(IllegalCommandError):
        _ = apply_command(passed.page, CHILD, "markCheckSkipped", {"checkId": check_id}, factory)


def test_add_decision_carries_ref():
    """The pure core records the decision block + its questionId; ref integrity is a store check."""
    factory = make_counter()
    child = new_child(factory)
    d = apply_command(child, CHILD, "addDecisions", {"blocks": [{"kind": "decision", "questionId": "q1", "text": "Use WCAG AA"}]}, factory)
    block = d.page.sections["decisions"]["body"][0]
    assert block["kind"] == "decision" and block["questionId"] == "q1" and block["text"] == "Use WCAG AA"


# --- field-setter classification (is_field_setter) ---------------------------
def test_is_field_setter_classifies_by_kind():
    """The kind-based classifier: SET_SCALAR / SET_PROSE / ADD_ELEMENT and a page-level ADD_BLOCK
    are field setters (including the element-FSM add askQuestion); everything else - remove/reorder,
    flag setters, element transitions, page transitions, the universal addLink/setTitle, and an
    element-scoped add - is not."""
    life = get_page_type("test-lifecycle")
    blocks = get_page_type("test-blocks")
    assert is_field_setter(FIELDS.command("setLabel"))       # SET_SCALAR
    assert is_field_setter(FIELDS.command("setBody"))        # SET_PROSE
    assert is_field_setter(FIELDS.command("addItem"))        # ADD_ELEMENT
    assert is_field_setter(life.command("askQuestion"))      # ADD_ELEMENT (element-FSM list add)
    assert not is_field_setter(FIELDS.command("removeItem"))     # REMOVE_ELEMENT
    assert not is_field_setter(FIELDS.command("reorderItem"))    # REORDER_ELEMENT
    assert not is_field_setter(FIELDS.command("flagItem"))       # SET_ELEMENT_FIELD
    assert not is_field_setter(FIELDS.command("addLink"))        # ADD_LINK
    assert not is_field_setter(FIELDS.command("setTitle"))       # SET_TITLE
    assert not is_field_setter(life.command("answerQuestion"))   # ELEMENT_TRANSITION
    assert not is_field_setter(life.command("beginPlanning"))    # TRANSITION
    # A page-level blocks add is now an ordinary field setter: one add per field, so it names
    # exactly one command in `do`.
    assert is_field_setter(blocks.command("addBody"))             # ADD_BLOCK, page-level
    assert not is_field_setter(blocks.command("removeBlock"))     # REMOVE_BLOCK
    element_blocks = get_page_type("test-element-blocks")
    # An element-scoped add fills an element that must exist first, so it is not a stage's work.
    assert not is_field_setter(element_blocks.command("addItemDetail"))


# --- stage-scoped field-setter `do` edges (field_setter_edges) ---------------
# An ad-hoc, unregistered draft->sealed type: markSealed requires overview (prose) AND design (blocks),
# both authored in `draft`. Built directly (not via the store/registry) to exercise the pure edge logic -
# blocks grouping, legal_in gating, and stage-scoping - which no production-mirroring fixture covers.
_DESIGN_BODY = _blocks("body", block_kinds=standard_blocks(), description="the design instruction")


def _field_setter_page_type() -> PageType:
    return PageType(
        tag="xtest-field-setter", name="Field setter fixture", description="ad-hoc field-setter-edge fixture",
        sections=(
            SectionSpec("overview", "Overview", (_prose("body", description="the overview instruction"),)),
            SectionSpec("design", "Design", (_DESIGN_BODY,)),
        ),
        commands=(
            set_prose_cmd("overview", legal_in=("draft",)),
            *blocks_cmds("design", legal_in=("draft",)),
            transition_cmd("markSealed", "draft -> sealed",
                           requires=(("overview", "body"), ("design", "body"))),
            add_link_cmd(), set_title_cmd(),
        ),
        fsm=FSMSpec(name="XTestSeal", initial="draft", states=("draft", "sealed")),
    )


def _field_setter_page(seal: PageType, status: str) -> Page:
    return Page(id="xtest-field-setter:1", type="xtest-field-setter", title="X", status=status,
                sections=initial_sections(seal))


def test_field_setter_edges_group_blocks_and_carry_instructions():
    """Every field surfaces as one `do` entry naming one command - a blocks field's single add
    (never its remove/reorder) or a prose/scalar setter. Each carries the field's FieldSpec
    instruction inline."""
    seal = _field_setter_page_type()
    edges = field_setter_edges(_field_setter_page(seal, "draft"), seal)
    by_field = {(e["section"], e["field"]): e for e in edges}

    overview = by_field[("overview", "body")]
    assert overview["kind"] == "field" and overview["command"] == "setOverview"
    assert overview["instruction"] == "the overview instruction"

    design = by_field[("design", "body")]
    assert design["kind"] == "field" and design["instruction"] == "the design instruction"
    # A blocks field is one edge naming one command - the whole field is authored through it.
    assert design["command"] == "addDesign"
    # The set, remove and reorder are never surfaced here; describeMutations reports them.
    assert not design["command"].startswith(("set", "remove", "reorder"))


def test_field_setter_edges_use_the_instruction_key_not_description():
    """A `next` field edge carries the authoring instruction under `instruction`. `description` is the
    command's short label and never appears on an edge - the two names must not be confused."""
    seal = _field_setter_page_type()
    edges = field_setter_edges(_field_setter_page(seal, "draft"), seal)
    assert edges
    for edge in edges:
        assert edge["instruction"]
        assert "description" not in edge
    # the setter's own description is the short label, and never leaks onto the edge
    assert seal.command("setOverview").description == "set the overview"


def test_field_setter_edges_are_stage_and_legal_in_scoped():
    """A setter enters `do` only where its field is a stage requirement AND it is legal now: setOverview
    (legal_in=draft) shows in `draft` (markSealed requires overview.body there) and vanishes in `sealed`
    (no legal transition requires it, and it is not legal anyway)."""
    seal = _field_setter_page_type()
    draft_cmds = {e["command"] for e in field_setter_edges(_field_setter_page(seal, "draft"), seal)}
    assert "setOverview" in draft_cmds
    assert field_setter_edges(_field_setter_page(seal, "sealed"), seal) == []


def test_field_setter_edges_drop_legal_in_none_noise():
    """Stage-scoping removes the 'always-legal setter shows everywhere' noise (Q5): setSummary
    (legal_in=None) shows in `draft` (beginPlanning requires summary.body) but NOT in `planning`,
    where no legal transition requires summary.body - even though it stays legal."""
    life = get_page_type("test-lifecycle")
    draft = create_page(life, "F", None, make_counter())
    assert "setSummary" in {e["command"] for e in field_setter_edges(draft, life)}
    planning = create_page(life, "F", None, make_counter())
    planning.status = "planning"
    assert "setSummary" not in {e["command"] for e in field_setter_edges(planning, life)}


def test_field_setter_edges_drop_blocked_events():
    """`blocked_events` takes an event out of the topology, so content required ONLY by that event
    stops surfacing - content whose transition the outside world is holding is not this page's work
    yet. The store passes a page's parent-state-guard failures here, which is what keeps a pinned
    plan child silent until its parent unlocks the stage."""
    child = new_child(make_counter())                     # test-child in `draft`; markReady needs steps
    assert "addStep" in {e["command"] for e in field_setter_edges(child, CHILD)}
    assert field_setter_edges(child, CHILD, {"markReady"}) == []
    # An unrelated blocked event leaves the topology (and so the edges) untouched.
    assert "addStep" in {e["command"] for e in field_setter_edges(child, CHILD, {"reopen"})}


# --- transition_guidance -----------------------------------------------------
def test_transition_guidance_returns_the_entered_states_text():
    guidance = transition_guidance(FLOW, [{"command": "open"}], "open")
    assert guidance == FLOW.fsm.guidance_for("open")
    assert guidance                                  # non-empty: the fixture declares text here


def test_transition_guidance_is_none_for_a_content_only_batch():
    # No transition, so no new stage was entered.
    assert transition_guidance(FLOW, [{"command": "setSummary", "args": {"text": "x"}}], "draft") is None


def test_transition_guidance_is_none_when_the_state_declares_none():
    # A real transition, but `draft` declares no guidance.
    assert transition_guidance(FLOW, [{"command": "reopen"}], "draft") is None


# --- block-bearing element fields --------------------------------------------
ELEMENT_BLOCKS = get_page_type("test-element-blocks")


def new_item(factory):
    """A test-element-blocks page holding one item, and that item's id."""
    page = create_page(ELEMENT_BLOCKS, "A fixture", None, factory)
    added = apply_command(page, ELEMENT_BLOCKS, "addItem", {"text": "one"}, factory)
    assert added.created_id is not None
    return added.page, added.created_id


def items_of(page):
    return page.sections["items"]["items"]


def _unified_blocks_page_type() -> PageType:
    """An ad-hoc type whose body field carries the unified four-command block surface."""
    body = _blocks("body", block_kinds=standard_blocks(), description="a rich-text blocks body")
    return PageType(
        tag="xtest-unified-blocks", name="Unified blocks fixture",
        description="ad-hoc fixture: one blocks field, one add and one set",
        sections=(SectionSpec("body", "Body", (body,)),),
        commands=(*blocks_cmds("body"), add_link_cmd(), set_title_cmd()),
        fsm=FSMSpec(name="XTestUnifiedBlocks", initial="active", states=("active",)),
    )


UNIFIED = _unified_blocks_page_type()


def _unified_page(factory):
    return create_page(UNIFIED, "A page", None, factory)


def _body(page):
    return page.sections["body"]["body"]


def test_add_block_creates_a_run():
    """One add creates several blocks, in argument order, each with its own id."""
    factory = make_counter()
    page = _unified_page(factory)
    added = apply_command(page, UNIFIED, "addBody", {"blocks": [
        {"kind": "paragraph", "inlines": ["one"]},
        {"kind": "heading", "level": 2, "inlines": ["two"]},
        {"kind": "code", "language": "py", "source": "x = 1"},
    ]}, factory)
    blocks = _body(added.page)
    assert [b["kind"] for b in blocks] == ["paragraph", "heading", "code"]
    assert len({b["id"] for b in blocks}) == 3
    # The first id is reported positionally; every id created is reported for the batch guard.
    assert added.created_id == blocks[0]["id"]
    assert added.created_ids == [b["id"] for b in blocks]


def test_add_block_stores_an_omitted_optional_as_none():
    factory = make_counter()
    page = _unified_page(factory)
    added = apply_command(page, UNIFIED, "addBody", {"blocks": [
        {"kind": "table", "header": [["A"]], "rows": [[["1"]]]},      # `align` omitted
    ]}, factory)
    assert _body(added.page)[0]["align"] is None


def test_add_block_accepts_an_empty_run():
    """Legal, writes nothing, reports no id - so a caller need not special-case an empty list."""
    factory = make_counter()
    page = _unified_page(factory)
    added = apply_command(page, UNIFIED, "addBody", {"blocks": []}, factory)
    assert _body(added.page) == []
    assert added.created_id is None and added.created_ids == []


def test_add_block_run_inserts_at_the_anchored_slot():
    """A positioned run lands contiguously, in order, from one resolved anchor."""
    factory = make_counter()
    page = _unified_page(factory)
    page = apply_command(page, UNIFIED, "addBody", {"blocks": [
        {"kind": "paragraph", "inlines": ["A"]},
        {"kind": "paragraph", "inlines": ["B"]},
    ]}, factory).page
    first, second = (b["id"] for b in _body(page))
    moved = apply_command(page, UNIFIED, "addBody", {
        "blocks": [{"kind": "paragraph", "inlines": ["X"]},
                   {"kind": "paragraph", "inlines": ["Y"]}],
        "index": 1, "precedingId": first,
    }, factory)
    assert [b["inlines"][0] for b in _body(moved.page)] == ["A", "X", "Y", "B"]
    # A stale anchor is refused and nothing is written.
    with pytest.raises(ConflictError):
        _ = apply_command(page, UNIFIED, "addBody", {
            "blocks": [{"kind": "paragraph", "inlines": ["X"]}],
            "index": 1, "precedingId": second,
        }, factory)
    # precedingId without an index is meaningless even for an empty run.
    with pytest.raises(ValidationError, match="requires an index"):
        _ = apply_command(page, UNIFIED, "addBody",
                          {"blocks": [], "precedingId": first}, factory)


def test_a_page_level_add_rejects_a_kind_the_field_does_not_declare():
    factory = make_counter()
    page = _unified_page(factory)
    with pytest.raises(ValidationError, match="not accepted here"):
        _ = apply_command(page, UNIFIED, "addBody",
                          {"blocks": [{"kind": "nope", "text": "x"}]}, factory)


def test_a_block_is_replaced_by_removing_it_and_adding_at_its_slot():
    """There is no in-place edit. Replacing a block is remove plus a positioned add, which
    keeps its slot and its neighbours - and gives it a NEW id, the whole cost of dropping the
    set."""
    factory = make_counter()
    page = _unified_page(factory)
    page = apply_command(page, UNIFIED, "addBody", {"blocks": [
        {"kind": "paragraph", "inlines": ["first"]},
        {"kind": "paragraph", "inlines": ["second"]},
        {"kind": "paragraph", "inlines": ["third"]},
    ]}, factory).page
    first, second, third = (block["id"] for block in _body(page))
    page = apply_command(page, UNIFIED, "removeBlock", {"blockId": second}, factory).page
    replaced = apply_command(page, UNIFIED, "addBody", {
        "blocks": [{"kind": "code", "language": "sh", "source": "ls"}],
        "index": 1, "precedingId": first,
    }, factory)
    blocks = _body(replaced.page)
    assert [block["id"] for block in blocks] == [first, replaced.created_id, third]
    assert replaced.created_id != second        # a replacement is a new block, not an edit
    assert blocks[1] == {"id": replaced.created_id, "kind": "code",
                         "language": "sh", "source": "ls"}


def test_a_bad_table_inside_a_page_level_add_still_raises():
    factory = make_counter()
    page = _unified_page(factory)
    with pytest.raises(ValidationError, match="header"):
        _ = apply_command(page, UNIFIED, "addBody", {"blocks": [
            {"kind": "table", "header": [["A"], ["B"]], "rows": [[["only-one"]]]}]}, factory)


def test_a_page_level_add_enforces_the_inline_grammar():
    factory = make_counter()
    page = _unified_page(factory)
    with pytest.raises(ValidationError, match="Markdown syntax"):
        _ = apply_command(page, UNIFIED, "addBody", {"blocks": [
            {"kind": "paragraph", "inlines": ["a **bold** word"]}]}, factory)


def _table_blocks_page_type() -> PageType:
    """An ad-hoc type whose element field accepts a table, so a table can be reached through an
    array argument. The shared fixtures deliberately declare narrower vocabularies."""
    items = _list("items", element_fields=("text", "detail"),
                  element_blocks=(ElementBlocksSpec("detail", (_table_block(),)),))
    return PageType(
        tag="xtest-table-blocks", name="Table blocks fixture",
        description="ad-hoc fixture: an element block field accepting a table",
        sections=(SectionSpec("items", "Items", (items,)),),
        commands=(*list_cmds("items", add_args=(_text(),), element_blocks=("detail",)),
                  add_link_cmd(), set_title_cmd()),
        fsm=FSMSpec(name="XTestTableBlocks", initial="active", states=("active",)),
    )


def test_a_bad_table_inside_a_block_array_still_raises():
    """The width rule outlives the hook that used to fire it.

    `_validate_args` used to carry a cross-arg table check keyed on the command's declared kind,
    which went with the per-kind commands. The rule survives only because validate_block runs
    validate_table per entry - this is the case that proves it.
    """
    page_type = _table_blocks_page_type()
    factory = make_counter()
    page = create_page(page_type, "A fixture", None, factory)
    with pytest.raises(ValidationError, match="header"):
        _ = apply_command(page, page_type, "addItem", {
            "text": "one",
            "detail": [{"kind": "table", "header": [["A"], ["B"]], "rows": [[["only-one"]]]}],
        }, factory)
    # The same table, correctly shaped, goes in.
    added = apply_command(page, page_type, "addItem", {
        "text": "one",
        "detail": [{"kind": "table", "header": [["A"], ["B"]], "rows": [[["1"], ["2"]]]}],
    }, factory)
    assert added.page.sections["items"]["items"][0]["detail"][0]["kind"] == "table"


def test_an_add_creates_an_element_with_its_blocks():
    factory = make_counter()
    page = create_page(ELEMENT_BLOCKS, "A fixture", None, factory)
    added = apply_command(page, ELEMENT_BLOCKS, "addItem", {
        "text": "one",
        "detail": [{"kind": "paragraph", "inlines": ["prose"]},
                   {"kind": "code", "language": "python", "source": "x = 1"}],
        "snippet": [{"kind": "code", "language": "bash", "source": "ls"}],
    }, factory)
    element = items_of(added.page)[0]
    assert added.created_id == element["id"]          # the ELEMENT's id, not a block's
    assert element["status"] == "todo"
    assert [block["kind"] for block in element["detail"]] == ["paragraph", "code"]
    assert element["detail"][1]["source"] == "x = 1"
    assert [block["kind"] for block in element["snippet"]] == ["code"]
    # Every block carries its own created id, distinct from the element's and from each other's.
    ids = [block["id"] for block in element["detail"] + element["snippet"]]
    assert len(set(ids)) == 3 and element["id"] not in ids


def test_a_block_created_with_its_element_matches_one_added_after():
    # The two paths must not drift into two dialects: same keys, same values, ids aside.
    made_with = apply_command(
        create_page(ELEMENT_BLOCKS, "A", None, make_counter()), ELEMENT_BLOCKS, "addItem",
        {"text": "one", "detail": [{"kind": "code", "language": "python", "source": "x = 1"}]},
        make_counter())
    factory = make_counter()
    page, item_id = new_item(factory)
    added_after = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                                {"itemId": item_id, "blocks": [{"kind": "code", "language": "python", "source": "x = 1"}]},
                                factory)
    first = items_of(made_with.page)[0]["detail"][0]
    second = items_of(added_after.page)[0]["detail"][0]
    assert {k: v for k, v in first.items() if k != "id"} == \
           {k: v for k, v in second.items() if k != "id"}


def test_creating_with_blocks_enforces_the_same_grammar():
    factory = make_counter()
    page = create_page(ELEMENT_BLOCKS, "A fixture", None, factory)

    def add(**args):
        return apply_command(page, ELEMENT_BLOCKS, "addItem", {"text": "one", **args}, factory)

    # The declared arg type is checked first, so a non-array never reaches the block grammar.
    with pytest.raises(ValidationError, match="must be of type 'array'"):
        _ = add(detail="nope")
    with pytest.raises(ValidationError, match="must be an object"):
        _ = add(detail=["nope"])
    with pytest.raises(ValidationError, match="not accepted here"):
        _ = add(snippet=[{"kind": "paragraph", "inlines": []}])     # snippet takes code only
    with pytest.raises(ValidationError, match="unknown keys"):
        _ = add(detail=[{"kind": "code", "language": "py", "source": "x", "nope": 1}])
    with pytest.raises(ValidationError, match="requires 'source'"):
        _ = add(detail=[{"kind": "code", "language": "py"}])
    with pytest.raises(ValidationError, match="Markdown syntax"):
        _ = add(detail=[{"kind": "paragraph", "inlines": ["a **bold** word"]}])


def test_a_new_element_carries_its_declared_block_fields_empty():
    factory = make_counter()
    page, item_id = new_item(factory)
    assert items_of(page) == [
        {"id": item_id, "text": "one", "snippet": [], "detail": [], "status": "todo"}
    ]


def test_add_and_set_a_block_on_an_element():
    factory = make_counter()
    page, item_id = new_item(factory)
    added = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "code", "language": "python", "source": "x = 1"}]}, factory)
    element = items_of(added.page)[0]
    assert element["detail"] == [
        {"id": added.created_id, "kind": "code", "language": "python", "source": "x = 1"}
    ]
    # The block landed on the element, not in a section field of its own.
    assert list(added.page.sections["items"]) == ["items"]
    assert element["snippet"] == []


def test_an_element_scoped_add_is_held_to_the_element_fields_vocabulary():
    """The kind rule one level deeper is the same one: `detail` declares paragraph, code and
    list, so a table is refused there just as it is at the page level."""
    factory = make_counter()
    page, item_id = new_item(factory)
    with pytest.raises(ValidationError, match="not accepted here"):
        _ = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id,
                           "blocks": [{"kind": "table", "header": [["A"]], "rows": [[["1"]]]}]},
                          factory)


def test_remove_and_reorder_blocks_on_an_element():
    factory = make_counter()
    page, item_id = new_item(factory)
    first = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": ["p"]}]}, factory)
    second = apply_command(first.page, ELEMENT_BLOCKS, "addItemDetail",
                           {"itemId": item_id, "blocks": [{"kind": "code", "language": "python", "source": "a"}]}, factory)
    third = apply_command(second.page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "code", "language": "python", "source": "b"}]}, factory)
    moved = apply_command(third.page, ELEMENT_BLOCKS, "reorderItemDetail",
                          {"itemId": item_id, "blockId": third.created_id, "toIndex": 0}, factory)
    assert [block["id"] for block in items_of(moved.page)[0]["detail"]] == [
        third.created_id, first.created_id, second.created_id
    ]
    removed = apply_command(moved.page, ELEMENT_BLOCKS, "removeItemDetail",
                            {"itemId": item_id, "blockId": first.created_id}, factory)
    assert [block["id"] for block in items_of(removed.page)[0]["detail"]] == [
        third.created_id, second.created_id
    ]
    assert len(items_of(removed.page)) == 1          # the element itself is untouched


def test_element_scoped_stale_read_names_the_element():
    factory = make_counter()
    page, item_id = new_item(factory)
    added = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": ["p"]}]}, factory)
    with pytest.raises(ConflictError, match=r"Stale read.*items\.items\[.*\]\.detail"):
        _ = apply_command(added.page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "code", "language": "python", "source": "x"}], "index": 1, "precedingId": "not-the-real-id"}, factory)
    with pytest.raises(ValidationError, match="precedingId requires an index"):
        _ = apply_command(added.page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "code", "language": "python", "source": "x"}], "precedingId": added.created_id}, factory)


def test_unknown_element_and_block_ids_name_different_lists():
    factory = make_counter()
    page, item_id = new_item(factory)
    added = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": ["p"]}]}, factory)
    # The ELEMENT lookup failed - the list named is the list field itself.
    with pytest.raises(NotFoundError, match=r"No element with id 'nope' in items\.items\."):
        _ = apply_command(added.page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": "nope", "blocks": [{"kind": "code", "language": "python", "source": "x"}]}, factory)
    # The BLOCK lookup failed - the list named is that element's block field.
    with pytest.raises(NotFoundError, match=r"No entry with id 'nope' in items\.items\[.*\]\.detail\."):
        _ = apply_command(added.page, ELEMENT_BLOCKS, "removeItemDetail",
                          {"itemId": item_id, "blockId": "nope"}, factory)


def test_an_element_without_the_block_key_accepts_its_first_block():
    # The shape an element stored before the field was declared has: no snippet, no detail.
    factory = make_counter()
    page = create_page(ELEMENT_BLOCKS, "A fixture", None, factory)
    page.sections["items"]["items"] = [{"id": "legacy", "text": "old", "status": "todo"}]
    added = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": "legacy", "blocks": [{"kind": "code", "language": "python", "source": "x = 1"}]}, factory)
    assert items_of(added.page)[0]["detail"] == [
        {"id": added.created_id, "kind": "code", "language": "python", "source": "x = 1"}
    ]


def test_element_scoped_blocks_still_enforce_the_inline_grammar():
    factory = make_counter()
    page, item_id = new_item(factory)
    with pytest.raises(ValidationError, match="unknown keys"):
        _ = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": [{"text": "hi", "nope": 1}]}]}, factory)
    with pytest.raises(ValidationError, match="Markdown syntax"):
        _ = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": ["a **bold** word"]}]}, factory)


def test_element_block_commands_are_locked_once_ready():
    factory = make_counter()
    page, item_id = new_item(factory)
    ready = apply_command(page, ELEMENT_BLOCKS, "markReady", {}, factory)
    assert ready.page.status == "ready"
    with pytest.raises(IllegalCommandError, match="'ready'"):
        _ = apply_command(ready.page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "code", "language": "python", "source": "x"}]}, factory)
    # An element-status mark stays legal - only the structural edits are draft-only.
    marked = apply_command(ready.page, ELEMENT_BLOCKS, "markItemDone", {"itemId": item_id}, factory)
    assert items_of(marked.page)[0]["status"] == "done"


def test_element_scoped_block_adds_are_withheld_from_the_fields_edge():
    """markReady requires items.items, so authoring that field is this stage's work - and the one
    command that does it is the element add, which carries the element's blocks with it.

    The element-scoped adds are withheld: the element they fill has to exist first, so naming them
    here would advertise work that cannot run yet. They stay reachable through describeMutations.
    """
    factory = make_counter()
    page, _ = new_item(factory)
    edges = field_setter_edges(page, ELEMENT_BLOCKS)
    assert [(edge["section"], edge["field"], edge["command"]) for edge in edges] == [
        ("items", "items", "addItem")
    ]
    # One edge, carrying the field's own instruction once.
    assert edges[0]["instruction"] == ELEMENT_BLOCKS.field_spec("items", "items").description


CHILD_BLOCKS = get_page_type("test-child")


def test_a_field_override_is_enforced_at_the_command():
    """One kind name, two body shapes, decided by the field - the case the vocabulary exists for.

    test-child's decisions field declares `paragraph` with a plain `text` arg; test-blocks' body
    takes the standard inline runs. Each rejects the other's shape. If any consumer read a shared
    table instead of the kind's own args this would pass in one direction only.
    """
    factory = make_counter()
    child = create_page(CHILD_BLOCKS, "Child", None, factory)
    ok = apply_command(child, CHILD_BLOCKS, "addDecisions",
                       {"blocks": [{"kind": "paragraph", "text": "plain prose"}]}, factory)
    assert ok.page.sections["decisions"]["body"][0] == {
        "id": ok.created_id, "kind": "paragraph", "text": "plain prose"}
    with pytest.raises(ValidationError, match="unknown keys"):
        _ = apply_command(child, CHILD_BLOCKS, "addDecisions",
                          {"blocks": [{"kind": "paragraph", "inlines": ["prose"]}]}, factory)
    # The mirror on a field declaring the standard kind.
    page = _unified_page(factory)
    with pytest.raises(ValidationError, match="unknown keys"):
        _ = apply_command(page, UNIFIED, "addBody",
                          {"blocks": [{"kind": "paragraph", "text": "plain prose"}]}, factory)


def test_a_block_is_built_in_exactly_one_place():
    """A block created by a page-level add, an element-scoped add, and an element carrying its
    content is identical key for key - including an omitted optional stored as None."""
    payload = {"kind": "code", "language": "py", "source": "x = 1"}

    factory = make_counter()
    page = _unified_page(factory)
    page_level = _body(apply_command(page, UNIFIED, "addBody",
                                     {"blocks": [payload]}, factory).page)[0]

    factory = make_counter()
    created_with = apply_command(
        create_page(ELEMENT_BLOCKS, "A", None, factory), ELEMENT_BLOCKS, "addItem",
        {"text": "one", "detail": [payload]}, factory)
    with_element = items_of(created_with.page)[0]["detail"][0]

    factory = make_counter()
    page2, item_id = new_item(factory)
    appended = apply_command(page2, ELEMENT_BLOCKS, "addItemDetail",
                             {"itemId": item_id, "blocks": [payload]}, factory)
    after = items_of(appended.page)[0]["detail"][0]

    strip = lambda block: {k: v for k, v in block.items() if k != "id"}
    assert strip(page_level) == strip(with_element) == strip(after)


def test_add_item_detail_appends_without_losing_the_element():
    """The whole reason an element-scoped add survived the collapse.

    Without it, correcting a item detail would mean removing and re-adding the element - losing
    its id, its element-FSM status, and the ids of every block already in it.
    """
    factory = make_counter()
    page, item_id = new_item(factory)
    first = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                          {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": ["p"]}]},
                          factory)
    marked = apply_command(first.page, ELEMENT_BLOCKS, "markItemDone", {"itemId": item_id}, factory)
    first_block = items_of(marked.page)[0]["detail"][0]["id"]
    ready = apply_command(marked.page, ELEMENT_BLOCKS, "markReady", {}, factory)
    reopened = apply_command(ready.page, ELEMENT_BLOCKS, "reopen", {}, factory)
    appended = apply_command(reopened.page, ELEMENT_BLOCKS, "addItemDetail",
                             {"itemId": item_id,
                              "blocks": [{"kind": "code", "language": "py", "source": "x = 1"}]},
                             factory)
    element = items_of(appended.page)[0]
    assert element["id"] == item_id                       # the element survived
    assert element["status"] == "done"                    # and so did its FSM state
    assert [block["kind"] for block in element["detail"]] == ["paragraph", "code"]
    assert element["detail"][0]["id"] == first_block      # and the block already in it


def test_every_declared_content_shape_is_checked_through_the_array_path():
    """The grammar reaches one level deeper unchanged - not only a paragraph's runs.

    Each block kind declares its body args with their own content shape (INLINE_RUNS,
    INLINE_RUN_LISTS, INLINE_RUN_GRID), and validate_block runs validate_inline_content on every
    one of them. If it only checked the first arg, a bad run inside a list item or a table cell
    would be stored.
    """
    factory = make_counter()
    page = _unified_page(factory)
    bad_run = {"nope": "not a run"}
    cases = [
        {"kind": "paragraph", "inlines": [bad_run]},
        {"kind": "heading", "level": 2, "inlines": [bad_run]},
        {"kind": "list", "ordered": False, "items": [[bad_run]]},
        {"kind": "quote", "paragraphs": [[bad_run]]},
        {"kind": "table", "header": [["A"]], "rows": [[[bad_run]]]},
    ]
    for block in cases:
        with pytest.raises(ValidationError):
            _ = apply_command(page, UNIFIED, "addBody", {"blocks": [block]}, factory)
    # A markdown token is rejected inside a list item and a table cell too, not just a paragraph.
    with pytest.raises(ValidationError, match="Markdown syntax"):
        _ = apply_command(page, UNIFIED, "addBody", {"blocks": [
            {"kind": "list", "ordered": True, "items": [["a **bold** item"]]}]}, factory)
    with pytest.raises(ValidationError, match="Markdown syntax"):
        _ = apply_command(page, UNIFIED, "addBody", {"blocks": [
            {"kind": "table", "header": [["A"]], "rows": [[["a `code` cell"]]]}]}, factory)
    # The same rules on an element-scoped add, since every block path runs one validator.
    element_page, item_id = new_item(factory)
    with pytest.raises(ValidationError, match="Markdown syntax"):
        _ = apply_command(element_page, ELEMENT_BLOCKS, "addItemDetail", {
            "itemId": item_id,
            "blocks": [{"kind": "list", "ordered": False,
                        "items": [["a **bold** item"]]}]}, factory)

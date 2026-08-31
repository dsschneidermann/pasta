"""Unit tests for the pure Markdown renderer (src.render)."""

import pytest
from wenmode import Wenmode
from wenmode.presets import github

from src.commands import apply_command, create_page
from src.errors import ConflictError, ValidationError
from src.model import Page
from src.pagetypes.core.specs import FSMSpec
from src.pagetypes.core.pagetype import PageType
from src.pagetypes._registry import get_page_type
from src.render import (RefContext, checkbox_state, escape_markdown, page_text, render_page,
                            render_workspace_links)

# Hand-authored capability fixtures (src.testtypes): test-fields for scalar/prose/list content,
# test-blocks for the inline-run block surface, test-child for element-FSM checkboxes, test-lifecycle
# for a no-checkbox element FSM.
FIELDS = get_page_type("test-fields")
BLOCKS = get_page_type("test-blocks")


def make_counter():
    state = {"n": 0}

    def factory(prefix: str) -> str:
        state["n"] += 1
        return f"{prefix}:{state['n']}" if prefix else f"el{state['n']}"

    return factory


def test_render_fields_markdown():
    factory = make_counter()
    page = create_page(FIELDS, "Page title", None, factory)
    page = apply_command(page, FIELDS, "setKind", {"kind": "alpha"}, factory).page
    page = apply_command(page, FIELDS, "setBody", {"text": "The body."}, factory).page
    page = apply_command(page, FIELDS, "addItem", {"text": "Item text."}, factory).page
    md = render_page(page, FIELDS)
    assert md.startswith("# Page title")
    assert "- **kind:** alpha" in md
    assert "The body." in md
    assert "Item text." in md


def test_meta_line_shows_the_revision_only_when_present():
    page = create_page(FIELDS, "Page title", None, make_counter())
    # A page carrying no token (created before the feature) renders the bare type/status meta line.
    assert " · rev `" not in render_page(page, FIELDS)
    # Once it carries one, the meta line surfaces it beside the status.
    page.status_revision_token = "042917"
    assert "· rev `042917`" in render_page(page, FIELDS)


def test_render_blocks_heading_and_code():
    factory = make_counter()
    page = create_page(BLOCKS, "Doc", None, factory)
    page = apply_command(page, BLOCKS, "addBody", {"blocks": [{"kind": "heading", "level": 2, "inlines": ["Overview"]}]}, factory).page
    page = apply_command(page, BLOCKS, "addBody", {"blocks": [{"kind": "code", "language": "py", "source": "x = 1"}]}, factory).page
    md = render_page(page, BLOCKS)
    assert "## Body" in md            # the section heading
    assert "## Overview" in md        # the heading block
    assert "```py" in md and "x = 1" in md


def test_page_text_projection():
    factory = make_counter()
    page = create_page(FIELDS, "Page title", None, factory)
    page = apply_command(page, FIELDS, "setBody", {"text": "concurrency matters"}, factory).page
    text = page_text(page, FIELDS)
    assert "Page title" in text and "concurrency" in text


TREE = {
    "workspaceId": "ws:demo",
    "name": "Demo",
    "nodes": [
        {"id": "test-fields:a1", "title": "Root", "type": "test-fields", "status": "active",
         "children": [
             {"id": "test-flow:b1", "title": "Child", "type": "test-flow", "status": "draft",
              "children": []},
         ]},
    ],
}


def test_render_workspace_links_nested_and_meta():
    md = render_workspace_links(TREE, show_archived=False, show_meta=True)
    # workspace-scoped page links, in a nested list
    assert "- [Root](/ws:demo/page/test-fields:a1)" in md
    assert "\n  - [Child](/ws:demo/page/test-flow:b1)" in md   # child indented one level
    assert "*test-fields* · `active`" in md                    # meta shown by default (type italic, status code)


def test_render_workspace_links_show_meta_false():
    md = render_workspace_links(TREE, show_archived=False, show_meta=False)
    assert "[Root](/ws:demo/page/test-fields:a1)" in md         # links still present
    assert "`" not in md and "·" not in md                      # but no type/status meta


def test_render_workspace_links_status_suffix_for_change_types_only():
    # feature-brief/simple-change/bug-report carry their status in parens inside the link text;
    # other page types (e.g. architecture) render a plain title with no status suffix.
    tree = {"workspaceId": "ws:demo", "nodes": [
        {"id": "feature-brief:a1", "title": "Feature", "type": "feature-brief", "status": "shipped",
         "children": []},
        {"id": "simple-change:b1", "title": "Change", "type": "simple-change", "status": "done",
         "children": []},
        {"id": "bug-report:c1", "title": "Bug", "type": "bug-report", "status": "open",
         "children": []},
        {"id": "architecture:d1", "title": "Arch", "type": "architecture", "status": "current",
         "children": []},
    ]}
    md = render_workspace_links(tree, show_archived=False, show_meta=False)
    assert "[Feature (shipped)](/ws:demo/page/feature-brief:a1)" in md
    assert "[Change (done)](/ws:demo/page/simple-change:b1)" in md
    assert "[Bug (open)](/ws:demo/page/bug-report:c1)" in md
    assert "[Arch](/ws:demo/page/architecture:d1)" in md
    assert "[Arch (current)]" not in md


def test_render_workspace_links_archived_marker_is_prefix():
    # An archived node is flagged with a bold (A) marker BEFORE its link, not a trailing suffix.
    tree = {"workspaceId": "ws:demo", "nodes": [
        {"id": "test-fields:a1", "title": "Root", "type": "test-fields", "status": "active",
         "children": [
             {"id": "test-flow:z1", "title": "Gone", "type": "test-flow", "status": "closed",
              "archived": True, "children": []},
         ]},
    ]}
    md = render_workspace_links(tree, show_archived=True, show_meta=True)
    assert "- **(A)** [Gone](/ws:demo/page/test-flow:z1?archived=true)" in md   # marker precedes the link
    assert "- [Root](/ws:demo/page/test-fields:a1?archived=true)" in md         # active node present...
    assert "**(A)** [Root]" not in md                                           # ...with no (A) marker


# ============================================================================
# test-blocks - the inline-run grammar + full block-editing surface
# ============================================================================
def new_blocks(factory):
    return create_page(BLOCKS, "Doc", None, factory)


def test_render_blocks_inline_runs():
    factory = make_counter()
    page = new_blocks(factory)
    page = apply_command(page, BLOCKS, "addBody", {"blocks": [{"kind": "paragraph", "inlines": [
        "plain ",
        {"text": "bold", "bold": True}, " ",
        {"text": "ital", "italic": True}, " ",
        {"code": "x=1"}, " ",
        {"text": "site", "href": "https://x"}, " ",
        {"ref": "test-fields:abc"},
    ]}]}, factory).page
    md = render_page(page, BLOCKS)
    assert "plain " in md
    assert "**bold**" in md
    assert "*ital*" in md
    assert "`x=1`" in md
    assert "[site](https://x)" in md
    # With no RefContext the ref can't be resolved to a title or a workspace link -> bare id text.
    assert "test-fields:abc" in md
    assert "/page/test-fields:abc" not in md


def test_render_ref_titled_link_and_show_archived():
    factory = make_counter()
    page = new_blocks(factory)
    page = apply_command(page, BLOCKS, "addBody",
                         {"blocks": [{"kind": "paragraph", "inlines": ["see ", {"ref": "test-fields:abc"}]}]}, factory).page
    titles = {"test-fields:abc": "Page title"}
    # A ref resolves to the target's title, linked to the real /<workspaceId>/page/<id> route.
    md = render_page(page, BLOCKS, ref_context=RefContext("ws:demo", titles, {}, {}))
    assert "[Page title](/ws:demo/page/test-fields:abc)" in md
    # show_archived flows onto the link as ?archived=true; default omits it.
    md_arch = render_page(page, BLOCKS, ref_context=RefContext("ws:demo", titles, {}, {}, show_archived=True))
    assert "[Page title](/ws:demo/page/test-fields:abc?archived=true)" in md_arch


def test_render_ref_fallback_when_unresolved():
    factory = make_counter()
    page = new_blocks(factory)
    page = apply_command(page, BLOCKS, "addBody",
                         {"blocks": [{"kind": "paragraph", "inlines": [{"ref": "test-fields:missing"}]}]}, factory).page
    # A RefContext whose title map lacks the id falls back to the bare id, with no link emitted.
    md = render_page(page, BLOCKS, ref_context=RefContext("ws:demo", {"test-fields:other": "Other"}, {}, {}))
    assert "test-fields:missing" in md
    assert "/page/test-fields:missing" not in md


def test_render_blocks_block_kinds():
    factory = make_counter()
    page = new_blocks(factory)
    page = apply_command(page, BLOCKS, "addBody", {"blocks": [{"kind": "heading", "level": 2, "inlines": ["Setup"]}]}, factory).page
    page = apply_command(page, BLOCKS, "addBody", {"blocks": [{"kind": "list", "ordered": True, "items": [["first"], ["second"]]}]}, factory).page
    page = apply_command(page, BLOCKS, "addBody", {"blocks": [{"kind": "list", "ordered": False, "items": [["a"], ["b"]]}]}, factory).page
    page = apply_command(page, BLOCKS, "addBody", {"blocks": [{"kind": "quote", "paragraphs": [["to be"], ["or not"]]}]}, factory).page
    page = apply_command(page, BLOCKS, "addBody", {"blocks": [{"kind": "table", "header": [["Name"], ["Role"]], "rows": [[["Ann"], ["Lead"]]], "align": ["left", "center"]}]}, factory).page
    page = apply_command(page, BLOCKS, "addBody", {"blocks": [{"kind": "divider"}]}, factory).page
    md = render_page(page, BLOCKS)
    assert "## Setup" in md
    assert "1. first" in md and "2. second" in md          # ordered list
    assert "- a" in md and "- b" in md                     # unordered list
    assert "> to be" in md and "> or not" in md            # quote paragraphs
    assert "| Name | Role |" in md                         # table header
    assert "| :--- | :---: |" in md                        # per-column alignment
    assert "| Ann | Lead |" in md                          # table row
    assert any(line == "---" for line in md.splitlines())  # divider on its own line


def test_blocks_block_move_and_remove():
    factory = make_counter()
    p = apply_command(new_blocks(factory), BLOCKS, "addBody", {"blocks": [{"kind": "paragraph", "inlines": ["first"]}]}, factory)
    first_id = p.created_id
    h = apply_command(p.page, BLOCKS, "addBody", {"blocks": [{"kind": "heading", "level": 1, "inlines": ["Title"]}]}, factory)
    heading_id = h.created_id
    # reorderBlock moves by id - front insert names no predecessor
    moved = apply_command(h.page, BLOCKS, "reorderBlock",
                          {"blockId": heading_id, "toIndex": 0, "precedingId": None}, factory)
    assert [b["id"] for b in moved.page.sections["body"]["body"]] == [heading_id, first_id]
    # a stale predecessor for the destination slot is rejected
    with pytest.raises(ConflictError):
        _ = apply_command(moved.page, BLOCKS, "reorderBlock",
                      {"blockId": first_id, "toIndex": 0, "precedingId": heading_id}, factory)
    # removeBlock drops it
    removed = apply_command(moved.page, BLOCKS, "removeBlock", {"blockId": first_id}, factory)
    assert [b["id"] for b in removed.page.sections["body"]["body"]] == [heading_id]


def test_blocks_add_block_index_insertion():
    factory = make_counter()
    a = apply_command(new_blocks(factory), BLOCKS, "addBody", {"blocks": [{"kind": "paragraph", "inlines": ["A"]}]}, factory)
    b = apply_command(a.page, BLOCKS, "addBody", {"blocks": [{"kind": "paragraph", "inlines": ["B"]}]}, factory)
    c = apply_command(b.page, BLOCKS, "addBody",
                      {"blocks": [{"kind": "paragraph", "inlines": ["C"]}], "index": 1, "precedingId": a.created_id}, factory)  # between A and B
    assert [blk["inlines"] for blk in c.page.sections["body"]["body"]] == [["A"], ["C"], ["B"]]


def test_a_block_is_retyped_by_removing_it_and_adding_at_its_slot():
    """There is no in-place edit, so changing a paragraph into a heading is remove plus a
    positioned add. The block keeps its slot and renders as the new kind; the id is new."""
    factory = make_counter()
    p = apply_command(new_blocks(factory), BLOCKS, "addBody",
                      {"blocks": [{"kind": "paragraph", "inlines": ["x"]}]}, factory)
    removed = apply_command(p.page, BLOCKS, "removeBlock", {"blockId": p.created_id}, factory)
    retyped = apply_command(removed.page, BLOCKS, "addBody",
                            {"blocks": [{"kind": "heading", "level": 1, "inlines": ["y"]}]},
                            factory)
    assert retyped.page.sections["body"]["body"] == [
        {"id": retyped.created_id, "kind": "heading", "level": 1, "inlines": ["y"]}
    ]
    assert retyped.created_id != p.created_id
    assert "## y" not in render_page(retyped.page, BLOCKS)   # level 1, so a single hash
    assert "# y" in render_page(retyped.page, BLOCKS)


def test_blocks_reject_markdown_and_bad_table_at_apply_time():
    factory = make_counter()
    page = new_blocks(factory)
    with pytest.raises(ValidationError):
        _ = apply_command(page, BLOCKS, "addBody", {"blocks": [{"kind": "paragraph", "inlines": ["**bold**"]}]}, factory)
    with pytest.raises(ValidationError):
        _ = apply_command(page, BLOCKS, "addBody",
                      {"blocks": [{"kind": "table", "header": [["A"], ["B"]], "rows": [[["only-one"]]]}]}, factory)  # width mismatch


def test_blocks_page_text_includes_inline_content():
    factory = make_counter()
    page = new_blocks(factory)
    page = apply_command(page, BLOCKS, "addBody",
                         {"blocks": [{"kind": "paragraph", "inlines": ["restart the ", {"text": "scheduler", "bold": True}]}]}, factory).page
    page = apply_command(page, BLOCKS, "addBody",
                         {"blocks": [{"kind": "list", "ordered": False, "items": [["check disk"], [{"code": "df -h"}]]}]}, factory).page
    text = page_text(page, BLOCKS)
    assert "scheduler" in text     # marked run
    assert "check disk" in text    # list item
    assert "df -h" in text         # inline code run


# ============================================================================
# full page shape on render - every section shown, empty -> *None.*, + Child pages
# ============================================================================
FIELDS_SECTION_NAMES = ["Basics", "Items"]


def _page_with_children(child_ids):
    """A bare test-fields Page (empty sections) with the given direct child ids."""
    return Page(id="test-fields:p", type="test-fields", title="Parent",
                status="active", child_ids=list(child_ids))


def test_render_empty_page_shows_full_shape():
    page = create_page(FIELDS, "Blank", None, make_counter())
    md = render_page(page, FIELDS)
    # every declared section heading is present even though nothing is filled in
    for name in FIELDS_SECTION_NAMES:
        assert f"## {name}" in md
    assert "## Child pages" in md
    # an empty scalar keeps its label; empty prose/list fall back to the italic *None.*
    assert "- **label:** *None.*" in md
    assert "- **kind:** *None.*" in md
    assert "*None.*" in md


def test_render_filled_fields_are_not_none():
    factory = make_counter()
    page = create_page(FIELDS, "Filled", None, factory)
    page = apply_command(page, FIELDS, "setKind", {"kind": "beta"}, factory).page
    page = apply_command(page, FIELDS, "setBody", {"text": "The body."}, factory).page
    md = render_page(page, FIELDS)
    assert "- **kind:** beta" in md             # filled scalar renders its value...
    assert "- **kind:** *None.*" not in md       # ...not the fallback
    assert "The body." in md
    assert "*None.*" in md                        # but still-empty fields (label, items) show it
    assert "*test-fields* · `active`" in md      # page-header meta: type italic, status in code


def test_render_child_pages_direct_titled_links_only():
    page = _page_with_children(["test-flow:c"])
    titles = {"test-fields:p": "Parent", "test-flow:c": "Child", "test-flow:g": "Grand"}
    types = {"test-flow:c": "test-flow"}
    statuses = {"test-flow:c": "open"}
    md = render_page(page, FIELDS, ref_context=RefContext("ws:demo", titles, types, statuses))
    assert "## Child pages" in md
    # the direct child links to its page, annotated with its type · status
    assert "- [Child](/ws:demo/page/test-flow:c) *test-flow* · `open`" in md
    # a grandchild (not a DIRECT child of this page) is never listed
    assert "/page/test-flow:g" not in md


def test_render_child_pages_show_archived_query_and_flag():
    page = _page_with_children(["test-flow:c", "test-flow:a"])
    titles = {"test-flow:c": "Child", "test-flow:a": "Archie"}
    types = {"test-flow:c": "test-flow", "test-flow:a": "test-flow"}
    statuses = {"test-flow:c": "open", "test-flow:a": "closed"}
    archived = frozenset({"test-flow:a"})
    # default: the archived child is hidden and the active child's link carries no query
    md = render_page(page, FIELDS, ref_context=RefContext("ws:demo", titles, types, statuses, archived_ids=archived))
    assert "- [Child](/ws:demo/page/test-flow:c) *test-flow* · `open`" in md
    assert "test-flow:a" not in md
    # show_archived: the archived child appears with a bold (A) marker prefixed before the link,
    # keeps its type · status, and links carry ?archived=true
    md_arch = render_page(page, FIELDS,
                          ref_context=RefContext("ws:demo", titles, types, statuses,
                                                 show_archived=True, archived_ids=archived))
    assert "- [Child](/ws:demo/page/test-flow:c?archived=true) *test-flow* · `open`" in md_arch
    assert "- **(A)** [Archie](/ws:demo/page/test-flow:a?archived=true) *test-flow* · `closed`" in md_arch


def test_render_child_pages_archived_sort_below_active():
    # An archived child listed first in child_ids still renders below the active one (stable partition).
    page = _page_with_children(["test-flow:a", "test-flow:c"])
    titles = {"test-flow:a": "Archie", "test-flow:c": "Child"}
    types = {"test-flow:a": "test-flow", "test-flow:c": "test-flow"}
    statuses = {"test-flow:a": "closed", "test-flow:c": "open"}
    archived = frozenset({"test-flow:a"})
    md = render_page(page, FIELDS,
                     ref_context=RefContext("ws:demo", titles, types, statuses,
                                            show_archived=True, archived_ids=archived))
    assert md.index("Child") < md.index("Archie")   # active child renders above the archived one


def test_render_child_pages_none_when_empty():
    md = render_page(_page_with_children([]), FIELDS, ref_context=RefContext("ws:demo", {}, {}, {}))
    assert "## Child pages" in md
    assert md.rstrip().endswith("*None.*")       # the trailing (Child pages) section is the fallback


def test_render_child_pages_bare_ids_without_context():
    md = render_page(_page_with_children(["test-flow:c"]), FIELDS)   # no RefContext
    for name in FIELDS_SECTION_NAMES:            # all sections still render
        assert f"## {name}" in md
    assert "- test-flow:c" in md                 # a child is a bare id...
    assert "/page/test-flow:c" not in md         # ...with no link, absent a workspace context


# ============================================================================
# References - outgoing page links, rendered before Child pages
# ============================================================================
def _page_with_links(links):
    """A bare test-fields Page (empty sections) with the given outgoing links [{to, role}]."""
    return Page(id="test-fields:p", type="test-fields", title="Src",
                status="active", links=list(links))


def test_render_references_titled_type_status_link_and_role():
    page = _page_with_links([{"to": "test-flow:x", "role": "depends-on"}])
    ctx = RefContext("ws:demo", {"test-flow:x": "Target"}, {"test-flow:x": "test-flow"},
                     {"test-flow:x": "open"})
    md = render_page(page, FIELDS, ref_context=ctx)
    assert "## References" in md
    # target renders as the same titled type·status link as a child, plus the edge role
    assert "- [Target](/ws:demo/page/test-flow:x) *test-flow* · `open` - depends-on" in md
    # the References section sits BEFORE the Child pages section
    assert md.index("## References") < md.index("## Child pages")


def test_render_references_none_and_bare_id_fallback():
    # no links -> the References section shows the *None.* fallback
    md = render_page(_page_with_links([]), FIELDS, ref_context=RefContext("ws:demo", {}, {}, {}))
    assert "## References\n\n*None.*" in md
    # no ref_context -> the target is a bare id + role, with no link
    md_bare = render_page(_page_with_links([{"to": "test-flow:x", "role": "rel"}]), FIELDS)
    assert "- test-flow:x - rel" in md_bare
    assert "/page/test-flow:x" not in md_bare


def test_render_references_archived_target_hidden_then_flagged():
    page = _page_with_links([{"to": "test-flow:a", "role": "supersedes"}])
    titles = {"test-flow:a": "Archie"}
    types = {"test-flow:a": "test-flow"}
    statuses = {"test-flow:a": "closed"}
    archived = frozenset({"test-flow:a"})
    # default: an archived target is hidden -> the reference is absent
    md = render_page(page, FIELDS, ref_context=RefContext("ws:demo", titles, types, statuses, archived_ids=archived))
    assert "test-flow:a" not in md
    # show_archived: shown with the (A) marker prefixed before the link, ?archived=true carried
    md_arch = render_page(page, FIELDS,
                          ref_context=RefContext("ws:demo", titles, types, statuses,
                                                 show_archived=True, archived_ids=archived))
    assert ("- **(A)** [Archie](/ws:demo/page/test-flow:a?archived=true) *test-flow* · `closed` - supersedes"
            in md_arch)


# ============================================================================
# element-state checkboxes - declared on the FSM (checkmark_done + auto-empty initial)
# ============================================================================
def test_render_step_checkbox_from_fsm():
    factory = make_counter()
    child = get_page_type("test-child")
    page = create_page(child, "Child", "test-lifecycle:x", factory)
    page = apply_command(page, child, "addStep", {"text": "do A"}, factory).page   # todo (initial)
    page = apply_command(page, child, "addStep", {"text": "do B"}, factory).page   # todo
    step_b = page.sections["steps"]["items"][1]["id"]
    page = apply_command(page, child, "markStepDone", {"stepId": step_b}, factory).page
    md = render_page(page, child)
    assert "- [ ] do A" in md          # initial state -> unchecked box
    assert "- [x] do B" in md          # checkmark_done state -> checked box
    assert "_[todo]_" in md and "_[done]_" in md   # the trailing status label is unchanged


def test_render_check_checkbox_pending_passed_failed():
    factory = make_counter()
    child = get_page_type("test-child")
    page = create_page(child, "Child", "test-lifecycle:x", factory)
    for text in ["c pend", "c pass", "c fail"]:
        page = apply_command(page, child, "addCheck", {"text": text}, factory).page
    items = page.sections["checks"]["items"]
    page = apply_command(page, child, "markCheckPassed", {"checkId": items[1]["id"]}, factory).page
    page = apply_command(page, child, "markCheckFailed", {"checkId": items[2]["id"]}, factory).page
    md = render_page(page, child)
    assert "- [ ] c pend" in md         # pending (initial) -> unchecked
    assert "- [x] c pass" in md         # passed (checkmark_done) -> checked
    assert "- c fail" in md             # failed -> NO box (neither initial nor checkmark_done)
    assert "- [ ] c fail" not in md and "- [x] c fail" not in md


def test_render_skipped_element_has_no_checkbox_but_keeps_its_suffix():
    factory = make_counter()
    child = get_page_type("test-child")
    page = create_page(child, "Child", "test-lifecycle:x", factory)
    page = apply_command(page, child, "addStep", {"text": "s skip"}, factory).page
    page = apply_command(page, child, "addCheck", {"text": "c skip"}, factory).page
    step_id = page.sections["steps"]["items"][0]["id"]
    check_id = page.sections["checks"]["items"][0]["id"]
    page = apply_command(page, child, "markStepSkipped", {"stepId": step_id}, factory).page
    page = apply_command(page, child, "markCheckSkipped", {"checkId": check_id}, factory).page
    md = render_page(page, child)
    # skipped is neither the initial nor the checkmark_done state, so it renders with no box - but
    # the trailing status label still surfaces the disposition.
    assert "- s skip" in md and "- c skip" in md
    assert "- [ ] s skip" not in md and "- [x] s skip" not in md
    assert "- [ ] c skip" not in md and "- [x] c skip" not in md
    assert "_[skipped]_" in md


def test_render_question_has_no_checkbox():
    factory = make_counter()
    lifecycle = get_page_type("test-lifecycle")
    page = create_page(lifecycle, "Feature", None, factory)
    page = apply_command(page, lifecycle, "askQuestion", {"text": "why"}, factory).page   # question FSM: no checkmark_done
    md = render_page(page, lifecycle)
    assert "- why" in md
    assert "- [ ] why" not in md and "- [x] why" not in md


def test_render_non_fsm_list_has_no_checkbox():
    factory = make_counter()
    page = create_page(FIELDS, "Fixture", None, factory)
    page = apply_command(page, FIELDS, "addItem", {"text": "must hold"}, factory).page   # no element_fsm, no status
    md = render_page(page, FIELDS)
    assert "- must hold" in md
    assert "- [ ] must hold" not in md and "- [x] must hold" not in md


# ============================================================================
# plain-text markdown escaping on the web render path (escape_plain_text)
# ============================================================================
_MD2HTML = Wenmode(github)   # same preset the web routes use


def _web_ctx(titles=None):
    """A RefContext in web render-mode (escape_plain_text=True)."""
    return RefContext("ws:demo", titles or {}, {}, {}, escape_plain_text=True)


# --- escape_markdown() as a pure function ---

def test_escape_markdown_passes_through_empty():
    assert escape_markdown("") == ""
    assert escape_markdown(None) is None


def test_escape_markdown_global_specials_render_as_text():
    html = _MD2HTML.render(escape_markdown("use *emph* and _under_ and `code` and [x]"))
    assert "<em>" not in html and "<strong>" not in html
    assert "<code>" not in html and "<a " not in html
    for literal in ("*emph*", "_under_", "`code`", "[x]"):
        assert literal in html


def test_escape_markdown_escapes_backslash_first():
    # a literal backslash is doubled, so it does not consume the markers the later rules add
    assert escape_markdown("a\\b") == "a\\\\b"
    assert "a\\b" in _MD2HTML.render(escape_markdown("a\\b"))


def test_escape_markdown_neutralises_line_start_blocks():
    for value, tag in [("# heading", "<h1"), ("###### h6", "<h6"),
                       ("- item", "<ul"), ("+ item", "<ul"),
                       ("> quote", "<blockquote"), ("~~~\ncode\n~~~", "<pre"),
                       ("Title\n===", "<h1")]:
        html = _MD2HTML.render(escape_markdown(value))
        assert tag not in html, f"{value!r} should not render as {tag}"


def test_escape_markdown_ordered_list_leaves_no_visible_backslash():
    html = _MD2HTML.render(escape_markdown("1. first thing"))
    assert "<ol" not in html
    assert "1. first thing" in html
    assert "\\" not in html          # dot escaped, not the digit -> no stray backslash


def test_escape_markdown_is_multiline():
    # a construct on a LATER line is also neutralised (re.MULTILINE)
    html = _MD2HTML.render(escape_markdown("intro line\n# mid heading\nmore"))
    assert "<h1>" not in html and "# mid heading" in html


# --- escaping threaded through the renderer, web vs MCP ---

def _fields_page_with_specials():
    factory = make_counter()
    page = create_page(FIELDS, "Page *title*", None, factory)
    page = apply_command(page, FIELDS, "setBody",
                         {"text": "use *args\n# not a heading"}, factory).page
    page = apply_command(page, FIELDS, "addItem", {"text": "- not a bullet"}, factory).page
    return page, FIELDS


def test_render_page_mcp_leaves_plain_text_unescaped():
    page, page_type = _fields_page_with_specials()
    md = render_page(page, page_type)                   # MCP path: no ref_context
    assert "# Page *title*" in md                    # title raw
    assert "use *args" in md and "# not a heading" in md  # prose raw
    assert "- not a bullet" in md                       # list text raw
    assert "\\*" not in md and "\\#" not in md          # nothing was escaped


def test_render_page_web_escapes_every_plain_text_leaf():
    page, page_type = _fields_page_with_specials()
    md = render_page(page, page_type, ref_context=_web_ctx())
    assert "# Page \\*title\\*" in md                # heading marker kept, title text escaped
    assert "use \\*args" in md and "\\# not a heading" in md   # prose escaped (line-start # too)
    assert "\\- not a bullet" in md                     # list element's leading dash escaped
    assert "## Basics" in md and "## Items" in md        # renderer's structural markdown survives


def test_render_page_web_escapes_scalar_value():
    factory = make_counter()
    page = create_page(FIELDS, "Fixture", None, factory)
    page = apply_command(page, FIELDS, "setLabel", {"label": "auth *module* [x]"}, factory).page
    md_web = render_page(page, FIELDS, ref_context=_web_ctx())
    assert "- **label:** auth \\*module\\* \\[x\\]" in md_web   # value escaped, **key:** label kept
    md_mcp = render_page(page, FIELDS)
    assert "- **label:** auth *module* [x]" in md_mcp          # MCP raw


def test_render_page_web_escapes_inline_run_text_keeps_decoration():
    factory = make_counter()
    page = create_page(BLOCKS, "Doc", None, factory)
    page = apply_command(page, BLOCKS, "addBody",
                         {"blocks": [{"kind": "paragraph", "inlines": [{"text": "a*b", "bold": True}, " ", {"code": "x_y"}]}]}, factory).page
    md_web = render_page(page, BLOCKS, ref_context=_web_ctx())
    assert "**a\\*b**" in md_web                        # inner * escaped, bold markers intact
    assert "`x_y`" in md_web                            # code run rendered verbatim
    html = _MD2HTML.render(md_web)
    assert "<strong>a*b</strong>" in html and "<code>x_y</code>" in html
    md_mcp = render_page(page, BLOCKS)                  # MCP path leaves the run text raw
    assert "**a*b**" in md_mcp and "`x_y`" in md_mcp


def test_render_page_web_leaves_code_block_source_verbatim():
    factory = make_counter()
    page = create_page(BLOCKS, "Doc", None, factory)
    page = apply_command(page, BLOCKS, "addBody",
                         {"blocks": [{"kind": "code", "language": "py", "source": "x = 1  # *keep* _me_"}]}, factory).page
    md_web = render_page(page, BLOCKS, ref_context=_web_ctx())
    assert "```py" in md_web
    assert "x = 1  # *keep* _me_" in md_web             # fenced code source is not escaped


def test_render_workspace_links_escapes_titles_web_only():
    tree = {"workspaceId": "ws:demo", "nodes": [
        {"id": "test-fields:a1", "title": "Root *star* [x]", "type": "test-fields",
         "status": "active", "children": []}]}
    web = render_workspace_links(tree, escape_plain_text=True)
    mcp = render_workspace_links(tree, escape_plain_text=False)
    assert "[Root \\*star\\* \\[x\\]]" in web           # title label escaped on the web path
    assert "[Root *star* [x]]" in mcp                   # default (MCP-style) unchanged
    assert "/ws:demo/page/test-fields:a1" in web and "/ws:demo/page/test-fields:a1" in mcp


def test_render_child_pages_web_escapes_child_label():
    # A child whose TITLE carries markdown-special characters must be escaped in the parent's
    # `Child pages` list on the web path (and left raw on the MCP path). The page-title heading and
    # workspace-tree-link cases are covered above; this closes the same gap for the child-link label.
    page = _page_with_children(["test-flow:c"])
    titles = {"test-flow:c": "Child *star* [x]"}
    types = {"test-flow:c": "test-flow"}
    statuses = {"test-flow:c": "open"}
    web = render_page(page, FIELDS,
                      ref_context=RefContext("ws:demo", titles, types, statuses, escape_plain_text=True))
    mcp = render_page(page, FIELDS,
                      ref_context=RefContext("ws:demo", titles, types, statuses))
    assert "- [Child \\*star\\* \\[x\\]](/ws:demo/page/test-flow:c) *test-flow* · `open`" in web   # escaped
    assert "- [Child *star* [x]](/ws:demo/page/test-flow:c) *test-flow* · `open`" in mcp           # raw on MCP


# ============================================================================
# toc - a container renders as JUST its title, meta, and the (unheadered) child list
# ============================================================================
# The production `toc` type is off-limits under the test-mode guard (get_page_type("toc") raises), so
# we build a tag="toc" PageType directly to drive the render branch that keys on page_type.tag.
TOC = PageType(tag="toc", name="Table of contents", description="", sections=(), commands=(),
               fsm=FSMSpec(name="Toc", initial="active", states=("active",)))


def _toc_with_children(child_ids):
    """A bare toc Page (no sections) with the given direct child ids."""
    return Page(id="toc:t", type="toc", title="Features", status="active", child_ids=list(child_ids))


def test_render_toc_omits_reference_and_child_headings():
    page = _toc_with_children(["simple-change:c"])
    titles = {"simple-change:c": "Some change"}
    types = {"simple-change:c": "simple-change"}
    statuses = {"simple-change:c": "closed"}
    md = render_page(page, TOC, ref_context=RefContext("ws:demo", titles, types, statuses))
    assert md.startswith("# Features")            # title kept
    assert "*toc* · `active`" in md               # meta line kept
    assert "## References" not in md              # both section headings dropped for a toc
    assert "## Child pages" not in md
    # the child list itself is still rendered, unheadered, in the usual titled type·status link form
    assert "- [Some change](/ws:demo/page/simple-change:c) *simple-change* · `closed`" in md


def test_render_toc_empty_shows_none_without_headings():
    # An empty toc still drops the headings; the child list falls back to *None.* under the meta line.
    md = render_page(_toc_with_children([]), TOC, ref_context=RefContext("ws:demo", {}, {}, {}))
    assert md.startswith("# Features")
    assert "*toc* · `active`" in md
    assert "## References" not in md and "## Child pages" not in md
    assert md.rstrip().endswith("*None.*")


def test_checkbox_state_maps_element_fsm_states():
    child = get_page_type("test-child")
    steps = child.field_spec("steps", "items")
    checks = child.field_spec("checks", "items")
    questions = get_page_type("test-lifecycle").field_spec("questions", "items")
    items = FIELDS.field_spec("items", "items")
    assert checkbox_state("done", steps.element_fsm) == "done"
    assert checkbox_state("todo", steps.element_fsm) == "todo"
    assert checkbox_state("passed", checks.element_fsm) == "done"
    assert checkbox_state("pending", checks.element_fsm) == "todo"
    assert checkbox_state("failed", checks.element_fsm) is None   # neither done nor initial
    assert checkbox_state("open", questions.element_fsm) is None  # FSM declares no checkmark_done
    assert checkbox_state(None, items.element_fsm) is None        # field has no element FSM


# --- block-bearing element fields --------------------------------------------
ELEMENT_BLOCKS = get_page_type("test-element-blocks")


def _page_with_one_item(factory):
    page = create_page(ELEMENT_BLOCKS, "Plan", None, factory)
    added = apply_command(page, ELEMENT_BLOCKS, "addItem", {"text": "one"}, factory)
    return added.page, added.created_id


def test_an_element_block_field_renders_indented_under_its_bullet():
    factory = make_counter()
    page, item_id = _page_with_one_item(factory)
    page = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                         {"itemId": item_id, "blocks": [{"kind": "code", "language": "python", "source": "x = 1"}]}, factory).page
    md = render_page(page, ELEMENT_BLOCKS)
    assert "- [ ] one _[todo]_\n\n  ```python\n  x = 1\n  ```" in md
    # A block list str()-ed into the bullet would show its dicts.
    assert "{'id':" not in md


def test_an_element_with_no_blocks_renders_exactly_as_before():
    factory = make_counter()
    page, _ = _page_with_one_item(factory)
    md = render_page(page, ELEMENT_BLOCKS)
    # An empty declared block field adds nothing at all - no blank line, no indented content.
    assert "## Items\n\n- [ ] one _[todo]_\n\n## References" in md


def test_page_text_finds_text_inside_an_element_block():
    factory = make_counter()
    page, item_id = _page_with_one_item(factory)
    page = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                         {"itemId": item_id, "blocks": [{"kind": "code", "language": "python", "source": "needle_in_source"}]}, factory).page
    page = apply_command(page, ELEMENT_BLOCKS, "addItemDetail",
                         {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": ["prose ", {"code": "needle_in_code_run"}]}]}, factory).page
    text = page_text(page, ELEMENT_BLOCKS)
    assert "needle_in_source" in text
    assert "needle_in_code_run" in text

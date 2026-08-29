"""Integration tests for the stateful storage shell (src.store).

These exercise real files: persistence across reload, atomic writes, and - importantly -
that the per-workspace lock prevents lost updates under concurrent writers.

The store is page-type-agnostic (it routes everything through get_page_type), so these use the
hand-authored fixtures (src.testtypes) rather than production types: test-fields as a generic
content page, test-flow as a simple leaf, test-blocks for inline-run refs, and the
test-lifecycle/test-child family for auto-children, cross-page refs, guards, and pinning.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.serialize import workspace_from_dict
from src.errors import (
    ConflictError,
    PastaError,
    IllegalCommandError,
    NotFoundError,
    ValidationError,
)
from src.store import Store


def _child(result, page_type):
    """The auto-created child page of a given type from a create_page result."""
    return next(child for child in result.children if child.type == page_type)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def _mutate(store, workspace_id, page_id, commands):
    """Run a batch presenting the page's current status_revision_token on each command - the ordinary
    single-batch caller pattern (read the token, then write against it). A batch that transitions
    mid-sequence is deliberately not expressible this way: the transition regenerates the token, so
    a later command's stamp is stale - such tests present tokens explicitly instead."""
    token = store.get_page(workspace_id, page_id).status_revision_token
    stamped = [{**command, "args": {"statusRevisionToken": token, **(command.get("args") or {})}}
               for command in commands]
    return store.mutate_page_batch(workspace_id, page_id, stamped)


def test_create_and_list_workspace(store):
    workspace = store.create_workspace("demo")
    assert workspace.id.startswith("ws:")
    listed = store.list_workspaces()
    assert [w["name"] for w in listed] == ["demo"]
    assert listed[0]["id"] == workspace.id


def test_create_page_persists_across_reload(store, tmp_path):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Page title").page

    # A brand-new Store over the same directory must see the page (it's on disk).
    reopened = Store(tmp_path)
    fetched = reopened.get_page(workspace.id, page.id)
    assert fetched.title == "Page title"
    assert fetched.status == "active"

    tree = reopened.tree(workspace.id)
    assert tree["nodes"][0]["id"] == page.id


def test_mutate_persists(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-flow", "A change").page
    _mutate(store, workspace.id, page.id, [
        {"command": "setSummary", "args": {"text": "The body."}}
    ])
    _mutate(store, workspace.id, page.id, [
        {"command": "open"}
    ])

    fetched = store.get_page(workspace.id, page.id)
    assert fetched.sections["summary"]["body"] == "The body."
    assert fetched.status == "open"


def test_child_page_and_tree(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "Parent").page
    child = store.create_page(workspace.id, "test-flow", "A change", parent_id=parent.id).page
    tree = store.tree(workspace.id)
    assert tree["nodes"][0]["id"] == parent.id
    assert tree["nodes"][0]["children"][0]["id"] == child.id


def test_duplicate_sibling_title_allowed(store):
    # Sibling titles are not reserved: two siblings may share a title (a title is a label, not an id).
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "Same").page
    b = store.create_page(workspace.id, "test-flow", "Same").page
    assert a.id != b.id
    titles = [store.get_page(workspace.id, pid).title
              for pid in store.load_workspace(workspace.id).root_page_ids]
    assert titles == ["Same", "Same"]


def test_unknown_type_and_missing_ids(store):
    workspace = store.create_workspace("demo")
    with pytest.raises(ValidationError):
        store.create_page(workspace.id, "nonexistent-type", "x")
    with pytest.raises(NotFoundError):
        store.get_page(workspace.id, "test-fields:missing")
    with pytest.raises(NotFoundError):
        store.get_page("ws:missing", "test-fields:missing")


def test_no_temp_files_left_after_writes(store, tmp_path):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Page title").page
    _mutate(store, workspace.id, page.id, [
        {"command": "setBody", "args": {"text": "x"}}
    ])
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp-" in p.name]
    assert leftovers == [], f"atomic write left temp files: {leftovers}"


def test_lifecycle_auto_creates_children(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Dark mode")
    assert sorted(child.type for child in result.children) == ["test-child"]
    # It is pinned into the tree under the parent.
    root = store.tree(workspace.id)["nodes"][0]
    assert {child["type"] for child in root["children"]} == {"test-child"}


def test_add_decision_ref_integrity(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Dark mode")
    parent, child = result.page, _child(result, "test-child")
    # A decision referencing a non-existent question aborts the commit.
    with pytest.raises(ValidationError):
        _mutate(store, workspace.id, child.id, [
            {"command": "addDecisions", "args": {"blocks": [{"kind": "decision", "questionId": "nope", "text": "x"}]}}
        ])
    # Mint the question on the parent, then the same decision resolves.
    asked, asked_created = _mutate(store, workspace.id, parent.id, [
        {"command": "askQuestion", "args": {"text": "contrast?"}}
    ])
    result, result_created = _mutate(store, workspace.id, child.id, [
        {"command": "addDecisions", "args": {"blocks": [{"kind": "decision", "questionId": asked_created[0], "text": "WCAG AA"}]}}
    ])
    assert result.sections["decisions"]["body"][0]["questionId"] == asked_created[0]


def test_list_add_ref_check_rejects_a_dangling_parent_reference(store):
    """The ref check is kind-agnostic: a list add carries it as the blocks addDecision above does,
    so a dangling id aborts the whole batch before anything is written."""
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Dark mode")
    parent, child = result.page, _child(result, "test-child")
    with pytest.raises(ValidationError, match="does not reference an existing element"):
        _mutate(store, workspace.id, child.id, [
            {"command": "addNote", "args": {"questionId": "nope", "text": "x"}}
        ])
    # All-or-nothing: the rejected batch wrote nothing.
    assert store.get_page(workspace.id, child.id).sections["notes"]["items"] == []
    # Mint the question on the parent, then the same note resolves.
    _asked, asked_created = _mutate(store, workspace.id, parent.id, [
        {"command": "askQuestion", "args": {"text": "contrast?"}}
    ])
    noted, _noted_created = _mutate(store, workspace.id, child.id, [
        {"command": "addNote", "args": {"questionId": asked_created[0], "text": "checked at AA"}}
    ])
    assert len(noted.sections["notes"]["items"]) == 1
    assert noted.sections["notes"]["items"][0]["questionId"] == asked_created[0]


def test_inline_ref_integrity_mutate_page(store):
    workspace = store.create_workspace("demo")
    target = store.create_page(workspace.id, "test-fields", "Target").page
    doc = store.create_page(workspace.id, "test-blocks", "Doc").page
    # A dangling inline page-ref aborts the commit and writes nothing.
    with pytest.raises(ValidationError):
        _mutate(store, workspace.id, doc.id, [
            {"command": "addBody", "args": {"blocks": [{"kind": "paragraph", "inlines": ["see ", {"ref": "test-fields:nope"}]}]}}
        ])
    assert store.get_page(workspace.id, doc.id).sections["body"]["body"] == []
    # A ref to an existing page is accepted and stored verbatim.
    _mutate(store, workspace.id, doc.id, [
        {"command": "addBody", "args": {"blocks": [{"kind": "paragraph", "inlines": [{"ref": target.id}]}]}}
    ])
    assert store.get_page(workspace.id, doc.id).sections["body"]["body"][0]["inlines"] == [{"ref": target.id}]
    # A self-ref (the page referencing itself) resolves.
    _mutate(store, workspace.id, doc.id, [
        {"command": "addBody", "args": {"blocks": [{"kind": "paragraph", "inlines": [{"ref": doc.id}]}]}}
    ])
    # A ref to an archived page still resolves - archived pages remain in the workspace.
    store.archive_page(workspace.id, target.id)
    _mutate(store, workspace.id, doc.id, [
        {"command": "addBody", "args": {"blocks": [{"kind": "paragraph", "inlines": [{"ref": target.id}]}]}}
    ])
    assert len(store.get_page(workspace.id, doc.id).sections["body"]["body"]) == 3


def test_inline_ref_integrity_batch_all_or_nothing(store):
    workspace = store.create_workspace("demo")
    target = store.create_page(workspace.id, "test-fields", "Target").page
    doc = store.create_page(workspace.id, "test-blocks", "Doc").page
    # A dangling ref anywhere in the batch aborts the whole batch - nothing commits.
    with pytest.raises(ValidationError):
        _mutate(store, workspace.id, doc.id, [
            {"command": "addBody", "args": {"blocks": [{"kind": "paragraph", "inlines": [{"ref": target.id}]}]}},
            {"command": "addBody", "args": {"blocks": [{"kind": "paragraph", "inlines": [{"ref": "test-fields:nope"}]}]}},
        ])
    assert store.get_page(workspace.id, doc.id).sections["body"]["body"] == []
    # An all-valid batch commits every command.
    _mutate(store, workspace.id, doc.id, [
        {"command": "addBody", "args": {"blocks": [{"kind": "paragraph", "inlines": [{"ref": target.id}]}]}},
        {"command": "addBody", "args": {"blocks": [{"kind": "heading", "level": 2, "inlines": ["ok ", {"ref": target.id}]}]}},
    ])
    assert len(store.get_page(workspace.id, doc.id).sections["body"]["body"]) == 2


def test_render_markdown_resolves_ref_title(store):
    workspace = store.create_workspace("demo")
    target = store.create_page(workspace.id, "test-fields", "Page title").page
    doc = store.create_page(workspace.id, "test-blocks", "Doc").page
    _mutate(store, workspace.id, doc.id, [
        {"command": "addBody", "args": {"blocks": [{"kind": "paragraph", "inlines": ["see ", {"ref": target.id}]}]}}
    ])
    # render_markdown resolves the ref to the target title and the real page route.
    md = store.render_markdown(workspace.id, doc.id)
    assert f"[Page title](/{workspace.id}/page/{target.id})" in md
    # show_archived flows onto the ref link.
    md_arch = store.render_markdown(workspace.id, doc.id, show_archived=True)
    assert f"[Page title](/{workspace.id}/page/{target.id}?archived=true)" in md_arch


def test_ship_guard_blocks_until_child_complete(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Dark mode")
    parent = result.page
    child = _child(result, "test-child")

    # The child's markReady is parent-gated, so take the parent to `planning` first. Then author the
    # child's step + check (structural, draft-only) and mark it ready - a ready child is what the
    # beginImplementation page-status guard requires before building.
    _mutate(store, workspace.id, parent.id, [
        {"command": "setSummary", "args": {"text": "A dark theme."}},
        {"command": "beginPlanning"},
    ])
    _, step_created = _mutate(store, workspace.id, child.id, [
        {"command": "addStep", "args": {"text": "build"}}
    ])
    _, check_created = _mutate(store, workspace.id, child.id, [
        {"command": "addCheck", "args": {"text": "renders"}}
    ])
    _mutate(store, workspace.id, child.id, [{"command": "markReady"}])

    # Reach building. The review gate now refuses to advance while the step is todo and the check
    # pending (the two element-status guards on submitForReview).
    _mutate(store, workspace.id, parent.id, [
        {"command": "addPart", "args": {"name": "Renderer"}},
        {"command": "beginImplementation"},
    ])
    with pytest.raises(IllegalCommandError):
        _mutate(store, workspace.id, parent.id, [{"command": "submitForReview"}])

    # Marks are recordable while the child is `ready`; address both, then review and a (human) ship
    # both pass the guard.
    _mutate(store, workspace.id, child.id, [
        {"command": "markStepDone", "args": {"stepId": step_created[0]}}
    ])
    _mutate(store, workspace.id, child.id, [
        {"command": "markCheckPassed", "args": {"checkId": check_created[0]}}
    ])
    _mutate(store, workspace.id, parent.id, [{"command": "submitForReview"}])
    shipped, _ = _mutate(store, workspace.id, parent.id, [{"command": "ship"}])
    assert shipped.status == "done"


def test_skip_counts_as_addressed_for_review_and_ship(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Dark mode")
    parent = result.page
    child = _child(result, "test-child")

    _mutate(store, workspace.id, parent.id, [
        {"command": "setSummary", "args": {"text": "A dark theme."}},
        {"command": "beginPlanning"},
    ])
    _, step_created = _mutate(store, workspace.id, child.id, [
        {"command": "addStep", "args": {"text": "build"}}
    ])
    _, check_created = _mutate(store, workspace.id, child.id, [
        {"command": "addCheck", "args": {"text": "renders"}}
    ])
    _mutate(store, workspace.id, child.id, [{"command": "markReady"}])
    _mutate(store, workspace.id, parent.id, [
        {"command": "addPart", "args": {"name": "Renderer"}},
        {"command": "beginImplementation"},
    ])

    # A deliberately skipped step and check are addressed: the review gate opens and the (human)
    # ship gate passes, because skipped is in each guard's allowed set.
    _mutate(store, workspace.id, child.id, [
        {"command": "markStepSkipped", "args": {"stepId": step_created[0]}}
    ])
    _mutate(store, workspace.id, child.id, [
        {"command": "markCheckSkipped", "args": {"checkId": check_created[0]}}
    ])
    _mutate(store, workspace.id, parent.id, [{"command": "submitForReview"}])
    shipped, _ = _mutate(store, workspace.id, parent.id, [{"command": "ship"}])
    assert shipped.status == "done"


def test_reopen_unskips_a_step(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Feature")
    parent = result.page
    child = _child(result, "test-child")

    _mutate(store, workspace.id, parent.id, [
        {"command": "setSummary", "args": {"text": "x"}},
        {"command": "beginPlanning"},
    ])
    _, step_created = _mutate(store, workspace.id, child.id, [
        {"command": "addStep", "args": {"text": "build"}}
    ])
    _mutate(store, workspace.id, child.id, [{"command": "markReady"}])
    _mutate(store, workspace.id, parent.id, [
        {"command": "addPart", "args": {"name": "R"}},
        {"command": "beginImplementation"},
    ])

    # skip fires todo -> skipped; reopen fires skipped -> todo (the same reopen event that also
    # fires done -> todo). The reopened step is unaddressed again, so the review gate rejects it.
    _mutate(store, workspace.id, child.id, [
        {"command": "markStepSkipped", "args": {"stepId": step_created[0]}}
    ])
    assert store.get_page(workspace.id, child.id).sections["steps"]["items"][0]["status"] == "skipped"
    _mutate(store, workspace.id, child.id, [
        {"command": "markStepTodo", "args": {"stepId": step_created[0]}}
    ])
    assert store.get_page(workspace.id, child.id).sections["steps"]["items"][0]["status"] == "todo"
    with pytest.raises(IllegalCommandError):
        _mutate(store, workspace.id, parent.id, [{"command": "submitForReview"}])


def test_begin_implementation_blocked_until_children_ready(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Feature")
    parent, child = result.page, _child(result, "test-child")

    # Reach planning and satisfy the required-content gate (a part), leaving the child in draft. The
    # transition takes its own batch: a command after a transition would carry the now-stale token.
    _mutate(store, workspace.id, parent.id, [
        {"command": "setSummary", "args": {"text": "x"}},
        {"command": "beginPlanning"},
    ])
    _mutate(store, workspace.id, parent.id, [
        {"command": "addPart", "args": {"name": "R"}},
    ])

    # The child is still draft, so beginImplementation is rejected (page-status guard) and surfaces
    # in nextActions as a blocked agent edge whose reason names the unmet 'ready' status.
    with pytest.raises(IllegalCommandError):
        _mutate(store, workspace.id, parent.id, [{"command": "beginImplementation"}])
    blocked = {edge["command"]: edge for edge in store.next_actions(workspace.id, parent.id)["blocked"]}
    assert "beginImplementation" in blocked and "ready" in blocked["beginImplementation"]["reason"]

    # Once the child has its step and is ready, beginImplementation succeeds.
    _mutate(store, workspace.id, child.id, [
        {"command": "addStep", "args": {"text": "build"}},
        {"command": "markReady"},
    ])
    advanced, _ = _mutate(store, workspace.id, parent.id, [{"command": "beginImplementation"}])
    assert advanced.status == "building"


def test_outline_returns_section_tree(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "A").page
    outline = store.outline(workspace.id, page.id)
    keys = [section["key"] for section in outline["sections"]]
    assert keys[0] == "basics" and "items" in keys
    assert outline["sections"][0]["order"] == 0


def test_render_markdown_page_and_tree(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Page title").page
    _mutate(store, workspace.id, page.id, [
        {"command": "setBody", "args": {"text": "The body."}}
    ])
    md = store.render_markdown(workspace.id, page.id)
    assert "# Page title" in md and "The body." in md
    tree_md = store.render_markdown(workspace.id)            # whole tree
    assert "# demo" in tree_md and "Page title" in tree_md


def test_render_markdown_child_pages_links(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "Parent").page
    child = store.create_page(workspace.id, "test-flow", "Child", parent_id=parent.id).page
    # a single-page render lists the parent's direct child as a titled link
    md = store.render_markdown(workspace.id, parent.id)
    assert "## Child pages" in md
    assert f"- [Child](/{workspace.id}/page/{child.id})" in md
    # and the whole-tree render resolves it too (build_ref_context supplies the titles)
    tree_md = store.render_markdown(workspace.id)
    assert f"- [Child](/{workspace.id}/page/{child.id})" in tree_md


def test_search_ranks_and_resolves_partial_id(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "Concurrency model").page
    _mutate(store, workspace.id, a.id, [
        {"command": "setBody", "args": {"text": "concurrency and locking"}}
    ])
    store.create_page(workspace.id, "test-fields", "Rendering")

    hits = store.search(workspace.id, "concur")["hits"]
    assert hits and hits[0]["pageId"] == a.id               # word-prefix finds "concurrency"
    assert hits[0]["archived"] is False                     # every hit reports its archived state

    token = a.id.rsplit("-", 1)[1]                          # a genuinely PARTIAL id: the suffix
    assert [hit["pageId"] for hit in store.search(workspace.id, f"id:{token}")["hits"]] == [a.id]
    assert store.search(workspace.id, f"id:{a.id}")["hits"][0]["pageId"] == a.id      # full id too
    assert store.search(workspace.id, f"ID:{token.upper()}")["hits"][0]["pageId"] == a.id  # no case

    # a colon-bearing query WITHOUT the id: prefix is ordinary text search, not id resolution
    assert all(hit["pageId"] != a.id for hit in store.search(workspace.id, f"type:{token}")["hits"])

    store.archive_page(workspace.id, a.id)
    assert all(hit["pageId"] != a.id                        # text search still excludes archived
               for hit in store.search(workspace.id, "concur")["hits"])
    archived = store.search(workspace.id, f"id:{token}")["hits"]      # an id search does not
    assert [hit["pageId"] for hit in archived] == [a.id]
    assert archived[0]["archived"] is True


def test_text_search_excludes_descendants_of_an_archived_page(store):
    # Archiving cascades onto PINNED children only, so an ordinary descendant keeps archived=False.
    # The tree hides it anyway (it recurses and stops at the archived ancestor), so text search has
    # to hide it too - otherwise search reports an archived subtree as live work.
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "Parent").page
    child = store.create_page(workspace.id, "test-fields", "Child", parent_id=parent.id).page
    grandchild = store.create_page(workspace.id, "test-fields", "Grandchild", parent_id=child.id).page
    for page in (child, grandchild):
        _mutate(store, workspace.id, page.id, [
            {"command": "setBody", "args": {"text": "zarquon marker"}}])

    found = {hit["pageId"] for hit in store.search(workspace.id, "zarquon")["hits"]}
    assert found == {child.id, grandchild.id}               # both visible while the parent is live

    store.archive_page(workspace.id, parent.id)
    assert store.get_page(workspace.id, child.id).archived is False        # only the ancestor is
    assert store.get_page(workspace.id, grandchild.id).archived is False   # flagged, not these two
    assert store.tree(workspace.id)["nodes"] == []                         # tree hides the subtree
    assert store.search(workspace.id, "zarquon")["hits"] == []             # so search must as well

    id_hits = store.search(workspace.id, f"id:{child.id}")["hits"]   # id: still reaches them
    assert [hit["pageId"] for hit in id_hits] == [child.id]


def test_archive_hides_from_tree_and_blocks_mutation(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "Parent").page
    child = store.create_page(workspace.id, "test-flow", "Child", parent_id=parent.id).page

    store.archive_page(workspace.id, child.id)
    assert store.tree(workspace.id)["nodes"][0]["children"] == []            # hidden by default
    shown = store.tree(workspace.id, include_archived=True)["nodes"][0]["children"]
    assert shown[0]["id"] == child.id and shown[0]["archived"] is True       # flagged when shown

    with pytest.raises(IllegalCommandError):
        _mutate(store, workspace.id, child.id, [
            {"command": "setSummary", "args": {"text": "x"}}
        ])

    store.unarchive_page(workspace.id, child.id)
    assert store.tree(workspace.id)["nodes"][0]["children"][0]["id"] == child.id


def test_archive_hides_whole_subtree(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "Parent").page
    store.create_page(workspace.id, "test-flow", "Child", parent_id=parent.id)
    store.archive_page(workspace.id, parent.id)
    assert store.tree(workspace.id)["nodes"] == []                           # parent + subtree gone


def test_archived_pages_sort_below_siblings_stably(store):
    # At every level the tree shows archived pages below non-archived ones, preserving each group's
    # explicit order (a stable partition, not a full re-sort).
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "Parent").page
    a = store.create_page(workspace.id, "test-flow", "A", parent_id=parent.id).page
    b = store.create_page(workspace.id, "test-flow", "B", parent_id=parent.id).page
    c = store.create_page(workspace.id, "test-flow", "C", parent_id=parent.id).page
    d = store.create_page(workspace.id, "test-flow", "D", parent_id=parent.id).page
    store.archive_page(workspace.id, b.id)
    store.archive_page(workspace.id, d.id)

    children = store.tree(workspace.id, include_archived=True)["nodes"][0]["children"]
    # active A, C keep their order; archived B, D follow, also in their original order.
    assert [n["id"] for n in children] == [a.id, c.id, b.id, d.id]

    # The same stable partition applies to the top-level (root) pages.
    e = store.create_page(workspace.id, "test-fields", "E").page       # active root, created after Parent
    store.archive_page(workspace.id, parent.id)
    roots = store.tree(workspace.id, include_archived=True)["nodes"]
    assert [n["id"] for n in roots] == [e.id, parent.id]               # active E first, archived Parent below


def test_archive_workspace_toggles_status(store):
    workspace = store.create_workspace("demo")
    store.archive_workspace(workspace.id)
    assert {w["id"]: w["status"] for w in store.list_workspaces()}[workspace.id] == "archived"
    store.unarchive_workspace(workspace.id)
    assert {w["id"]: w["status"] for w in store.list_workspaces()}[workspace.id] == "active"


def test_next_actions_partitions_edges(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-lifecycle", "F").page
    # In draft: beginPlanning is blocked (needs summary); abandon is an agent/either `do`. Every `do`
    # edge (transition or field-setter) carries a `commands` array - the singular `command` is gone.
    actions = store.next_actions(workspace.id, parent.id)
    do_commands = {edge["command"] for edge in actions["do"]}
    assert "abandon" in do_commands
    blocked = {edge["command"]: edge for edge in actions["blocked"]}   # blocked keeps singular `command`
    assert "beginPlanning" in blocked and "summary.body" in blocked["beginPlanning"]["reason"]
    # Once the summary is set, beginPlanning becomes a `do`.
    _mutate(store, workspace.id, parent.id, [
        {"command": "setSummary", "args": {"text": "x"}}
    ])
    do_after = store.next_actions(workspace.id, parent.id)["do"]
    assert "beginPlanning" in {edge["command"] for edge in do_after}


def test_next_actions_ship_is_a_gated_human_gate(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "F")
    parent, child = result.page, _child(result, "test-child")
    _mutate(store, workspace.id, parent.id, [
        {"command": "setSummary", "args": {"text": "x"}},
        {"command": "beginPlanning"},                       # unlocks the child's parent-gated markReady
    ])
    _, step_created = _mutate(store, workspace.id, child.id, [
        {"command": "addStep", "args": {"text": "build"}}
    ])
    _mutate(store, workspace.id, child.id, [
        {"command": "markReady"}                            # ready so beginImplementation is unblocked
    ])
    # Address the step so the review gate opens, reach review, then reopen it: ship is now a human
    # gate that the reopened (todo) step blocks.
    _mutate(store, workspace.id, child.id, [
        {"command": "markStepDone", "args": {"stepId": step_created[0]}}
    ])
    # One transition per batch: reach building, then submit for review in a second batch.
    _mutate(store, workspace.id, parent.id, [
        {"command": "addPart", "args": {"name": "R"}},
        {"command": "beginImplementation"},
    ])
    _mutate(store, workspace.id, parent.id, [{"command": "submitForReview"}])
    _mutate(store, workspace.id, child.id, [
        {"command": "markStepTodo", "args": {"stepId": step_created[0]}}
    ])
    gates = {edge["command"]: edge for edge in store.next_actions(workspace.id, parent.id)["humanGates"]}
    assert "ship" in gates and "blockedReason" in gates["ship"]               # blocked by the reopened step


def test_next_actions_do_lists_stage_field_setters_with_shape(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-lifecycle", "F").page
    do = store.next_actions(workspace.id, parent.id)["do"]
    # Every `do` edge carries a singular `command` string and a `kind` - the same shape `blocked`,
    # `humanGates` and `attention` use, so all four rollup lists read alike. The `commands` array is
    # gone: after the block commands collapsed, every edge names exactly one command.
    assert all(isinstance(edge.get("command"), str) and "commands" not in edge for edge in do)
    by_kind: dict[str, list] = {}
    for edge in do:
        by_kind.setdefault(edge["kind"], []).append(edge)
    # The stage-required prose field surfaces as a self-instructing kind='field' edge (section/field/
    # instruction/command inline).
    field_edges = {edge["command"]: edge for edge in by_kind.get("field", [])}
    assert "setSummary" in field_edges
    set_summary = field_edges["setSummary"]
    assert set_summary["section"] == "summary" and set_summary["field"] == "body" and set_summary["instruction"]
    # A transition edge is kind='transition' command=<event> and carries no section/field.
    transitions = {edge["command"]: edge for edge in by_kind.get("transition", [])}
    assert "abandon" in transitions and "section" not in transitions["abandon"]
    # Excluded from `do`: remove/reorder, flag setters, element transitions, addLink/setTitle, and
    # setters whose field is not a requirement of a transition legal in `draft` (addPart, askQuestion).
    do_commands = {edge["command"] for edge in do}
    assert do_commands.isdisjoint({
        "removePart", "reorderPart", "escalateQuestion", "answerQuestion",
        "addLink", "setTitle", "addPart", "askQuestion",
    })


def test_next_actions_withholds_child_field_setters_until_parent_unlocks(store):
    """A pinned child stays silent in the subtree rollup until its parent unlocks its stage.

    The child's markReady is gated on BOTH content (a step) and a ParentStateGuard. While the parent
    is still `draft` no authoring on the child can fire it, so its addStep is withheld from `do` and
    the blocked reason names the parent rather than the unauthored field - the child does not
    advertise work that is not due yet.
    """
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "F")
    parent, child = result.page, _child(result, "test-child")

    actions = store.next_actions(workspace.id, parent.id)
    assert [e for e in actions["do"] if e["pageId"] == child.id and e["kind"] == "field"] == []
    reason = {e["command"]: e["reason"] for e in actions["blocked"] if e["pageId"] == child.id}["markReady"]
    assert "planning or later" in reason and "steps.items" not in reason
    # The PARENT's own stage setter is untouched - only the parent-gated child went quiet.
    assert "setSummary" in {e["command"] for e in actions["do"] if e["pageId"] == parent.id}

    # Reaching `planning` satisfies the guard: the child's addStep appears, and its blocked reason
    # switches to the content that is now genuinely the next thing to author.
    _mutate(store, workspace.id, parent.id, [
        {"command": "setSummary", "args": {"text": "x"}},
        {"command": "beginPlanning"},
    ])
    actions = store.next_actions(workspace.id, parent.id)
    assert "addStep" in {e["command"] for e in actions["do"] if e["pageId"] == child.id}
    reason = {e["command"]: e["reason"] for e in actions["blocked"] if e["pageId"] == child.id}["markReady"]
    assert "steps.items" in reason


def test_next_actions_child_guard_does_not_withhold_field_setters(store):
    """A failing CHILD-state guard must NOT silence the page's own stage setters.

    The mirror of the parent-guard case: `beginImplementation` requires a part AND guards on the
    pinned child being `ready`. With the child unready that transition cannot fire, but authoring
    the part is still exactly what the parent's `planning` stage calls for - 'my children are
    unfinished' does not make my own authoring premature.
    """
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "F")
    parent, child = result.page, _child(result, "test-child")
    _mutate(store, workspace.id, parent.id, [
        {"command": "setSummary", "args": {"text": "x"}},
        {"command": "beginPlanning"},
    ])
    assert store.get_page(workspace.id, child.id).status == "draft"   # the guard's unmet condition
    actions = store.next_actions(workspace.id, parent.id)
    assert "addPart" in {e["command"] for e in actions["do"] if e["pageId"] == parent.id}


def test_next_actions_terminal_state_has_no_field_setters(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-flow", "C").page
    # draft -> open -> closed; `closed` is terminal (authoring is locked there).
    _mutate(store, workspace.id, page.id, [
        {"command": "setSummary", "args": {"text": "s"}}, {"command": "open"},
    ])
    _mutate(store, workspace.id, page.id, [
        {"command": "close", "args": {"sha": "abc", "message": "done"}},
    ])
    do = store.next_actions(workspace.id, page.id)["do"]
    assert all(edge["kind"] != "field" for edge in do)                        # no field setters when terminal
    assert "reopen" in {edge["command"] for edge in do}           # the transition still shows


def test_attention_lists_escalated_open_questions(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-lifecycle", "F").page
    result, created = _mutate(store, workspace.id, parent.id, [
        {"command": "askQuestion", "args": {"text": "?"}}
    ])
    assert store.attention(workspace.id)["attention"] == []                  # not escalated yet
    _mutate(store, workspace.id, parent.id, [
        {"command": "escalateQuestion", "args": {"questionId": created[0]}}
    ])
    attention = store.attention(workspace.id)["attention"]
    assert len(attention) == 1 and attention[0]["itemId"] == created[0] and attention[0]["status"] == "open"
    # answering it (open -> answered) clears it from attention
    _mutate(store, workspace.id, parent.id, [
        {"command": "answerQuestion", "args": {"questionId": created[0], "answer": "yes"}}
    ])
    assert store.attention(workspace.id)["attention"] == []


def test_mutate_page_batch_atomic_commit(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-lifecycle", "A").page
    # setSummary populates the field beginPlanning requires; each command is decided against the prior.
    result, created = _mutate(store, workspace.id, page.id, [
        {"command": "setSummary", "args": {"text": "S"}},
        {"command": "addPart", "args": {"name": "P"}},
        {"command": "beginPlanning"},
    ])
    assert result.status == "planning"
    assert result.sections["summary"]["body"] == "S"
    assert len(result.sections["parts"]["items"]) == 1
    assert created[1] is not None                                            # addPart's id


def test_mutate_page_batch_aborts_and_writes_nothing(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-flow", "A").page
    with pytest.raises(PastaError):                                       # reopen illegal from `draft`
        _mutate(store, workspace.id, page.id, [
            {"command": "setSummary", "args": {"text": "S"}},
            {"command": "reopen"},
        ])
    assert store.get_page(workspace.id, page.id).sections["summary"]["body"] == ""   # rolled back


# --- batch opaque-id chaining (a batch's own not-yet-committed ids are opaque to the anchor guard) -
def _texts(store, workspace_id, page_id):
    return [it["text"] for it in store.get_page(workspace_id, page_id).sections["items"]["items"]]


def _seed_items(store, *texts):
    """A fresh test-fields page with `texts` appended; returns (workspace_id, page_id, [item ids])."""
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "P").page
    _, created = _mutate(store,
        workspace.id, page.id, [{"command": "addItem", "args": {"text": t}} for t in texts]
    )
    return workspace.id, page.id, created


def test_batch_chained_add_reuses_committed_preceding_id(store):
    # The exact case that used to abort: two positioned adds anchored on the SAME committed id. The
    # second reuses id0 because the first insert's id is unknowable when the batch is composed.
    ws, page_id, (id0,) = _seed_items(store, "0")
    result, created = _mutate(store, ws, page_id, [
        {"command": "addItem", "args": {"text": "1", "index": 1, "precedingId": id0}},
        {"command": "addItem", "args": {"text": "2", "index": 2, "precedingId": id0}},
    ])
    assert [it["text"] for it in result.sections["items"]["items"]] == ["0", "1", "2"]
    assert all(cid is not None for cid in created)                       # both ids created
    assert [it["id"] for it in result.sections["items"]["items"]] == [id0, created[0], created[1]]


def test_batch_chained_add_longer_run(store):
    # The skip walks past a run of >1 batch-created ids: three adds, all anchored on id0.
    ws, page_id, (id0,) = _seed_items(store, "0")
    result, _ = _mutate(store, ws, page_id, [
        {"command": "addItem", "args": {"text": "1", "index": 1, "precedingId": id0}},
        {"command": "addItem", "args": {"text": "2", "index": 2, "precedingId": id0}},
        {"command": "addItem", "args": {"text": "3", "index": 3, "precedingId": id0}},
    ])
    assert [it["text"] for it in result.sections["items"]["items"]] == ["0", "1", "2", "3"]


def test_batch_insert_between_two_just_added(store):
    # Q12: index stays strict, so the in-between slot is reachable with the committed anchor.
    ws, page_id, (id0,) = _seed_items(store, "0")
    result, _ = _mutate(store, ws, page_id, [
        {"command": "addItem", "args": {"text": "1", "index": 1, "precedingId": id0}},   # [0, 1]
        {"command": "addItem", "args": {"text": "2", "index": 2, "precedingId": id0}},   # [0, 1, 2]
        {"command": "addItem", "args": {"text": "3", "index": 2, "precedingId": id0}},   # between 1 and 2
    ])
    assert [it["text"] for it in result.sections["items"]["items"]] == ["0", "1", "3", "2"]


def test_batch_reorder_skips_batch_created(store):
    # Q13: a committed entry reorders past a same-batch insert, anchored on a committed id.
    ws, page_id, (a_id, b_id) = _seed_items(store, "A", "B")
    result, _ = _mutate(store, ws, page_id, [
        {"command": "addItem", "args": {"text": "n1", "index": 2, "precedingId": b_id}},          # [A, B, n1]
        {"command": "reorderItem", "args": {"itemId": a_id, "toIndex": 2, "precedingId": b_id}},  # skip n1 -> anchor B
    ])
    assert [it["text"] for it in result.sections["items"]["items"]] == ["B", "n1", "A"]


def test_batch_wrong_preceding_id_still_aborts(store):
    # Strict guard preserved INSIDE a batch: a precedingId that is not the committed id reached by
    # skipping batch-created ids is a genuine stale read -> the whole batch aborts, nothing written.
    ws, page_id, (a_id, b_id) = _seed_items(store, "A", "B")
    with pytest.raises(ConflictError) as exc:
        _mutate(store, ws, page_id, [
            {"command": "addItem", "args": {"text": "n1", "index": 2, "precedingId": b_id}},   # [A, B, n1]
            {"command": "addItem", "args": {"text": "x", "index": 3, "precedingId": a_id}},    # slot-3 anchor is B, not A
        ])
    assert "Batch aborted at command 1" in str(exc.value)
    assert _texts(store, ws, page_id) == ["A", "B"]                     # all-or-nothing intact


def test_single_positioned_add_stays_strict(store):
    # A one-command batch has no batch-created ids, so the guard is byte-for-byte the old strict
    # behaviour: a stale precedingId still raises.
    ws, page_id, (a_id, b_id) = _seed_items(store, "A", "B")
    with pytest.raises(ConflictError):
        _mutate(store, ws, page_id, [
            {"command": "addItem", "args": {"text": "X", "index": 2, "precedingId": a_id}},   # slot-2 predecessor is B
        ])


def test_page_reorder_stays_strict_no_batch_semantics(store):
    # Boundary of Q13: page reorder calls resolve_anchored_slot directly with no batch context,
    # so it never gains the skip; a stale precedingId still raises (mirrors the block/element guard).
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "P").page
    a = store.create_page(workspace.id, "test-flow", "A", parent_id=parent.id).page
    store.create_page(workspace.id, "test-flow", "B", parent_id=parent.id)
    c = store.create_page(workspace.id, "test-flow", "C", parent_id=parent.id).page
    with pytest.raises(ConflictError):
        store.reorder_page(workspace.id, c.id, 2, a.id)    # slot-2 predecessor is B, not A


def test_concurrent_writes_do_not_lose_updates(store):
    """N threads each append one item to the same page; the lock must serialize
    the load-modify-save so all N survive (no lost updates)."""
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Page title").page

    writer_count = 20
    barrier = threading.Barrier(writer_count)
    errors: list[Exception] = []

    def writer(index: int) -> None:
        barrier.wait()  # maximize contention: release all writers together
        try:
            _mutate(store, workspace.id, page.id, [
                {"command": "addItem", "args": {"text": f"item-{index}"}}
            ])
        except Exception as exc:  # noqa: BLE001 - record and assert later
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(writer_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"concurrent writers raised: {errors}"
    final = store.get_page(workspace.id, page.id)
    assert len(final.sections["items"]["items"]) == writer_count


def test_reader_waits_for_the_destination_copy(store, monkeypatch):
    """A read arriving mid-replace waits for it, rather than reading the old file."""
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Page title").page

    replace_started = threading.Event()
    finish_replace = threading.Event()
    real_replace = os.replace

    def slow_replace(self, target, **kwargs):
        replace_started.set()
        assert finish_replace.wait(timeout=5)
        return real_replace(self, target, **kwargs)

    monkeypatch.setattr(os, "replace", slow_replace)

    read_done = threading.Event()
    writer = threading.Thread(target=lambda: _mutate(store,
        workspace.id, page.id, [{"command": "addItem", "args": {"text": "one"}}]))
    reader = threading.Thread(target=lambda: (store.load_workspace(workspace.id), read_done.set()))

    writer.start()
    assert replace_started.wait(timeout=5)
    reader.start()
    assert not read_done.wait(timeout=0.2)   # held out while the destination is mid-replace
    finish_replace.set()
    assert read_done.wait(timeout=5)         # let through once the copy has returned
    writer.join(timeout=5)
    reader.join(timeout=5)


def test_backup_write_and_live_read_share_one_lock(store, monkeypatch):
    """One workspace, one lock: a backup write excludes a read of its live file."""
    workspace = store.create_workspace("demo")
    store.create_page(workspace.id, "test-fields", "Page title")
    loaded = store.load_workspace(workspace.id)

    replace_started = threading.Event()
    finish_replace = threading.Event()
    real_replace = os.replace

    def slow_replace(self, target, **kwargs):
        replace_started.set()
        assert finish_replace.wait(timeout=5)
        return real_replace(self, target, **kwargs)

    monkeypatch.setattr(os, "replace", slow_replace)

    read_done = threading.Event()
    backup = threading.Thread(target=lambda: store.write_backup(
        loaded, datetime(2026, 1, 1, tzinfo=timezone.utc)))
    reader = threading.Thread(target=lambda: (store.load_workspace(workspace.id), read_done.set()))

    backup.start()
    assert replace_started.wait(timeout=5)
    reader.start()
    assert not read_done.wait(timeout=0.2)   # the backup holds this workspace's lock
    finish_replace.set()
    assert read_done.wait(timeout=5)         # and releases it when the copy returns
    backup.join(timeout=5)
    reader.join(timeout=5)


def test_readers_never_see_a_partially_written_file(store):
    """Readers hammering a workspace while writers mutate it never see a partial file.

    The workspace is padded so the copy is slow enough for an unlocked reader to land
    inside it."""
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Page title").page
    for index in range(300):                       # pad the file so a copy is not instantaneous
        _mutate(store, workspace.id, page.id, [
            {"command": "addItem", "args": {"text": f"padding-{index}-" + "x" * 200}}
        ])

    errors: list[Exception] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                _ = store.load_workspace(workspace.id)
            except Exception as exc:               # noqa: BLE001 - recorded and asserted below
                errors.append(exc)

    def writer() -> None:
        for index in range(20):
            _mutate(store, workspace.id, page.id, [
                {"command": "addItem", "args": {"text": f"item-{index}"}}
            ])

    readers = [threading.Thread(target=reader) for _ in range(4)]
    writers = [threading.Thread(target=writer) for _ in range(2)]
    for thread in readers + writers:
        thread.start()
    for thread in writers:
        thread.join()
    stop.set()
    for thread in readers:
        thread.join()

    assert not errors, f"readers saw a partial file: {errors[:3]}"


# --- page-tree structure (reparent / reorder) --------------------------------
def _child_ids(store, workspace_id, parent_id):
    return store.load_workspace(workspace_id).pages[parent_id].child_ids


def test_reparent_moves_page_between_parents(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    b = store.create_page(workspace.id, "test-fields", "B").page
    child = store.create_page(workspace.id, "test-flow", "C", parent_id=a.id).page

    moved, siblings = store.reparent_page(workspace.id, child.id, b.id)
    assert moved.parent_id == b.id and siblings == [child.id]
    assert _child_ids(store, workspace.id, a.id) == []          # gone from A
    assert _child_ids(store, workspace.id, b.id) == [child.id]  # present under B


def test_reparent_to_top_level(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    child = store.create_page(workspace.id, "test-flow", "C", parent_id=a.id).page
    moved, _ = store.reparent_page(workspace.id, child.id, None)
    assert moved.parent_id is None
    reloaded = store.load_workspace(workspace.id)
    assert child.id in reloaded.root_page_ids and reloaded.pages[a.id].child_ids == []


def test_reparent_rejects_cycle_and_missing_parent(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    child = store.create_page(workspace.id, "test-flow", "C", parent_id=a.id).page
    grandchild = store.create_page(workspace.id, "test-flow", "G", parent_id=child.id).page
    with pytest.raises(ConflictError):                          # under itself
        store.reparent_page(workspace.id, a.id, a.id)
    with pytest.raises(ConflictError):                          # under a descendant
        store.reparent_page(workspace.id, a.id, grandchild.id)
    with pytest.raises(NotFoundError):                          # unknown new parent
        store.reparent_page(workspace.id, child.id, "test-fields:missing")


def test_reparent_allows_duplicate_sibling_title(store):
    # Sibling titles are not reserved: reparenting under a parent that already has a same-titled
    # child is allowed (the two just share a display label).
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    b = store.create_page(workspace.id, "test-fields", "B").page
    store.create_page(workspace.id, "test-flow", "Dup", parent_id=b.id)
    dup_under_a = store.create_page(workspace.id, "test-flow", "Dup", parent_id=a.id).page
    moved, siblings = store.reparent_page(workspace.id, dup_under_a.id, b.id)   # B already has a "Dup"
    assert moved.parent_id == b.id and siblings[-1] == dup_under_a.id
    assert [store.get_page(workspace.id, cid).title for cid in siblings] == ["Dup", "Dup"]
    # A same-parent reparent still works (moves to end).
    x = store.create_page(workspace.id, "test-flow", "X", parent_id=a.id).page
    moved, siblings = store.reparent_page(workspace.id, x.id, a.id)
    assert moved.parent_id == a.id and siblings[-1] == x.id


def test_reorder_page_moves_among_siblings(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "P").page
    a = store.create_page(workspace.id, "test-flow", "A", parent_id=parent.id).page
    b = store.create_page(workspace.id, "test-flow", "B", parent_id=parent.id).page
    c = store.create_page(workspace.id, "test-flow", "C", parent_id=parent.id).page
    # Move C between A and B -> after removing C the list is [A, B]; slot 1's predecessor is A.
    _, siblings = store.reorder_page(workspace.id, c.id, 1, a.id)
    assert siblings == [a.id, c.id, b.id]


def test_reorder_page_front_and_stale_guard(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "P").page
    a = store.create_page(workspace.id, "test-flow", "A", parent_id=parent.id).page
    b = store.create_page(workspace.id, "test-flow", "B", parent_id=parent.id).page
    c = store.create_page(workspace.id, "test-flow", "C", parent_id=parent.id).page
    _, siblings = store.reorder_page(workspace.id, c.id, 0, None)   # front
    assert siblings == [c.id, a.id, b.id]
    with pytest.raises(ConflictError):                             # slot-1 predecessor is A, not B
        store.reorder_page(workspace.id, c.id, 1, b.id)


def test_reorder_top_level_pages(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    b = store.create_page(workspace.id, "test-fields", "B").page
    c = store.create_page(workspace.id, "test-fields", "C").page
    _, roots = store.reorder_page(workspace.id, a.id, 2, c.id)     # move A to the end -> [B, C, A]
    assert roots == [b.id, c.id, a.id]


def test_reorder_page_to_current_position_is_a_noop(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "P").page
    a = store.create_page(workspace.id, "test-flow", "A", parent_id=parent.id).page
    b = store.create_page(workspace.id, "test-flow", "B", parent_id=parent.id).page
    _, siblings = store.reorder_page(workspace.id, b.id, 1, a.id)   # B stays after A
    assert siblings == [a.id, b.id]


def test_rename_page_changes_title(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Old").page
    renamed = store.rename_page(workspace.id, page.id, "New")
    assert renamed.title == "New"
    assert store.get_page(workspace.id, page.id).title == "New"   # persisted


def test_rename_allows_duplicate_sibling_title(store):
    # Sibling titles are not reserved, so a rename may collide a page's title with a sibling's.
    workspace = store.create_workspace("demo")
    store.create_page(workspace.id, "test-fields", "Same")
    other = store.create_page(workspace.id, "test-flow", "Other").page
    renamed = store.rename_page(workspace.id, other.id, "Same")   # no ConflictError
    assert renamed.title == "Same"
    titles = [store.get_page(workspace.id, pid).title
              for pid in store.load_workspace(workspace.id).root_page_ids]
    assert titles == ["Same", "Same"]


def test_rename_rejects_blank_title(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Old").page
    with pytest.raises(ValidationError):
        store.rename_page(workspace.id, page.id, "   ")


def test_rename_missing_page_rejected(store):
    workspace = store.create_workspace("demo")
    with pytest.raises(NotFoundError):
        store.rename_page(workspace.id, "test-fields:missing", "New")


def test_set_title_command_renames_via_batch(store):
    # The setTitle page command is the batch-surface alias for rename_page: it changes the title and
    # persists, and rejects a blank title (aborting the whole batch, so nothing is written).
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Old").page
    renamed, _ = _mutate(store, workspace.id, page.id, [
        {"command": "setTitle", "args": {"title": "New"}}
    ])
    assert renamed.title == "New"
    assert store.get_page(workspace.id, page.id).title == "New"   # persisted
    with pytest.raises(ValidationError):
        _mutate(store, workspace.id, page.id, [
            {"command": "setTitle", "args": {"title": "   "}}
        ])
    assert store.get_page(workspace.id, page.id).title == "New"   # blank rejected, title unchanged


# --- page reference graph (link / unlink) ------------------------------------
def test_link_and_unlink_page(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    b = store.create_page(workspace.id, "test-flow", "B").page
    _, links = store.link_page(workspace.id, a.id, b.id, "depends-on")
    assert links == [{"to": b.id, "role": "depends-on"}]
    assert store.get_page(workspace.id, a.id).links == [{"to": b.id, "role": "depends-on"}]  # persisted
    _, links = store.unlink_page(workspace.id, a.id, b.id, "depends-on")
    assert links == []
    assert store.get_page(workspace.id, a.id).links == []


def test_link_rejections(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    b = store.create_page(workspace.id, "test-flow", "B").page
    with pytest.raises(NotFoundError):                       # missing target
        store.link_page(workspace.id, a.id, "test-fields:missing", "rel")
    with pytest.raises(NotFoundError):                       # missing source
        store.link_page(workspace.id, "test-fields:missing", b.id, "rel")
    with pytest.raises(ValidationError):                     # self-link
        store.link_page(workspace.id, a.id, a.id, "rel")
    with pytest.raises(ValidationError):                     # empty role
        store.link_page(workspace.id, a.id, b.id, "   ")
    store.link_page(workspace.id, a.id, b.id, "rel")
    with pytest.raises(ConflictError):                       # duplicate (to, role)
        store.link_page(workspace.id, a.id, b.id, "rel")


def test_link_rejects_archived_source_but_allows_archived_target(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    b = store.create_page(workspace.id, "test-flow", "B").page
    store.archive_page(workspace.id, b.id)                   # target may be archived
    _, links = store.link_page(workspace.id, a.id, b.id, "depends-on")
    assert links == [{"to": b.id, "role": "depends-on"}]
    store.archive_page(workspace.id, a.id)                   # source archived -> cannot link from it
    with pytest.raises(IllegalCommandError):
        store.link_page(workspace.id, a.id, b.id, "another")


def test_add_link_command_matches_link_tool(store):
    # The addLink page command adds the same edge as the top-level link tool, via a batch.
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    b = store.create_page(workspace.id, "test-flow", "B").page
    _mutate(store, workspace.id, a.id,
                            [{"command": "addLink", "args": {"toId": b.id, "role": "depends-on"}}])
    assert store.get_page(workspace.id, a.id).links == [{"to": b.id, "role": "depends-on"}]


def test_add_link_command_enforces_link_rules_and_is_atomic(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    b = store.create_page(workspace.id, "test-flow", "B").page
    with pytest.raises(NotFoundError):                       # missing target
        _mutate(store, workspace.id, a.id,
                                [{"command": "addLink", "args": {"toId": "test-flow:missing", "role": "rel"}}])
    with pytest.raises(ValidationError):                     # self-link
        _mutate(store, workspace.id, a.id,
                                [{"command": "addLink", "args": {"toId": a.id, "role": "rel"}}])
    # A duplicate (to, role) WITHIN one batch is caught against the working copy, and the all-or-nothing
    # batch means nothing is persisted - proving _check_link sees links accrued earlier in the batch.
    with pytest.raises(ConflictError):
        _mutate(store, workspace.id, a.id, [
            {"command": "addLink", "args": {"toId": b.id, "role": "rel"}},
            {"command": "addLink", "args": {"toId": b.id, "role": "rel"}}])
    assert store.get_page(workspace.id, a.id).links == []   # atomic: the aborted batch wrote nothing


def test_unlink_missing_edge_rejected(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    b = store.create_page(workspace.id, "test-flow", "B").page
    with pytest.raises(NotFoundError):
        store.unlink_page(workspace.id, a.id, b.id, "nope")


def test_content_mutation_preserves_links(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    b = store.create_page(workspace.id, "test-flow", "B").page
    store.link_page(workspace.id, a.id, b.id, "depends-on")
    _mutate(store, workspace.id, a.id, [
        {"command": "setBody", "args": {"text": "hi"}}   # a content mutation of A
    ])
    assert store.get_page(workspace.id, a.id).links == [{"to": b.id, "role": "depends-on"}]


def test_render_markdown_shows_references_before_child_pages(store):
    workspace = store.create_workspace("demo")
    a = store.create_page(workspace.id, "test-fields", "A").page
    target = store.create_page(workspace.id, "test-flow", "Target").page
    store.link_page(workspace.id, a.id, target.id, "depends-on")
    md = store.render_markdown(workspace.id, a.id)
    assert "## References" in md
    assert f"- [Target](/{workspace.id}/page/{target.id}) *test-flow* · `draft` - depends-on" in md
    assert md.index("## References") < md.index("## Child pages")   # References before Child pages


# --- pinned auto-created children (protected from structural ops) -------------
def test_pinned_child_cannot_be_reparented(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Feat")
    child = _child(result, "test-child")
    other = store.create_page(workspace.id, "test-fields", "Other").page
    with pytest.raises(IllegalCommandError):
        store.reparent_page(workspace.id, child.id, other.id)
    with pytest.raises(IllegalCommandError):
        store.reparent_page(workspace.id, child.id, None)          # even to top level


def test_pinned_child_cannot_be_reordered(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Feat")
    child = _child(result, "test-child")
    with pytest.raises(IllegalCommandError):
        store.reorder_page(workspace.id, child.id, 0, None)


def test_pinned_child_cannot_be_archived_or_unarchived_individually(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Feat")
    child = _child(result, "test-child")
    with pytest.raises(IllegalCommandError):
        store.archive_page(workspace.id, child.id)
    with pytest.raises(IllegalCommandError):
        store.unarchive_page(workspace.id, child.id)


def test_archiving_parent_cascades_to_pinned_children(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Feat")
    parent_id = result.page.id
    child_ids = [c.id for c in result.children]
    store.archive_page(workspace.id, parent_id)
    reloaded = store.load_workspace(workspace.id)
    assert reloaded.pages[parent_id].archived is True
    assert all(reloaded.pages[cid].archived is True for cid in child_ids)    # cascaded onto pinned children
    store.unarchive_page(workspace.id, parent_id)
    reloaded = store.load_workspace(workspace.id)
    assert reloaded.pages[parent_id].archived is False
    assert all(reloaded.pages[cid].archived is False for cid in child_ids)   # restored with the parent


def test_non_pinned_child_is_still_mutable(store):
    workspace = store.create_workspace("demo")
    parent_id = store.create_page(workspace.id, "test-lifecycle", "Feat").page.id
    # a manually-created child of the parent (NOT an auto-child type) is unprotected
    manual = store.create_page(workspace.id, "test-fields", "Notes", parent_id=parent_id).page
    other = store.create_page(workspace.id, "test-fields", "Elsewhere").page
    store.reparent_page(workspace.id, manual.id, other.id)          # allowed
    store.archive_page(workspace.id, manual.id)                     # allowed
    assert store.get_page(workspace.id, manual.id).archived is True


def test_lifecycle_still_auto_creates_child_from_specs(store):
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Feat")
    assert sorted(c.type for c in result.children) == ["test-child"]


# --- cleanup sweep: backup, stamp, prune -------------------------------------

NOW = datetime(2026, 8, 13, 9, 5, tzinfo=timezone.utc)
AFTER_EXPIRY = datetime(2026, 8, 19, 9, 5, tzinfo=timezone.utc)


def test_backup_writes_under_a_subdirectory_and_is_not_listed_as_a_workspace(store, tmp_path):
    workspace = store.create_workspace("demo")
    store.create_page(workspace.id, "test-fields", "Keep me")

    path = store.write_backup(store.load_workspace(workspace.id),
                              datetime(2026, 8, 13, 12, 5, 0, tzinfo=timezone.utc))

    token = workspace.id.replace(":", "_")
    assert path == tmp_path / "backups" / token / "20260813T120500Z.json"
    assert path.exists()
    assert ":" not in path.name                       # Windows-legal
    assert len(store.list_workspaces()) == 1          # the "*.json" glob must not see it


def test_backup_is_a_loadable_workspace_document(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Keep me").page

    path = store.write_backup(store.load_workspace(workspace.id),
                              datetime(2026, 8, 13, 12, 5, tzinfo=timezone.utc))

    restored = workspace_from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert restored.id == workspace.id
    assert restored.pages[page.id].title == "Keep me"


def test_cleanup_stamps_an_archived_page_but_does_not_delete_it(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Bye").page
    store.archive_page(workspace.id, page.id)

    report = store.cleanup_workspace(workspace.id, NOW)

    assert report.stamped == 1 and report.pruned == [] and report.backup is None
    assert store.get_page(workspace.id, page.id).expires_at == "2026-08-18T12:00:00+00:00"


def test_cleanup_prunes_only_after_the_expiry_passes(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Bye").page
    store.archive_page(workspace.id, page.id)
    store.cleanup_workspace(workspace.id, NOW)                  # stamps for 2026-08-18

    store.cleanup_workspace(workspace.id, NOW)                  # same hour: still no-op
    assert store.get_page(workspace.id, page.id) is not None

    report = store.cleanup_workspace(workspace.id, AFTER_EXPIRY)
    assert report.pruned == [page.id]
    with pytest.raises(NotFoundError):
        store.get_page(workspace.id, page.id)


def test_cleanup_writes_a_backup_before_pruning(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Bye").page
    store.archive_page(workspace.id, page.id)
    store.cleanup_workspace(workspace.id, NOW)

    report = store.cleanup_workspace(workspace.id, AFTER_EXPIRY)

    assert report.backup is not None
    # The backup must still hold the page that this very run deleted.
    saved = json.loads(Path(report.backup).read_text(encoding="utf-8"))
    assert page.id in saved["pages"]


def test_cleanup_clears_the_stamp_when_a_page_is_unarchived(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Back").page
    store.archive_page(workspace.id, page.id)
    store.cleanup_workspace(workspace.id, NOW)
    store.unarchive_page(workspace.id, page.id)

    report = store.cleanup_workspace(workspace.id, NOW)

    assert report.cleared == 1
    assert store.get_page(workspace.id, page.id).expires_at is None


def test_cleanup_prunes_an_archived_parent_with_its_shadowed_children(store):
    workspace = store.create_workspace("demo")
    parent = store.create_page(workspace.id, "test-fields", "Parent").page
    child = store.create_page(workspace.id, "test-fields", "Child", parent_id=parent.id).page
    store.archive_page(workspace.id, parent.id)
    # The child is not flagged archived - it is hidden only because its parent is.
    assert store.get_page(workspace.id, child.id).archived is False

    store.cleanup_workspace(workspace.id, NOW)
    report = store.cleanup_workspace(workspace.id, AFTER_EXPIRY)

    assert sorted(report.pruned) == sorted([parent.id, child.id])
    assert store.load_workspace(workspace.id).root_page_ids == []


def test_cleanup_rejects_a_naive_datetime(store):
    # Comparing naive vs aware raises deep inside classify with a useless message;
    # fail fast at the boundary instead.
    workspace = store.create_workspace("demo")
    with pytest.raises(ValidationError):
        store.cleanup_workspace(workspace.id, datetime(2026, 8, 13, 9, 5))


def test_cleanup_aborts_without_deleting_when_the_backup_cannot_be_written(store, monkeypatch):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Bye").page
    store.archive_page(workspace.id, page.id)
    store.cleanup_workspace(workspace.id, NOW)

    def boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(Store, "write_backup", boom)

    report = store.cleanup_workspace(workspace.id, AFTER_EXPIRY)

    assert report.error is not None and "disk full" in report.error
    assert report.pruned == [] and report.stamped == 0
    # Page and stamp must both be exactly as they were - no half-applied pass.
    assert store.get_page(workspace.id, page.id).expires_at == "2026-08-18T12:00:00+00:00"


def test_cleanup_leaves_an_archived_workspace_alone(store):
    # archiveWorkspace is a listing flag, not a deletion request: its pages are
    # individually unarchived, so they stay findable and nothing is stamped.
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Still here").page
    store.archive_workspace(workspace.id)

    report = store.cleanup_workspace(workspace.id, NOW)

    assert report.stamped == 0 and report.pruned == []
    assert store.get_page(workspace.id, page.id).expires_at is None


def test_render_html_returns_structured_html_for_one_page(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "Page title").page
    _mutate(store, workspace.id, page.id, [
        {"command": "addItem", "args": {"text": "Item one", "note": "a note"}},
    ])
    out = store.render_html(workspace.id, page.id)
    assert '<article class="pasta-page">' in out
    assert '<h1 class="page-title">Page title</h1>' in out
    assert "<dt>text</dt><dd><p>Item one</p></dd>" in out


def test_render_html_rejects_an_unknown_page(store):
    workspace = store.create_workspace("demo")
    with pytest.raises(NotFoundError):
        store.render_html(workspace.id, "test-fields:nope")


# --- block-bearing element fields --------------------------------------------
def _page_with_one_item(store, workspace_id):
    """A test-element-blocks page holding one item, and that item's id."""
    page = store.create_page(workspace_id, "test-element-blocks", "Plan").page
    _, created = _mutate(store, workspace_id, page.id, [
        {"command": "addItem", "args": {"text": "one"}},
    ])
    return page, created[0]


def _detail(store, workspace_id, page_id):
    return store.get_page(workspace_id, page_id).sections["items"]["items"][0]["detail"]


def test_one_batch_authors_two_complete_elements(store):
    """The shape the do edge advertises, run as one batch with no read-back in the middle."""
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-element-blocks", "Plan").page
    _, created = _mutate(store, workspace.id, page.id, [
        {"command": "addItem", "args": {"text": "one", "detail": [
            {"kind": "paragraph", "inlines": ["first"]},
            {"kind": "code", "language": "python", "source": "x = 1"}]}},
        {"command": "addItem", "args": {"text": "two", "detail": [
            {"kind": "paragraph", "inlines": ["second"]}]}},
    ])
    items = store.get_page(workspace.id, page.id).sections["items"]["items"]
    assert created == [item["id"] for item in items]         # the two ELEMENT ids, in order
    assert [block["kind"] for block in items[0]["detail"]] == ["paragraph", "code"]
    assert [block["kind"] for block in items[1]["detail"]] == ["paragraph"]
    assert items[1]["detail"][0]["inlines"] == ["second"]


def test_a_dangling_ref_in_a_created_element_block_aborts_the_batch(store):
    """collect_ref_ids reaches into a block created with its element, so the store's existing
    precheck still sees it - without that branch the ref would be written dangling."""
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-element-blocks", "Plan").page
    with pytest.raises(ValidationError, match="inline reference"):
        _mutate(store, workspace.id, page.id, [
            {"command": "addItem", "args": {"text": "one", "detail": [
                {"kind": "paragraph", "inlines": [{"ref": "test-fields:nope"}]}]}},
        ])
    assert store.get_page(workspace.id, page.id).sections["items"]["items"] == []


def test_a_dangling_ref_in_an_element_block_aborts_the_batch(store):
    """The store needs no change for the new commands: _check_inline_refs walks every arg by its
    declared content shape, so an element-scoped paragraph is checked like a page-level one."""
    workspace = store.create_workspace("demo")
    page, item_id = _page_with_one_item(store, workspace.id)
    with pytest.raises(ValidationError, match="inline reference"):
        _mutate(store, workspace.id, page.id, [
            {"command": "addItemDetail", "args": {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": ["ok"]}]}},
            {"command": "addItemDetail",
             "args": {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": [{"ref": "test-fields:nope"}]}]}},
        ])
    # All-or-nothing: neither paragraph was written.
    assert _detail(store, workspace.id, page.id) == []
    # A ref to a real page is accepted and stored verbatim.
    target = store.create_page(workspace.id, "test-fields", "Target").page
    _mutate(store, workspace.id, page.id, [
        {"command": "addItemDetail",
         "args": {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": ["see ", {"ref": target.id}]}]}},
    ])
    assert _detail(store, workspace.id, page.id)[0]["inlines"] == ["see ", {"ref": target.id}]


def test_element_block_inserts_compose_within_one_batch(store):
    """Two positioned inserts anchored on the SAME committed id compose in command order: the
    batch guard walks left past the id the batch itself just created."""
    workspace = store.create_workspace("demo")
    page, item_id = _page_with_one_item(store, workspace.id)
    _, first = _mutate(store, workspace.id, page.id, [
        {"command": "addItemDetail", "args": {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": ["anchor"]}]}},
    ])
    anchor = first[0]
    _, created = _mutate(store, workspace.id, page.id, [
        {"command": "addItemDetail",
         "args": {"itemId": item_id, "blocks": [{"kind": "code", "language": "python", "source": "x = 1"}], "index": 1, "precedingId": anchor}},
        {"command": "addItemDetail",
         "args": {"itemId": item_id, "blocks": [{"kind": "paragraph", "inlines": ["second"]}], "index": 1, "precedingId": anchor}},
    ])
    code_id, paragraph_id = created
    assert [block["id"] for block in _detail(store, workspace.id, page.id)] == [
        anchor, paragraph_id, code_id
    ]


def test_a_multi_block_add_composes_within_one_batch(store):
    """Every id a command creates enters the batch's anchored-slot context, not just the reported one.

    A block add returns the first of the run positionally, so if mutate_page_batch tracked only
    result.created_id the guard could not skip the rest - and a later positioned insert anchored
    on a committed block would be rejected as a stale read.
    """
    workspace = store.create_workspace("demo")
    doc = store.create_page(workspace.id, "test-blocks", "Doc").page
    page, _ = _mutate(store, workspace.id, doc.id, [
        {"command": "addBody", "args": {"blocks": [{"kind": "paragraph", "inlines": ["anchor"]}]}}
    ])
    anchor = page.sections["body"]["body"][0]["id"]
    # One batch: a run of three blocks, then an insert anchored on the committed block. The three
    # ids the first command created are not nameable by the caller, so the guard must skip them.
    page, _ = _mutate(store, workspace.id, doc.id, [
        {"command": "addBody", "args": {"blocks": [
            {"kind": "paragraph", "inlines": ["a"]},
            {"kind": "paragraph", "inlines": ["b"]},
            {"kind": "paragraph", "inlines": ["c"]},
        ]}},
        {"command": "addBody", "args": {
            "blocks": [{"kind": "paragraph", "inlines": ["d"]}],
            "index": 1, "precedingId": anchor}},
    ])
    body = page.sections["body"]["body"]
    assert [block["inlines"][0] for block in body] == ["anchor", "d", "a", "b", "c"]


def test_createdids_stays_one_id_per_command(store):
    """The response contract is unchanged: one id per command, positionally - a block add that
    creates a run reports the first, exactly as an element add reports the element."""
    workspace = store.create_workspace("demo")
    doc = store.create_page(workspace.id, "test-blocks", "Doc").page
    page, created = _mutate(store, workspace.id, doc.id, [
        {"command": "addBody", "args": {"blocks": [
            {"kind": "paragraph", "inlines": ["a"]},
            {"kind": "paragraph", "inlines": ["b"]},
        ]}},
        {"command": "addBody", "args": {"blocks": [{"kind": "divider"}]}},
    ])
    assert len(created) == 2
    body = page.sections["body"]["body"]
    assert created[0] == body[0]["id"]      # the first of the run
    assert created[1] == body[2]["id"]


def test_a_ref_checked_block_is_checked_when_created_with_its_element(store):
    """Integrity the command-level check could never give.

    store._check_ref reads args[ref.arg] as one scalar, so a questionId buried in an array entry
    was invisible to it. With the ref check on the kind, a block created together with its
    element is checked too - this case fails on the per-kind surface.
    """
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Dark mode")
    parent, child = result.page, _child(result, "test-child")
    # A dangling questionId inside a block argument aborts the whole commit.
    with pytest.raises(ValidationError, match="does not reference an existing element"):
        _mutate(store, workspace.id, child.id, [
            {"command": "addDecisions", "args": {"blocks": [
                {"kind": "decision", "questionId": "nope", "text": "x"}]}}
        ])
    assert store.get_page(workspace.id, child.id).sections["decisions"]["body"] == []
    # The same block resolves once the parent has the question.
    _, asked = _mutate(store, workspace.id, parent.id, [
        {"command": "askQuestion", "args": {"text": "contrast?"}}
    ])
    page, _ = _mutate(store, workspace.id, child.id, [
        {"command": "addDecisions", "args": {"blocks": [
            {"kind": "decision", "questionId": asked[0], "text": "WCAG AA"}]}}
    ])
    assert page.sections["decisions"]["body"][0]["questionId"] == asked[0]


def test_a_dangling_ref_in_a_later_add_aborts_the_whole_batch(store):
    """A block already written by an earlier command in the batch is rolled back with it - the
    per-block check runs before anything commits."""
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Dark mode")
    parent, child = result.page, _child(result, "test-child")
    _, asked = _mutate(store, workspace.id, parent.id, [
        {"command": "askQuestion", "args": {"text": "contrast?"}}
    ])
    with pytest.raises(ValidationError, match="does not reference an existing element"):
        _mutate(store, workspace.id, child.id, [
            {"command": "addDecisions", "args": {"blocks": [
                {"kind": "decision", "questionId": asked[0], "text": "first"}]}},
            {"command": "addDecisions", "args": {"blocks": [
                {"kind": "decision", "questionId": "nope", "text": "second"}]}},
        ])
    # Nothing was written, not even the first command's block.
    assert store.get_page(workspace.id, child.id).sections["decisions"]["body"] == []


def test_a_ref_checked_block_created_with_its_element_is_checked(store):
    """The strictly-new integrity, and the case that fails on the per-kind surface.

    addStep(note=[...]) creates the element and its blocks in one command, so the questionId
    lives inside an array entry. store._check_ref reads args[ref.arg] as one scalar and cannot
    see it; _check_block_refs walks the block argument and resolves the ref carried by the kind.
    """
    workspace = store.create_workspace("demo")
    result = store.create_page(workspace.id, "test-lifecycle", "Dark mode")
    parent, child = result.page, _child(result, "test-child")
    with pytest.raises(ValidationError, match="does not reference an existing element"):
        _mutate(store, workspace.id, child.id, [
            {"command": "addStep", "args": {"text": "one", "note": [
                {"kind": "decision", "questionId": "nope", "text": "x"}]}}
        ])
    assert store.get_page(workspace.id, child.id).sections["steps"]["items"] == []
    # The same element commits once the parent carries the question.
    _, asked = _mutate(store, workspace.id, parent.id, [
        {"command": "askQuestion", "args": {"text": "contrast?"}}
    ])
    page, _ = _mutate(store, workspace.id, child.id, [
        {"command": "addStep", "args": {"text": "one", "note": [
            {"kind": "decision", "questionId": asked[0], "text": "WCAG AA"}]}}
    ])
    step = page.sections["steps"]["items"][0]
    assert step["note"][0]["questionId"] == asked[0]
    # And the element-scoped add gets the same check.
    with pytest.raises(ValidationError, match="does not reference an existing element"):
        _mutate(store, workspace.id, child.id, [
            {"command": "addStepNote", "args": {"stepId": step["id"], "blocks": [
                {"kind": "decision", "questionId": "nope", "text": "x"}]}}
        ])


# --- status_revision_token: the per-command optimistic-concurrency stamp ---------
@pytest.fixture
def revstore(tmp_path):
    """A store whose revision factory is a deterministic 6-digit counter, so tokens are
    predictable in assertions: the first page created gets '000001', the next '000002', ..."""
    seq = iter(range(1, 100_000))
    return Store(tmp_path, revision_factory=lambda: f"{next(seq):06d}")


def test_create_assigns_a_six_digit_revision(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-fields", "P").page
    token = page.status_revision_token
    assert isinstance(token, str) and len(token) == 6 and token.isdigit()
    assert store.get_page(workspace.id, page.id).status_revision_token == token   # persisted + surfaced


def test_create_and_auto_children_each_get_a_fresh_revision(revstore):
    workspace = revstore.create_workspace("demo")
    result = revstore.create_page(workspace.id, "test-lifecycle", "F")
    # parent first, then its pinned child - each drawn from the factory in creation order.
    assert result.page.status_revision_token == "000001"
    assert _child(result, "test-child").status_revision_token == "000002"


def test_wrong_revision_token_aborts_and_writes_nothing(revstore):
    workspace = revstore.create_workspace("demo")
    page = revstore.create_page(workspace.id, "test-fields", "P").page      # token "000001"
    with pytest.raises(ConflictError, match="does not match"):
        revstore.mutate_page_batch(workspace.id, page.id, [
            {"command": "addItem", "args": {"statusRevisionToken": "wrong", "text": "x"}}])
    assert revstore.get_page(workspace.id, page.id).sections["items"]["items"] == []


def test_token_is_read_from_args_and_stripped_before_the_command(revstore):
    workspace = revstore.create_workspace("demo")
    page = revstore.create_page(workspace.id, "test-fields", "P").page      # token "000001"
    token = page.status_revision_token
    # The token rides inside args and is consumed by the store, so the command applies cleanly
    # rather than the pure core rejecting an unknown argument.
    after, _ = revstore.mutate_page_batch(workspace.id, page.id, [
        {"command": "addItem", "args": {"statusRevisionToken": token, "text": "x"}}])
    assert [item["text"] for item in after.sections["items"]["items"]] == ["x"]
    # A token left in the old sibling position is no longer read, so it reads as a missing token.
    with pytest.raises(ConflictError, match="does not match"):
        revstore.mutate_page_batch(workspace.id, page.id, [
            {"command": "addItem", "args": {"text": "y"}, "statusRevisionToken": token}])


def test_missing_token_is_rejected_when_the_page_has_one(revstore):
    workspace = revstore.create_workspace("demo")
    page = revstore.create_page(workspace.id, "test-fields", "P").page
    with pytest.raises(ConflictError, match="does not match"):
        revstore.mutate_page_batch(workspace.id, page.id, [{"command": "addItem", "args": {"text": "x"}}])


def test_content_keeps_the_token_and_a_transition_regenerates_it(revstore):
    workspace = revstore.create_workspace("demo")
    page = revstore.create_page(workspace.id, "test-flow", "C").page        # token "000001"
    token = page.status_revision_token
    # a content command does not move the status, so it leaves the token unchanged
    after, _ = revstore.mutate_page_batch(workspace.id, page.id, [
        {"command": "setSummary", "args": {"statusRevisionToken": token, "text": "s"}}])
    assert after.status_revision_token == token
    # a status transition regenerates the token to a fresh value
    opened, _ = revstore.mutate_page_batch(workspace.id, page.id, [
        {"command": "open", "args": {"statusRevisionToken": token}}])
    assert opened.status == "open"
    assert opened.status_revision_token != token and opened.status_revision_token.isdigit()


def test_a_command_after_a_transition_in_one_batch_is_rejected(revstore):
    """The whole 'single transition, at the end only' guarantee: a transition regenerates the token,
    so any command sequenced after it carries a now-stale token and the batch aborts."""
    workspace = revstore.create_workspace("demo")
    page = revstore.create_page(workspace.id, "test-flow", "C").page
    token = page.status_revision_token
    with pytest.raises(ConflictError, match="command 1"):
        revstore.mutate_page_batch(workspace.id, page.id, [
            {"command": "open", "args": {"statusRevisionToken": token}},                       # transitions
            {"command": "setSummary", "args": {"statusRevisionToken": token, "text": "s"}},     # stale now
        ])
    assert revstore.get_page(workspace.id, page.id).status == "draft"       # all-or-nothing


def test_set_page_status_regenerates_the_revision(revstore):
    workspace = revstore.create_workspace("demo")
    page = revstore.create_page(workspace.id, "test-flow", "C").page
    token = page.status_revision_token
    updated = revstore.set_page_status(workspace.id, page.id, "open")       # admin FSM bypass
    assert updated.status == "open"
    assert updated.status_revision_token != token and updated.status_revision_token.isdigit()


def test_legacy_page_without_a_token_bootstraps_on_first_transition(store):
    workspace = store.create_workspace("demo")
    page = store.create_page(workspace.id, "test-flow", "C").page
    # Simulate a page stored before this feature: clear its token on disk.
    loaded = store.load_workspace(workspace.id)
    loaded.pages[page.id].status_revision_token = None
    store._touch_and_save(loaded)

    # A command presenting no token matches the null current token, and content keeps it null.
    store.mutate_page_batch(workspace.id, page.id, [{"command": "setSummary", "args": {"text": "s"}}])
    assert store.get_page(workspace.id, page.id).status_revision_token is None
    # The first status transition assigns the first real token.
    opened, _ = store.mutate_page_batch(workspace.id, page.id, [{"command": "open"}])
    assert opened.status == "open"
    assert opened.status_revision_token is not None and opened.status_revision_token.isdigit()


def test_render_markdown_shows_the_revision(revstore):
    workspace = revstore.create_workspace("demo")
    page = revstore.create_page(workspace.id, "test-fields", "P").page      # token "000001"
    assert "rev `000001`" in revstore.render_markdown(workspace.id, page.id)


def test_next_actions_edges_carry_the_revision(revstore):
    workspace = revstore.create_workspace("demo")
    page = revstore.create_page(workspace.id, "test-lifecycle", "F").page
    token = page.status_revision_token
    actions = revstore.next_actions(workspace.id, page.id)
    edges = [e for e in actions["do"] + actions["blocked"] + actions["humanGates"]
             if e["pageId"] == page.id]
    assert edges and all(edge["statusRevisionToken"] == token for edge in edges)

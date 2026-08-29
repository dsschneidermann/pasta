"""Integration tests for the FastMCP server, driven end-to-end via the in-memory client.

Exercises the full path: MCP tool call -> store transaction -> pure core -> disk,
and back. The module-global STORE is repointed at a per-test temp directory.

The behavioural flows use the hand-authored fixtures (src.testtypes); only the
`describePageType` listing assertion pins to the production set (the advertised surface a real
client sees), since the fixtures are hidden from discovery.
"""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import src.server as server
from src.store import Store


@pytest.fixture
def mcp(tmp_path):
    server.STORE = Store(tmp_path)   # tools resolve STORE from the module at call time
    return server.mcp


def call(mcp, name, args=None):
    async def _run():
        async with Client(mcp) as client:
            result = await client.call_tool(name, args or {})
            return result.data

    return asyncio.run(_run())


def _mutate(mcp, args):
    """Call mutatePageBatch presenting the page's current status_revision_token on each command - the
    ordinary caller pattern (read the token, then write against it). getPage returns the raw
    serialized page, so the token is under its snake_case key."""
    page = call(mcp, "getPage", {"workspaceId": args["workspaceId"], "pageId": args["pageId"]})
    token = page["status_revision_token"]
    stamped = {**args, "commands": [
        {**command, "args": {"statusRevisionToken": token, **(command.get("args") or {})}}
        for command in args["commands"]]}
    return call(mcp, "mutatePageBatch", stamped)


def test_end_to_end_authoring_flow(mcp):
    workspace = call(mcp, "createWorkspace", {"name": "demo"})
    workspace_id = workspace["id"]

    page = call(mcp, "createPage",
                {"workspaceId": workspace_id, "type": "test-flow", "title": "A change"})
    page_id = page["id"]
    assert page["status"] == "draft"

    fetched = call(mcp, "getPage", {"workspaceId": workspace_id, "pageId": page_id})
    assert fetched["type"] == "test-flow"
    assert fetched["status"] == "draft"

    # Content mutation round-trips.
    _mutate(mcp, {"workspaceId": workspace_id, "pageId": page_id,
                            "commands": [{"command": "setSummary", "args": {"text": "The body."}}]})
    fetched = call(mcp, "getPage", {"workspaceId": workspace_id, "pageId": page_id})
    assert fetched["sections"]["summary"]["body"] == "The body."

    # Transition advances status and the echoed legal set updates.
    result = _mutate(mcp,
                  {"workspaceId": workspace_id, "pageId": page_id, "commands": [{"command": "open"}]})
    assert result["status"] == "open"
    # The sole `do` edge from `open` is the `close` transition, now shaped kind='transition'
    # commands=[event] (the singular `command` field is gone from `do`).
    close_edge = result["next"]["do"][0]
    assert close_edge["kind"] == "transition" and close_edge["command"] == "close"

    # tree and listWorkspaces reflect reality.
    tree = call(mcp, "tree", {"workspaceId": workspace_id})
    assert tree["nodes"][0]["id"] == page_id
    assert any(w["id"] == workspace_id for w in call(mcp, "listWorkspaces"))


def test_describe_mutations_marks_availability(mcp):
    workspace = call(mcp, "createWorkspace", {"name": "demo"})
    page = call(mcp, "createPage",
                {"workspaceId": workspace["id"], "type": "test-flow", "title": "A change"})
    mutations = call(mcp, "describeMutations", {"workspaceId": workspace["id"], "pageId": page["id"]})
    availability = {entry["name"]: entry["available"] for entry in mutations["commands"]}
    assert availability["open"] is True        # legal from draft
    assert availability["close"] is False      # not legal until open


def test_illegal_mutation_surfaces_as_tool_error(mcp):
    workspace = call(mcp, "createWorkspace", {"name": "demo"})
    page = call(mcp, "createPage",
                {"workspaceId": workspace["id"], "type": "test-flow", "title": "A change"})
    with pytest.raises(ToolError):
        _mutate(mcp,
             {"workspaceId": workspace["id"], "pageId": page["id"], "commands": [{"command": "reopen"}]})  # illegal from draft


def test_lifecycle_transition_blocked_until_required_fields(mcp):
    workspace = call(mcp, "createWorkspace", {"name": "demo"})
    wid = workspace["id"]
    page = call(mcp, "createPage", {"workspaceId": wid, "type": "test-lifecycle", "title": "Dark mode"})
    pid = page["id"]
    assert page["status"] == "draft"

    child_id = page["children"][0]["id"]

    # beginPlanning requires a summary - describeMutations advertises it as unavailable,
    # and the tool call is rejected.
    mutations = call(mcp, "describeMutations", {"workspaceId": wid, "pageId": pid})
    availability = {entry["name"]: entry["available"] for entry in mutations["commands"]}
    assert availability["beginPlanning"] is False
    with pytest.raises(ToolError):
        _mutate(mcp, {"workspaceId": wid, "pageId": pid, "commands": [{"command": "beginPlanning"}]})

    # Once the summary is set, the transition is legal and advances the status.
    _mutate(mcp, {"workspaceId": wid, "pageId": pid,
                            "commands": [{"command": "setSummary", "args": {"text": "A dark theme."}}]})
    result = _mutate(mcp, {"workspaceId": wid, "pageId": pid, "commands": [{"command": "beginPlanning"}]})
    assert result["status"] == "planning"
    # The next transition is itself gated: beginImplementation needs a part first, so it is not yet a
    # `do` edge (each `do` edge carries a `commands` array).
    assert "beginImplementation" not in {edge["command"] for edge in result["next"]["do"]}
    # beginImplementation also carries a page-status guard on the pinned child (it must be `ready`).
    # The child's own markReady is parent-gated, so it only becomes possible now that we are in
    # `planning`; ready it here so the rest of this test isolates the required-content gates.
    _mutate(mcp, {"workspaceId": wid, "pageId": child_id, "commands": [
        {"command": "addStep", "args": {"text": "build"}}, {"command": "markReady"}]})
    _mutate(mcp, {"workspaceId": wid, "pageId": pid,
                            "commands": [{"command": "addPart", "args": {"name": "Renderer"}}]})
    result = _mutate(mcp, {"workspaceId": wid, "pageId": pid, "commands": [{"command": "beginImplementation"}]})
    assert result["status"] == "building"


def test_batch_and_next_actions_via_server(mcp):
    workspace = call(mcp, "createWorkspace", {"name": "demo"})
    wid = workspace["id"]
    page = call(mcp, "createPage", {"workspaceId": wid, "type": "test-lifecycle", "title": "F"})
    result = _mutate(mcp, {"workspaceId": wid, "pageId": page["id"], "commands": [
        {"command": "setSummary", "args": {"text": "S"}},
        {"command": "addPart", "args": {"name": "P"}},
        {"command": "beginPlanning"},
    ]})
    assert result["status"] == "planning" and result["count"] == 3
    # Ready the pinned child so beginImplementation clears its page-status guard and stays a `do` edge.
    # The parent is in `planning`, which is what unlocks the child's parent-gated markReady.
    child_id = page["children"][0]["id"]
    _mutate(mcp, {"workspaceId": wid, "pageId": child_id, "commands": [
        {"command": "addStep", "args": {"text": "build"}}, {"command": "markReady"}]})
    actions = call(mcp, "nextActions", {"workspaceId": wid})
    # agent edge from `planning`; every `do` edge carries a `commands` array (singular `command` is gone)
    assert "beginImplementation" in {edge["command"] for edge in actions["do"]}


def test_echoed_next_carries_stage_field_setters(mcp):
    wid = call(mcp, "createWorkspace", {"name": "demo"})["id"]
    # createPage echoes `next`: a fresh draft feature surfaces its stage field setter (setSummary).
    created = call(mcp, "createPage", {"workspaceId": wid, "type": "test-lifecycle", "title": "F"})
    assert "setSummary" in {edge["command"] for edge in created["next"]["do"]}
    # mutatePageBatch echoes the same rollup: after advancing to `planning`, the new stage's field
    # setter (addPart) shows and the previous stage's setSummary drops out (stage-scoping).
    result = _mutate(mcp, {"workspaceId": wid, "pageId": created["id"], "commands": [
        {"command": "setSummary", "args": {"text": "S"}},
        {"command": "beginPlanning"},
    ]})
    planning_do = {edge["command"] for edge in result["next"]["do"]}
    assert "addPart" in planning_do and "setSummary" not in planning_do


def test_archive_page_via_server(mcp):
    workspace = call(mcp, "createWorkspace", {"name": "demo"})
    wid = workspace["id"]
    page = call(mcp, "createPage", {"workspaceId": wid, "type": "test-fields", "title": "A"})
    call(mcp, "archivePage", {"workspaceId": wid, "pageId": page["id"]})
    assert call(mcp, "tree", {"workspaceId": wid})["nodes"] == []
    # The tool has no archived escape hatch: `includeArchived` is not a parameter, so FastMCP
    # rejects it on argument validation rather than quietly ignoring it. An archived page is
    # still reachable by id through `getPage`, and through the web UI's ?archived=true view -
    # Store.tree keeps its include_archived flag for exactly that route.
    with pytest.raises(ToolError):
        call(mcp, "tree", {"workspaceId": wid, "includeArchived": True})
    assert call(mcp, "getPage", {"workspaceId": wid, "pageId": page["id"]})["archived"] is True
    with pytest.raises(ToolError):
        _mutate(mcp, {"workspaceId": wid, "pageId": page["id"],
                                "commands": [{"command": "setBody", "args": {"text": "x"}}]})
    call(mcp, "unarchivePage", {"workspaceId": wid, "pageId": page["id"]})
    assert len(call(mcp, "tree", {"workspaceId": wid})["nodes"]) == 1


def test_reparent_and_reorder_via_server(mcp):
    wid = call(mcp, "createWorkspace", {"name": "demo"})["id"]
    a = call(mcp, "createPage", {"workspaceId": wid, "type": "test-fields", "title": "A"})["id"]
    b = call(mcp, "createPage", {"workspaceId": wid, "type": "test-fields", "title": "B"})["id"]
    child = call(mcp, "createPage",
                 {"workspaceId": wid, "type": "test-flow", "title": "C", "parentId": a})["id"]

    # reparent C from A to B
    res = call(mcp, "reparentPage", {"workspaceId": wid, "pageId": child, "newParentId": b})
    assert res["parentId"] == b and res["siblingIds"] == [child]
    nodes = {n["id"]: n for n in call(mcp, "tree", {"workspaceId": wid})["nodes"]}
    assert nodes[a]["children"] == [] and nodes[b]["children"][0]["id"] == child

    # reorder top-level: move A to after B -> [B, A]
    res = call(mcp, "reorderPage", {"workspaceId": wid, "pageId": a, "toIndex": 1, "precedingId": b})
    assert res["siblingIds"] == [b, a]
    assert [n["id"] for n in call(mcp, "tree", {"workspaceId": wid})["nodes"]] == [b, a]

    # a cycle and a stale reorder both surface as ToolErrors
    with pytest.raises(ToolError):
        call(mcp, "reparentPage", {"workspaceId": wid, "pageId": a, "newParentId": a})
    with pytest.raises(ToolError):
        call(mcp, "reorderPage", {"workspaceId": wid, "pageId": a, "toIndex": 0, "precedingId": b})


def test_rename_page_via_server(mcp):
    wid = call(mcp, "createWorkspace", {"name": "demo"})["id"]
    a = call(mcp, "createPage", {"workspaceId": wid, "type": "test-fields", "title": "A"})["id"]
    b = call(mcp, "createPage", {"workspaceId": wid, "type": "test-fields", "title": "B"})["id"]

    res = call(mcp, "renamePage", {"workspaceId": wid, "pageId": a, "title": "Renamed"})
    assert res == {"id": a, "title": "Renamed"}
    assert call(mcp, "getPage", {"workspaceId": wid, "pageId": a})["title"] == "Renamed"

    # Sibling titles are NOT reserved: renaming B to A's title succeeds (no ToolError).
    call(mcp, "renamePage", {"workspaceId": wid, "pageId": b, "title": "Renamed"})
    titles = [n["title"] for n in call(mcp, "tree", {"workspaceId": wid})["nodes"]]
    assert titles == ["Renamed", "Renamed"]


def test_add_link_via_server(mcp):
    wid = call(mcp, "createWorkspace", {"name": "demo"})["id"]
    a = call(mcp, "createPage", {"workspaceId": wid, "type": "test-fields", "title": "A"})["id"]
    b = call(mcp, "createPage", {"workspaceId": wid, "type": "test-flow", "title": "B"})["id"]

    # addLink is offered on the page's OWN command surface - not only via the separate `link` tool.
    mutations = call(mcp, "describeMutations", {"workspaceId": wid, "pageId": a})
    assert "addLink" in {entry["name"] for entry in mutations["commands"]}

    _mutate(mcp, {"workspaceId": wid, "pageId": a,
                                  "commands": [{"command": "addLink",
                                                "args": {"toId": b, "role": "depends-on"}}]})
    assert call(mcp, "getPage", {"workspaceId": wid, "pageId": a})["links"] == [{"to": b, "role": "depends-on"}]
    # It obeys the same rules as the link tool: a duplicate edge is rejected.
    with pytest.raises(ToolError):
        _mutate(mcp, {"workspaceId": wid, "pageId": a,
                                      "commands": [{"command": "addLink",
                                                    "args": {"toId": b, "role": "depends-on"}}]})


def test_lifecycle_auto_children_via_server(mcp):
    workspace = call(mcp, "createWorkspace", {"name": "demo"})
    page = call(mcp, "createPage",
                {"workspaceId": workspace["id"], "type": "test-lifecycle", "title": "Dark mode"})
    assert {child["type"] for child in page["children"]} == {"test-child"}
    tree = call(mcp, "tree", {"workspaceId": workspace["id"]})
    assert len(tree["nodes"][0]["children"]) == 1


def test_flow_close_flow(mcp):
    workspace = call(mcp, "createWorkspace", {"name": "demo"})
    page = call(mcp, "createPage",
                {"workspaceId": workspace["id"], "type": "test-flow", "title": "Crash"})
    pid = page["id"]
    _mutate(mcp, {"workspaceId": workspace["id"], "pageId": pid, "commands": [{"command": "open"}]})
    result = _mutate(mcp, {"workspaceId": workspace["id"], "pageId": pid,
                                     "commands": [{"command": "close", "args": {"sha": "abc", "message": "fixed"}}]})
    assert result["status"] == "closed"
    assert result["createdIds"][0] is not None
    fetched = call(mcp, "getPage", {"workspaceId": workspace["id"], "pageId": pid})
    assert fetched["sections"]["resolution"]["commits"][0]["sha"] == "abc"


# --- state guidance echoed on a transition -----------------------------------
# test-flow guides `open` only; test-child guides its initial `draft`.
FLOW_OPEN_GUIDANCE = ("open - the work is under way.\n"
                      "Record a commit with close when it is finished.")


def _flow_page(mcp):
    workspace = call(mcp, "createWorkspace", {"name": "guidance"})
    page = call(mcp, "createPage",
                {"workspaceId": workspace["id"], "type": "test-flow", "title": "A change"})
    return workspace["id"], page["id"]


def test_mutate_page_batch_echoes_guidance_on_transition(mcp):
    workspace_id, page_id = _flow_page(mcp)
    result = _mutate(mcp,
                  {"workspaceId": workspace_id, "pageId": page_id,
                   "commands": [{"command": "open"}]})
    assert result["status"] == "open"
    assert result["guidance"] == FLOW_OPEN_GUIDANCE


def test_mutate_page_batch_omits_guidance_on_a_content_only_write(mcp):
    workspace_id, page_id = _flow_page(mcp)
    result = _mutate(mcp,
                  {"workspaceId": workspace_id, "pageId": page_id,
                   "commands": [{"command": "setSummary", "args": {"text": "x"}}]})
    # Absent, not null, so an unguided write is byte-identical to before the feature.
    assert "guidance" not in result


def test_two_transitions_in_one_batch_are_rejected(mcp):
    # A transition regenerates the status revision, so a second transition in the same batch carries
    # a now-stale token and the whole batch aborts - at most one transition per batch, at its end.
    workspace_id, page_id = _flow_page(mcp)
    with pytest.raises(ToolError, match="command 1"):
        _mutate(mcp,
               {"workspaceId": workspace_id, "pageId": page_id,
                "commands": [{"command": "open"},
                             {"command": "close", "args": {"sha": "abc", "message": "done"}}]})
    # All-or-nothing: nothing committed, so the page is still in draft.
    assert call(mcp, "getPage", {"workspaceId": workspace_id, "pageId": page_id})["status"] == "draft"


def test_create_page_echoes_initial_state_guidance_and_children_do_not(mcp):
    workspace = call(mcp, "createWorkspace", {"name": "guidance"})
    workspace_id = workspace["id"]

    # A guided initial state echoes on creation.
    child = call(mcp, "createPage",
                 {"workspaceId": workspace_id, "type": "test-child", "title": "Child"})
    assert child["guidance"] == "draft - write the steps and checks here."

    # One whose initial state declares nothing stays silent.
    flow = call(mcp, "createPage",
                {"workspaceId": workspace_id, "type": "test-flow", "title": "Flow"})
    assert "guidance" not in flow

    # An auto-pinned child gets no echo, even though it guides its initial state.
    parent = call(mcp, "createPage",
                  {"workspaceId": workspace_id, "type": "test-lifecycle", "title": "Parent"})
    assert "guidance" not in parent
    assert parent["children"]                                   # the pinned child was created
    assert all("guidance" not in entry for entry in parent["children"])


def test_status_revision_surfaced_and_echoed_via_server(mcp):
    wid = call(mcp, "createWorkspace", {"name": "demo"})["id"]
    created = call(mcp, "createPage", {"workspaceId": wid, "type": "test-flow", "title": "A"})
    token = created["statusRevisionToken"]                         # createPage echoes the new token
    assert isinstance(token, str) and len(token) == 6 and token.isdigit()
    # getPage surfaces it under the raw serialized (snake_case) key.
    assert call(mcp, "getPage", {"workspaceId": wid, "pageId": created["id"]})["status_revision_token"] == token
    # A content write echoes the (unchanged) token; a transition echoes a fresh one.
    result = _mutate(mcp, {"workspaceId": wid, "pageId": created["id"],
                          "commands": [{"command": "setSummary", "args": {"text": "s"}}]})
    assert result["statusRevisionToken"] == token
    opened = _mutate(mcp, {"workspaceId": wid, "pageId": created["id"], "commands": [{"command": "open"}]})
    assert opened["status"] == "open" and opened["statusRevisionToken"] != token
    # A wrong token surfaces as a ToolError rather than mutating.
    with pytest.raises(ToolError, match="does not match"):
        call(mcp, "mutatePageBatch", {"workspaceId": wid, "pageId": created["id"],
                                      "commands": [{"command": "close",
                                                    "args": {"statusRevisionToken": "nope", "sha": "a", "message": "m"}}]})

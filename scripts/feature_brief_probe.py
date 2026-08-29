"""Manual end-to-end probe of the feature-brief workflow over MCP.

Drives a real feature-brief through its whole lifecycle against a running
pasta MCP endpoint - draft -> grounding -> spec -> planning -> planReview ->
building -> review - authoring the content each stage gates on, then archives the
page. After every step it prints the `do` / `blocked` / `humanGates` / `attention`
rollup for the brief's whole subtree, so you can watch WHICH instructions the
server hands an agent at WHICH stage.

`do` edges are printed as SHAPES - kind, page type, commands, target field - with
each field's instruction elided to `INSTRUCTION_WIDTH` characters. The point is
to see the set of edges at a glance, not to re-read the descriptions; raise that
constant (or pass --instructions) if you want more of each one.

What it is good for: confirming stage-scoping by eye. Each pinned child is held
by a ParentStateGuard on its finalize transition, and next_actions withholds the
field setters of a parent-gated transition - so the children come online one
stage at a time:

    grounding  ->  the brief's four grounding setters, all three children silent
    spec       ->  the feature-spec's setters (+ askQuestion); NO addStep/addCase
    planning   ->  addStep (carrying the step's own detail blocks) and addCase; the spec has
                   locked itself. addStepDetail, which appends to a step that already exists,
                   is deliberately not offered - the element must exist before it can be filled

The closing summary tallies the edge count per stage, so a regression shows up as
a stage that got noisy - most usefully, an addStep appearing during `spec`.

The run STOPS at `review`: `ship` is a human gate and the probe never crosses one.
It then archives the brief (and its pinned subtree) so repeated runs do not litter
the workspace - pass --keep to leave it for inspection.

Usage (server must already be running):
    uv run python scripts/feature_brief_probe.py
    uv run python scripts/feature_brief_probe.py --workspace ws:xxxx-yyyy
    uv run python scripts/feature_brief_probe.py --url http://localhost:8000/pasta/mcp
    uv run python scripts/feature_brief_probe.py --keep --instructions

Sibling of mcp_probe.py, whose JSON-RPC/SSE plumbing it reuses; that probe checks
the transport handshake, this one checks the workflow the transport carries.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from typing import Any

import httpx

# Running a script puts its own directory on sys.path, so the sibling probe imports directly.
from mcp_probe import BASE_HEADERS, DEFAULT_URL, PROTOCOL_VERSION, parse_body, rpc

DEFAULT_WORKSPACE = "ws:mrteq0c5-238cf6"
BRIEF_TITLE = "[probe] feature-brief lifecycle walk"

# How much of a field's instruction to show on its `do` line. The probe is about edge SHAPES;
# the full text is a describePageType call away.
INSTRUCTION_WIDTH = 44

# Column widths for the `do` table.
_KIND_W, _TYPE_W, _CMDS_W, _TARGET_W = 11, 20, 38, 21


class ProbeError(RuntimeError):
    """A tool call came back as a JSON-RPC error, an isError result, or an undecodable body."""


# --- transport ----------------------------------------------------------------
def _result_frame(body: Any) -> dict[str, Any]:
    """The JSON-RPC message from a JSON body, or the last meaningful frame of an SSE stream."""
    if isinstance(body, list):                       # SSE: parse_body yields decoded frames
        for frame in reversed(body):
            if isinstance(frame, dict) and ("result" in frame or "error" in frame):
                return frame
        raise ProbeError(f"no JSON-RPC result among the SSE frames: {body!r}")
    if isinstance(body, dict):
        return body
    raise ProbeError(f"unexpected response body: {body!r}")


def _text_content(result: dict[str, Any]) -> str:
    """The first text block of a tools/call result (where FastMCP puts the payload / the error)."""
    for item in result.get("content", []):
        if item.get("type") == "text":
            return item.get("text", "")
    return ""


class Probe:
    """A connected MCP session, scoped to one workspace."""

    def __init__(self, client: httpx.Client, url: str, workspace_id: str) -> None:
        self.client = client
        self.url = url
        self.workspace_id = workspace_id
        self.headers = dict(BASE_HEADERS)
        self._ids = itertools.count(1)

    def handshake(self) -> None:
        """initialize -> notifications/initialized, carrying the session id onward."""
        init = self.client.post(self.url, headers=self.headers, json=rpc(
            next(self._ids), "initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "feature-brief-probe", "version": "0.0.0"},
            }))
        if init.status_code >= 400:
            raise ProbeError(f"initialize failed: HTTP {init.status_code} {init.text[:400]}")
        session_id = init.headers.get("mcp-session-id")
        if session_id:
            self.headers["mcp-session-id"] = session_id
        self.client.post(self.url, headers=self.headers, json=rpc(None, "notifications/initialized"))

    def call(self, tool: str, **arguments: Any) -> Any:
        """One tools/call round-trip; returns the tool's decoded payload or raises ProbeError."""
        response = self.client.post(self.url, headers=self.headers, json=rpc(
            next(self._ids), "tools/call", {"name": tool, "arguments": arguments}))
        message = _result_frame(parse_body(response))
        if "error" in message:
            raise ProbeError(f"{tool}: {json.dumps(message['error'])}")
        result = message.get("result", {})
        if result.get("isError"):
            raise ProbeError(f"{tool}: {_text_content(result)}")
        if "structuredContent" in result:
            return result["structuredContent"]
        text = _text_content(result)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def mutate(self, page_id: str, *commands: dict[str, Any]) -> list[str]:
        """Run a batch on one page and return the ids it created, in order, nulls dropped.

        Reads the page's current status_revision_token and stamps it as the first entry in each
        command's args, which the server requires on each."""
        token = self.call("getPage", workspaceId=self.workspace_id,
                          pageId=page_id)["status_revision_token"]
        stamped = [{**command, "args": {"statusRevisionToken": token, **command.get("args", {})}}
                   for command in commands]
        result = self.call("mutatePageBatch", workspaceId=self.workspace_id,
                           pageId=page_id, commands=stamped)
        return [created for created in result.get("createdIds", []) if created]


def cmd(command: str, /, **args: Any) -> dict[str, Any]:
    """One `{command, args}` entry for a mutatePageBatch batch.

    `command` is positional-ONLY: several page-type commands take an arg called `name`
    (addComponent) or `command`, which would otherwise collide with the parameter.
    """
    return {"command": command, "args": args} if args else {"command": command}


# --- reporting ----------------------------------------------------------------
ELLIPSIS = "..."          # ASCII on purpose: the Windows console is cp1252 and mangles "…"


def elide(text: str | None, width: int) -> str:
    """Collapse whitespace and cut to `width`, marking the cut - instructions are multi-line."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= width else flat[: max(0, width - len(ELLIPSIS))] + ELLIPSIS


def show(probe: Probe, step: str, brief_id: str, width: int) -> int:
    """Print the brief subtree's rollup and return its `do` edge count."""
    status = probe.call("getPage", workspaceId=probe.workspace_id, pageId=brief_id)["status"]
    actions = probe.call("nextActions", workspaceId=probe.workspace_id, pageId=brief_id)
    do, blocked = actions["do"], actions["blocked"]
    gates, attention = actions["humanGates"], actions["attention"]

    print(f"\n{'=' * 100}")
    print(f"== {step}   [brief status: {status}]")
    print("=" * 100)

    print(f"  do ({len(do)}):" if do else "  do: (none)")
    for edge in do:
        command = edge["command"]
        if edge["kind"] == "field":
            target = f"{edge['section']}.{edge['field']}"
            print(f"    {'field':<{_KIND_W}}{edge['pageType']:<{_TYPE_W}}{command:<{_CMDS_W}}"
                  f"{target:<{_TARGET_W}}{elide(edge.get('instruction'), width)}")
        else:
            print(f"    {'transition':<{_KIND_W}}{edge['pageType']:<{_TYPE_W}}{command}")

    print(f"  blocked ({len(blocked)}):" if blocked else "  blocked: (none)")
    for edge in blocked:
        print(f"    {edge['pageType']:<{_TYPE_W}}{edge['command']:<{_CMDS_W}}{edge['reason']}")

    print(f"  humanGates ({len(gates)}):" if gates else "  humanGates: (none)")
    for edge in gates:
        reason = f"   BLOCKED: {edge['blockedReason']}" if edge.get("blockedReason") else ""
        print(f"    {edge['pageType']:<{_TYPE_W}}{edge['command']}{reason}")

    if attention:
        print(f"  attention ({len(attention)}):")
        for item in attention:
            print(f"    {item['pageTitle']:<{_TYPE_W}}{item['sectionKey']}.{item['field']}"
                  f"  {item['itemId']}  ({item['status']})")
    else:
        print("  attention: (none)")
    return len(do)


def find_features_toc(probe: Probe) -> str | None:
    """The workspace's `Features` toc page id, where briefs are filed - None if it has none."""
    for node in probe.call("tree", workspaceId=probe.workspace_id)["nodes"]:
        if node["type"] == "toc" and node["title"].strip().lower() == "features":
            return node["id"]
    return None


# --- the walk -----------------------------------------------------------------
def walk(probe: Probe, width: int, keep: bool) -> None:
    tally: list[tuple[str, int]] = []

    def record(step: str, brief_id: str) -> None:
        tally.append((step, show(probe, step, brief_id, width)))

    parent_id = find_features_toc(probe)
    print(f"workspace: {probe.workspace_id}")
    print(f"filing under: {parent_id or '(workspace root - no Features toc found)'}")

    created = probe.call("createPage", workspaceId=probe.workspace_id, type="feature-brief",
                         title=BRIEF_TITLE, parentId=parent_id)
    brief = created["id"]
    children = {child["type"]: child["id"] for child in created["children"]}
    plan, tests, spec = (children["implementation-plan"], children["testing-plan"],
                         children["feature-spec"])
    print(f"brief: {brief}")
    for tag, page_id in sorted(children.items()):
        print(f"  pinned child: {tag:<22}{page_id}")

    try:
        # 1. draft - only the intent is asked for; the pinned children are not yet in play.
        record("1. DRAFT (just created)", brief)

        # 2. grounding - the four grounded-base lists. The plan children stay silent here: their
        #    markReady/markSealed are held by a ParentStateGuard until the brief reaches planning.
        probe.mutate(brief, cmd("setSummary", text=(
            "Probe the feature-brief lifecycle end to end so the stage-scoped instruction set is "
            "visible at every state.")))
        record("2. DRAFT + summary (beginGrounding now legal)", brief)
        probe.mutate(brief, cmd("beginGrounding"))
        record("3. GROUNDING (grounding fields only - children silent)", brief)

        # 3. planning - the grounded base lets the brief advance, which unlocks all three children.
        probe.mutate(
            brief,
            cmd("addComponent", name="scripts/feature_brief_probe.py", text="Component description."),
            cmd("addConstraint", text="Python >=3.14; the probe must not cross a human gate."),
            cmd("addConflict", text="None - a new script, no existing prober covers the lifecycle."),
            cmd("addDocumentation", text="Self-direction: its `do` invariants describe this rollup."),
        )
        record("4. GROUNDING complete (beginSpec now legal)", brief)
        probe.mutate(brief, cmd("beginSpec"))
        record("5. SPEC (spec unlocked - the two plans stay silent)", brief)

        # 4. the SPEC stage. Questions come first: a spec decision ref-checks its questionId
        #    against the parent brief's questions, and beginPlanning requires questions.items.
        question_ids = probe.mutate(brief, cmd(
            "askQuestion", text="Should the probe archive the brief on a failed run too?"))
        probe.mutate(brief, cmd("answerQuestion", questionId=question_ids[0],
                                answer="No - leave it for inspection and print its id."))
        probe.mutate(
            spec,
            cmd("setOverview", text=(
                "A manual probe of the feature-brief lifecycle. Covers the stage rollups; excludes "
                "the ship gate, which is a human edge.")),
            cmd("addDesign", blocks=[
                {"kind": "heading", "level": 2, "text": "Output"},
                {"kind": "paragraph", "text": (
                    "One block per stage: the do/blocked/humanGates/attention rollup for the "
                    "brief's whole subtree, with field instructions elided.")},
                {"kind": "code", "language": "text",
                 "source": "field  implementation-plan  addStep  steps.items  Each one action..."},
            ]),
            cmd("addDecisions", blocks=[
                {"kind": "decision", "questionId": question_ids[0], "text": (
                    "A failed run leaves the brief un-archived and prints its id, so the failure "
                    "can be inspected in place.")},
            ]),
        )
        record("6. SPEC authored (markSealed now legal)", brief)
        probe.mutate(spec, cmd("markSealed"))
        record("7. SPEC sealed (beginPlanning now legal)", brief)

        # 5. planning - only NOW are steps and cases written, against a settled design. The spec's
        #    own setters are gone here: `sealed` is terminal, so its authoring locks itself.
        probe.mutate(brief, cmd("beginPlanning"))
        record("8. PLANNING (plans unlocked, sealed spec locked)", brief)
        # A step is created complete: the add carries its content, so one batch writes both steps
        # and nothing has to name an id the batch has not committed. Both kinds the field accepts
        # are exercised - a paragraph, whose inline runs let a code span ride inside prose, and a
        # code block. createdIds stays one id per command, so `step_ids` is still the two steps.
        step_ids = probe.mutate(
            plan,
            cmd("addStep", detail=[
                {"kind": "paragraph", "inlines": [
                    "Add ", {"code": "scripts/feature_brief_probe.py"},
                    " with the MCP session plumbing, reusing the sibling probe's transport."]},
                {"kind": "code", "language": "bash",
                 "source": "uv run python scripts/feature_brief_probe.py"},
            ]),
            cmd("addStep", detail=[
                {"kind": "paragraph", "inlines": [
                    "Drive the lifecycle and print each stage's rollup, then archive the brief."]},
            ]),
        )
        probe.mutate(plan, cmd("markReady"))
        case_ids = probe.mutate(
            tests,
            cmd("addCase", text="A brief in grounding offers exactly its four grounding setters."),
            cmd("addCase", text="The spec stage offers no addStep or addCase."),
        )
        probe.mutate(tests, cmd("markReady"))
        record("9. PLANNING complete (both plans ready)", brief)

        # 6. plan review - record a verdict, then approve into building.
        probe.mutate(brief, cmd("submitPlan"))
        record("10. PLAN REVIEW (verdict is the gate)", brief)
        probe.mutate(
            brief,
            cmd("addFinding", issue="The probe never exercises a failing case status.",
                severity="nit", action="edit"),
            cmd("setReviewVerdict", verdict="build-ready"),
        )
        record("11. PLAN REVIEW + verdict (approvePlan now legal)", brief)

        # 7. building - execution marks, which stay legal on a `ready` plan.
        probe.mutate(brief, cmd("approvePlan"))
        record("12. BUILDING (execution marks, not authoring)", brief)
        probe.mutate(plan, *(cmd("markStepDone", stepId=step_id) for step_id in step_ids))
        probe.mutate(tests, *(cmd("markCasePassed", caseId=case_id) for case_id in case_ids))
        probe.mutate(brief, cmd("recordCommit", sha="0000000", message="probe: walk the lifecycle"))
        record("13. BUILDING complete (every step done, every case passed)", brief)

        # 8. review - `ship` is a human gate, so this is where the probe stops.
        probe.mutate(brief, cmd("submitForReview"))
        record("14. REVIEW (ship is a human gate - stopping here)", brief)

    except Exception:
        print(f"\n!! FAILED - the brief is left at {brief} for inspection.")
        print(f"!! Archive it with: archivePage(workspaceId={probe.workspace_id!r}, pageId={brief!r})")
        raise

    print(f"\n{'=' * 100}")
    print("== SUMMARY: `do` edges offered per stage")
    print("=" * 100)
    for step, count in tally:
        print(f"  {count:>3}  {step}")

    if keep:
        print(f"\nkept (--keep): {brief}")
        return
    archived = probe.call("archivePage", workspaceId=probe.workspace_id, pageId=brief)
    print(f"\narchived: {archived['id']} (archived={archived['archived']}) - subtree hidden from tree")
    live = {node["id"] for node in probe.call("tree", workspaceId=probe.workspace_id)["nodes"]}
    print(f"gone from the default tree: {brief not in live}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"MCP endpoint (default {DEFAULT_URL})")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE,
                        help=f"workspace id (default {DEFAULT_WORKSPACE})")
    parser.add_argument("--new-workspace", nargs="?", const="[probe] lifecycle", default=None,
                        metavar="NAME", help="create a fresh workspace (optionally named) and run in "
                                             "it instead of --workspace - the clean-slate run")
    parser.add_argument("--keep", action="store_true",
                        help="leave the brief in place instead of archiving it")
    parser.add_argument("--instructions", type=int, nargs="?", const=200, default=INSTRUCTION_WIDTH,
                        metavar="N", help="characters of each field instruction to show "
                                          f"(default {INSTRUCTION_WIDTH}, bare flag gives 200)")
    args = parser.parse_args()

    print(f"MCP endpoint: {args.url}")
    try:
        with httpx.Client(timeout=30.0) as client:
            probe = Probe(client, args.url, args.workspace)
            probe.handshake()
            if args.new_workspace is not None:
                created = probe.call("createWorkspace", name=args.new_workspace)
                probe.workspace_id = created["id"]
                print(f"created clean workspace: {probe.workspace_id} ({created['name']!r})")
            walk(probe, args.instructions, args.keep)
    except httpx.ConnectError:
        print(f"\nCould not connect to {args.url} - is the server running on that host/port?")
        sys.exit(1)
    except (httpx.RequestError, ProbeError) as exc:
        print(f"\nProbe failed: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()

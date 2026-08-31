"""The `bug-report` page type."""

from __future__ import annotations

from ._stage_guidance import BUG_REPORT_DRAFT, BUG_REPORT_OPEN, REVIEW
from ._workspace_guidance import (
    MERGE_PROCESS_DESC,
    MERGE_PROCESS_FIELD,
    TESTING_TOOL_DESC,
    TESTING_TOOL_FIELD,
)
from .core.specs import FSMSpec, WorkspaceGuidanceSpec
from .core.args import _text, add_link_cmd, set_title_cmd
from .core.fields import SectionSpec, _list, _prose, _scalar
from .core.commands import (
    list_cmds,
    set_element_field_cmd,
    set_prose_cmd,
    set_scalar_cmd,
    transition_cmd,
    transition_on_add_cmd,
)
from .core.pagetype import PageType

_COMMIT_LOG_STATES = ("open", "review", "done", "closed")

_BUG_REPORT = PageType(
    tag="bug-report",
    name="Bug report",
    description="Tracks a defect in existing behavior - what's wrong, how to reproduce it, and its resolution.",
    sections=(
        SectionSpec("report", "Report", (
            _scalar("component", description="""
                The component or area of the system the defect lives in. Use the name the codebase or
                an existing architecture page already uses, so related reports group together.
                """),
            _scalar("platform", description="""
                The platform the defect was observed on: OS, browser, runtime, or device, with
                versions. Record what you actually reproduced on, not the full supported matrix.
                """),
            _scalar("version", description="""
                The version, build, or commit sha the defect was observed in. Prefer a sha when the
                build is not tagged, so the report stays pinnable to a point in history.
                """),
        )),
        SectionSpec("summary", "Summary", (
            _prose("body", description="""
                One sentence naming the wrong behaviour, specific enough to tell this defect apart
                from similar ones. State the symptom you can observe, not the cause you suspect or the
                fix you have in mind.
                """),
        )),
        SectionSpec("repro", "Reproduction", (
            _list("steps", element_fields=("text",), description="""
                Each one action that leads to the defect, in order, beginning from a stated starting
                state. Give the exact commands, inputs, and data used, so someone who has never seen
                the system can follow them. The last step is the one that exposes the defect.
                """),
        )),
        SectionSpec("expected", "Expected", (
            _prose("body", description="""
                What should have happened at the final repro step, and what makes that the correct
                behaviour: a spec line, a doc, a passing test, or an established convention. Without
                that grounding the report is an opinion.
                """),
        )),
        SectionSpec("observed", "Observed", (
            _prose("body", description="""
                What actually happened at the final repro step: the literal error message, stack
                trace, exit code, or wrong output, quoted rather than paraphrased. Say whether it
                reproduces every time or intermittently.
                """),
        )),
        SectionSpec("pull_request", "Pull Request", (
            _scalar("url", description="""
                The pull request URL created for this fix.
                """),
        )),
        SectionSpec("resolution", "Resolution", (
            _list("fixCommits", element_fields=("sha", "message", "stale"), description="""
                Each a commit that fixes this defect: the sha and its subject line. Record each as
                you make it rather than reconstructing the list at the end, and flag one stale once
                its sha has left history, for example after a rebase. Record the commit that
                actually lands the fix, not the intermediate work that led to it.
                """),
        )),
    ),
    workspace_guidance=(
        WorkspaceGuidanceSpec(MERGE_PROCESS_FIELD, ("review", "done"), MERGE_PROCESS_DESC),
        WorkspaceGuidanceSpec(TESTING_TOOL_FIELD, ("open",), TESTING_TOOL_DESC),
    ),
    commands=(
        set_scalar_cmd("report", "component"),
        set_scalar_cmd("report", "platform"),
        set_scalar_cmd("report", "version"),
        set_prose_cmd("summary"),
        *list_cmds("repro", field="steps", label="repro step",
                   add_args=(_text(),)),
        set_scalar_cmd("pull_request", "url", name="setPullRequestUrl", label="pull request url",
                       legal_in=("review", "done")),
        set_prose_cmd("expected"),
        set_prose_cmd("observed"),
        transition_cmd("open", "draft -> open"),
        transition_cmd("submitForReview", "open -> review"),
        # review -> done marks the fix built and reviewed, but not yet shippable or merged to main.
        transition_cmd("markDone", "review -> done"),
        transition_cmd("requestChanges", "review -> open", agency="either"),
        # close is a human gate: a person confirms the fix is shippable/merged before it lands.
        transition_on_add_cmd("close", "done -> closed", section="resolution", field="fixCommits",
                     description="record a fix commit AND close the bug", agency="human",
                     add_args=(_text("sha"), _text("message"))),
        transition_on_add_cmd("closeWithoutCommit", "done -> closed", section="resolution", field="fixCommits",
                     description="record a closing note (message only, no commit) AND close the bug", agency="human",
                     add_args=(_text("message"),)),
        *list_cmds("resolution", field="fixCommits", add_name="recordCommit", label="fix commit",
                   remove=False, add_args=(_text("sha"), _text("message")),
                   legal_in=_COMMIT_LOG_STATES),
        set_element_field_cmd("resolution", field="fixCommits", singular="fixCommit",
                              name="markCommitStale", const=("stale", True),
                              description="flag a recorded commit as stale - its sha is no longer in history (e.g. after a rebase)",
                              legal_in=_COMMIT_LOG_STATES),
        transition_cmd("reopen", "closed -> open"),
        add_link_cmd(),
        set_title_cmd(),
    ),
    fsm=FSMSpec(
        name="BugReport",
        initial="draft",
        states=("draft", "open", "review", "done", "closed"),
        state_guidance=(
            ("draft", BUG_REPORT_DRAFT),
            ("open", BUG_REPORT_OPEN),
            ("review", REVIEW),
        ),
    ),
)

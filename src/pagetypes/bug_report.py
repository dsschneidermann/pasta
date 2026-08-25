"""The `bug-report` page type."""

from __future__ import annotations

from ._stage_guidance import BUG_REPORT_DRAFT, BUG_REPORT_OPEN, REVIEW
from . import (
    FSMSpec,
    PageType,
    SectionSpec,
    _list,
    _prose,
    _scalar,
    _text,
    add_link_cmd,
    list_cmds,
    set_prose_cmd,
    set_scalar_cmd,
    set_title_cmd,
    transition_cmd,
    transition_on_add_cmd,
)

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
        SectionSpec("resolution", "Resolution", (
            _list("fixCommits", element_fields=("sha", "message", "url"), description="""
                Each a commit that fixes this defect, recorded as the bug is closed: the sha, its
                subject line, and a url when one exists. Record the commit that actually lands the
                fix, not the intermediate work that led to it.
                """),
        )),
    ),
    commands=(
        set_scalar_cmd("report", "component"),
        set_scalar_cmd("report", "platform"),
        set_scalar_cmd("report", "version"),
        set_prose_cmd("summary"),
        *list_cmds("repro", field="steps", label="repro step",
                   add_args=(_text(),)),
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
                     add_args=(_text("sha"), _text("message"), _text("url", required=False))),
        transition_on_add_cmd("closeWithoutCommit", "done -> closed", section="resolution", field="fixCommits",
                     description="record a closing note (message only, no commit) AND close the bug", agency="human",
                     add_args=(_text("message"),)),
        # fixCommits is populated only by `close`; reorder is offered for a uniform surface.
        *list_cmds("resolution", field="fixCommits", label="fix commit", add=False, remove=False),
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

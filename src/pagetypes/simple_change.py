"""The `simple-change` page type."""

from __future__ import annotations

from ._stage_guidance import REVIEW, SIMPLE_CHANGE_DRAFT, SIMPLE_CHANGE_OPEN
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
    set_element_field_cmd,
    set_prose_cmd,
    set_scalar_cmd,
    set_title_cmd,
    transition_cmd,
    transition_on_add_cmd,
)

_COMMIT_LOG_STATES = ("open", "review", "done", "closed")

_SIMPLE_CHANGE = PageType(
    tag="simple-change",
    name="Simple change",
    description=(
        "Tracks a small, self-contained change or minor feature through a lightweight flow "
        "(draft -> open -> review -> done -> closed) - no planning, spec or testing gates, but the "
        "work is still reviewed before it is marked done. Use this "
        "page type ONLY when the user specifically asks to make a small/simple change or a small/simple "
        "feature; for larger work create a feature-brief, and for a defect in existing behavior use "
        "a bug-report."
    ),
    sections=(
        SectionSpec("change", "Change", (
            _scalar("component", description="""
                The component or area this change touches. Keep it to one: work that spans several
                components is not a simple change and belongs in a feature-brief instead.
                """),
        )),
        SectionSpec("summary", "Summary", (
            _prose("body", description="""
                What to change, in a sentence or two: the behaviour today and the behaviour you want
                in its place. Concrete enough that someone could start work from this line alone.
                """),
        )),
        SectionSpec("motivation", "Motivation", (
            _prose("body", description="""
                Why the change is worth making: who it affects and what it costs to leave things as
                they are. If someone asked for it, say who and what they actually asked for.
                """
                ),
        )),
        SectionSpec("acceptance", "Acceptance", (
            _list("criteria", element_fields=("text",), description="""
                Each one checkable statement of what done looks like, phrased so it is unambiguously
                true or false once the change is made. Cover the new behaviour AND anything nearby
                that must keep working. A criterion that can only be settled by opinion is not one.
                """),
        )),
        SectionSpec("pull_request", "Pull Request", (
            _scalar("url", description="""
                The pull request URL created for this change.
                """),
        )),
        SectionSpec("resolution", "Resolution", (
            _list("changeCommits", element_fields=("sha", "message", "stale"), description="""
                Each a commit that delivers this change: the sha and its subject line. Record each
                as you make it rather than reconstructing the list at the end, and flag one stale
                once its sha has left history, for example after a rebase.
                """),
        )),
    ),
    commands=(
        set_scalar_cmd("change", "component"),
        set_prose_cmd("summary"),
        set_prose_cmd("motivation"),
        *list_cmds("acceptance", field="criteria", singular="criterion", label="acceptance criterion",
                   add_args=(_text(),)),
        set_scalar_cmd("pull_request", "url", name="setPullRequestUrl", label="pull request url",
                       legal_in=("review", "done")),
        transition_cmd("open", "draft -> open"),
        transition_cmd("submitForReview", "open -> review"),
        # review -> done marks the change built and reviewed, but not yet shippable or merged to main.
        transition_cmd("markDone", "review -> done"),
        transition_cmd("requestChanges", "review -> open", agency="either"),
        # close is a human gate: a person confirms the change is shippable/merged before it lands.
        transition_on_add_cmd("close", "done -> closed", section="resolution", field="changeCommits",
                     description="record a change commit AND close the change", agency="human",
                     add_args=(_text("sha"), _text("message"))),
        transition_on_add_cmd("closeWithoutCommit", "done -> closed", section="resolution", field="changeCommits",
                     description="record a closing note (message only, no commit) AND close the change", agency="human",
                     add_args=(_text("message"),)),
        *list_cmds("resolution", field="changeCommits", add_name="recordCommit", label="change commit",
                   remove=False, add_args=(_text("sha"), _text("message")),
                   legal_in=_COMMIT_LOG_STATES),
        set_element_field_cmd("resolution", field="changeCommits", singular="changeCommit",
                              name="markCommitStale", const=("stale", True),
                              description="flag a recorded commit as stale - its sha is no longer in history (e.g. after a rebase)",
                              legal_in=_COMMIT_LOG_STATES),
        transition_cmd("reopen", "closed -> open"),
        add_link_cmd(),
        set_title_cmd(),
    ),
    fsm=FSMSpec(
        name="SimpleChange",
        initial="draft",
        states=("draft", "open", "review", "done", "closed"),
        state_guidance=(
            ("draft", SIMPLE_CHANGE_DRAFT),
            ("open", SIMPLE_CHANGE_OPEN),
            ("review", REVIEW),
        ),
    ),
)

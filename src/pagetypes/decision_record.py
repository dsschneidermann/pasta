"""The `decision-record` page type."""

from __future__ import annotations

from ._stage_guidance import DECISION_RECORD_AUTHORING
from .core.specs import FSMSpec
from .core.args import _code_block, _text, _paragraph_text
from .core.commands import (
    add_link_cmd,
    set_title_cmd,
    blocks_cmds,
    list_cmds,
    set_prose_cmd,
    set_scalar_cmd,
    transition_cmd,
)
from .core.fields import SectionSpec, _blocks, _list, _prose, _scalar
from .core.pagetype import PageType

_DECISION_RECORD = PageType(
    tag="decision-record",
    name="Decision record",
    description=(
        "Records an architectural decision - its context, the options weighed, the choice made, "
        "and the consequences - as a durable dated rationale. Captures WHY the system is shaped "
        "the way it is; the shape itself belongs on an architecture page."
    ),
    sections=(
        SectionSpec("meta", "Meta", (
            _scalar("date", description="""
                The date the decision was taken, as YYYY-MM-DD - the date of the decision, not of
                the page. A record's weight decays as the system moves on, and a reader can only
                judge how much of it still applies against the real date.
                """),
            _scalar("scope", description="""
                What this decision governs: the subsystem, service, or cross-cutting concern it
                binds. Keep it to the narrowest scope the reasoning actually reaches - a record
                scoped to the whole system binds work it was never reasoned about.
                """),
            _list("deciders", element_fields=("name",), description="""
                Each one person or group who took this decision, one per element. Record who
                actually decided rather than everyone who was in the room, so a later reader
                knows who to ask once the context has gone stale.
                """),
        )),
        SectionSpec("context", "Context", (
            _prose("body", description="""
                The forces that made a decision necessary, written for a reader who was not there:
                the constraints in play, the state of the system at the time, and what was being
                traded against what. Say what made it hard. This is the section worth the most
                years later and the one skipped the most often, so write it before the decision
                itself - if the context does not make the decision feel necessary, it is not
                finished.
                """),
        )),
        SectionSpec("decision", "Decision", (
            _blocks("body", block_kinds=(_paragraph_text(), _code_block()), description="""
                What was decided, in the active voice and stated before any supporting detail, then
                the options that were seriously weighed and what ruled each one out. Use a code
                block for anything with a precise shape: an interface, a schema, a config. A record
                that names no rejected option is a note rather than a decision, because nothing in
                it explains why the alternatives are not still open.
                """),
        )),
        SectionSpec("consequences", "Consequences", (
            _blocks("body", block_kinds=(_paragraph_text(),),
                    description="""
                What this decision makes easy and what it makes hard, including the costs now to be
                lived with, what it forecloses, and the follow-on work it creates. Consequences
                that are all benefits mean the trade-off has not been thought through yet - the
                reader inheriting the cost is the one this section is written for.
                """),
        )),
        SectionSpec("relations", "Relations", (
            _scalar("supersededBy", description="""
                The page id of the decision record that replaces this one, recorded whenever a
                later decision overtakes it. A record is never edited to reverse itself - the
                reasoning that held at the time has to stay readable - so this pointer is what
                carries a reader forward to the reasoning that replaced it, and it is the one
                field that stays writable after the record is accepted.
                """),
        )),
    ),
    commands=(
        set_scalar_cmd("meta", "date"),
        set_scalar_cmd("meta", "scope"),
        *list_cmds("meta", field="deciders", add_args=(_text("name"),)),
        set_prose_cmd("context"),
        # Two blocks fields on one type, so each passes its own remove/reorder names.
        *blocks_cmds(
            "decision",
            remove_name="removeDecisionBlock", remove_desc="remove a decision block",
            reorder_name="reorderDecisionBlock",
            reorder_desc="move a decision block to an anchored position (precedingId guards a stale read)"),
        *blocks_cmds(
            "consequences",
            remove_name="removeConsequence", remove_desc="remove a consequence",
            reorder_name="reorderConsequence",
            reorder_desc="move a consequence to an anchored position (precedingId guards a stale read)"),
        # The one authoring command that opts back into the terminal state: a record is
        # overtaken by a later one long after it was accepted.
        set_scalar_cmd("relations", "supersededBy", label="superseding record",
                       legal_in=("authoring", "accepted")),
        transition_cmd("markAccepted", "authoring -> accepted"),
        transition_cmd("author", "accepted -> authoring"),
        add_link_cmd(),
        set_title_cmd(),
    ),
    fsm=FSMSpec(
        name="DecisionRecord",
        initial="authoring",
        states=("authoring", "accepted"),
        terminal_states=("accepted",),
        status_guidance=(("authoring", DECISION_RECORD_AUTHORING),),
    ),
)

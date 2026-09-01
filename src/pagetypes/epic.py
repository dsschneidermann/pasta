"""The `epic` page type, and the `agent-plan` pinned under it.

The two are declared together because they are designed against each other rather than
on their own, and reading one without the other would hide the coupling.
"""

from __future__ import annotations

from .core.specs import (
    AutoChildSpec,
    ChildStateGuard,
    ElementFSMSpec,
    FSMSpec,
    ParentStateGuard,
    RefCheck,
)
from .core.args import _boolean, _integer, _text
from .core.commands import (
    add_link_cmd,
    set_title_cmd,
    element_cmds,
    list_cmds,
    set_element_field_cmd,
    set_prose_cmd,
    set_scalar_cmd,
    transition_cmd,
)
from .core.fields import SectionSpec, _list, _prose, _scalar
from .core.pagetype import PageType

_QUESTION_FSM = ElementFSMSpec(
    name="Question",
    initial="open", states=("open", "answered"),
    transitions=(("answer", "open", "answered", "agent"),),
)


_VERDICTS = ("build-ready", "needs-changes", "needs-human-decision")
_SEVERITIES = ("blocking", "should-fix", "nit")
_EPIC_FINDING_ACTIONS = ("addWorkstream", "addAgent", "addDispatch", "addContract",
                         "addConstraint", "askQuestion", "edit")


_EPIC = PageType(
    tag="epic",
    name="Epic (major feature)",
    description=(
        "The root of a major feature too large for one feature-brief: it decomposes into several "
        "child feature-briefs and is built by subagents dispatched from its pinned agent plan. Use "
        "it when the work splits into parts that each ship on their own; for one feature use a "
        "feature-brief, and for a small self-contained change use a simple-change."
    ),
    sections=(
        SectionSpec("summary", "Summary", (
            _prose("body", description="""
                The intent in a sentence or two: what you want to build and why it is worth building.
                Write it before reading any code, because grounding searches the repo from this line.
                State the outcome you want, not the implementation you imagine. If the outcome can be
                stated without joining two independent things with 'and', it is probably one
                feature-brief rather than an epic.
                """),
        )),
        SectionSpec("components", "Components", (
            _list("items", element_fields=("name",), description="""
                Each one part of the system this epic touches, named as a real file, module, or
                subsystem path you CONFIRMED exists while grounding. One per element. This is the
                epic's whole surface area: a component missed here becomes a workstream nobody owns,
                and a component two workstreams both touch is a collision found only when the second
                agent's edits land on the first agent's file.
                """),
        )),
        SectionSpec("constraints", "Constraints", (
            _list("items", element_fields=("text",), description="""
                Each one project-wide requirement every workstream must respect, with its exact
                values copied verbatim: version floors, dependency limits, naming and copy rules,
                platform and performance targets. Every dispatched agent inherits these and none of
                them can see the others' work, so a constraint recorded vaguely here is one that
                each agent interprets differently and the integration review has to reconcile.
                """),
        )),
        SectionSpec("conflicts", "Conflicts", (
            _list("items", element_fields=("text",), description="""
                Each one collision with what already exists, found while grounding: prior art that
                already solves part of this, an interface this epic would break, or a competing
                in-flight change. Name the file or page it collides with and say what has to give.
                Record collisions BETWEEN the intended workstreams here too, because two agents
                editing one file is the failure this list exists to catch before either is dispatched.
                """),
        )),
        SectionSpec("documentation", "Documentation", (
            _list("items", element_fields=("text",), description="""
                Each an existing pasta doc or architecture page this epic will make stale, found
                while grounding. Name the page and the specific part of it that will need to change,
                so reconciling it when the work lands is mechanical rather than a fresh investigation.
                """),
        )),
        SectionSpec("workstreams", "Workstreams", (
            _list("items", element_fields=("name", "briefId", "scope", "dependsOn"), description="""
                Each one child feature-brief of this epic: its name, the page id of that brief in
                briefId, the slice of the epic it owns in scope, and the workstreams that must finish
                before it in dependsOn. CREATE the feature-brief as a child of this epic and record
                its id here. The ship gate can only check briefs that exist, so a workstream listed
                without a brief page lets an epic ship having built nothing. Split along seams where
                one agent can work without reading another's code, and keep each slice something that
                could ship on its own.
                """),
        )),
        SectionSpec("contracts", "Shared contracts", (
            _list("items", element_fields=("name", "owner", "shape"), description="""
                Each one interface that crosses a workstream boundary: its name, the workstream that
                defines it in owner, and its exact signature or data shape in shape. A dispatched
                agent sees only its own brief, so anything two workstreams must agree on is written
                here before either starts. Record the real signature rather than a description of
                one: 'returns the parsed config' is not a contract, a written-out function signature
                with its argument and return types is.
                """),
        )),
        SectionSpec("questions", "Questions", (
            _list("items", element_fields=("text", "answer", "needsHuman", "status"),
                  element_fsm=_QUESTION_FSM, description="""
                Each one open question that blocks or reshapes the decomposition, asked as a single
                decidable question rather than a topic. Set needsHuman when only a person can settle
                it: a product call, a trade-off with no technically correct answer, or anything
                carrying cost or policy consequences. Answer it here once decided, because a question
                still open when the dispatches begin is answered differently by every agent that
                reaches it.
                """),
        )),
        # The plan review's outcome, populated in `planReview`: the decomposition and the agent plan
        # are reviewed together, before any agent is dispatched.
        SectionSpec("review", "Plan review", (
            _scalar("verdict", choices=_VERDICTS, description="""
                The review outcome for the decomposition and the agent plan together. build-ready:
                the workstreams cover the epic with no gaps and no overlaps, and an agent could be
                dispatched against any one of them without getting stuck. needs-changes: at least one
                blocking finding must be applied before dispatching. needs-human-decision: the work
                cannot proceed until a person settles a question. Approve unless there are serious
                gaps, meaning a component no workstream owns, two workstreams owning the same file, a
                contract two workstreams need that nobody defines, or an agent mission too vague to
                act on. Minor wording preferences are never a reason to withhold build-ready.
                """),
            _list("findings", element_fields=("issue", "severity", "action"), description="""
                Each one plan-review finding: what is wrong, why it matters before any agent is
                dispatched, its severity, and the action taken to apply it. blocking means an agent
                would build the wrong thing or two agents would collide; should-fix is real but
                survivable; nit is polish. Record the finding here AND make the edit its action
                names, so the plan and this record agree. Say which workstream or agent it applies to.
                """),
        )),
    ),
    commands=(
        set_prose_cmd("summary"),
        *list_cmds("components", add_args=(_text("name"),)),
        *list_cmds("constraints", add_args=(_text(),)),
        *list_cmds("conflicts", add_args=(_text(),)),
        *list_cmds("documentation", add_args=(_text(),)),
        *list_cmds("workstreams", add_args=(
            _text("name"), _text("briefId", required=False), _text("scope"),
            _text("dependsOn", required=False))),
        *list_cmds("contracts", add_args=(_text("name"), _text("owner"), _text("shape"))),
        # Questions carry the same surface as a feature-brief's: askQuestion (no remove), an answer
        # transition that also writes the answer, and an escalate that raises the needsHuman flag.
        *list_cmds("questions", add_name="askQuestion", label="question", remove=False,
                   add_args=(_text(), _boolean("needsHuman", required=False))),
        *element_cmds("questions", marks=(
            ("answerQuestion", "answer", "answer a question (open -> answered)", (_text("answer"),)),)),
        set_element_field_cmd("questions", name="escalateQuestion",
                              description="flag a question as awaiting a human", const=("needsHuman", True)),
        set_scalar_cmd("review", "verdict", name="setReviewVerdict", choices=_VERDICTS,
                       legal_in=("planReview",)),
        *list_cmds("review", field="findings", label="finding",
                   add_args=(_text("issue"), _text("severity", choices=_SEVERITIES),
                             _text("action", required=False, choices=_EPIC_FINDING_ACTIONS,
                                   description="how the finding was applied to the plan")),
                   legal_in=("planReview",)),
        # draft -> grounding needs only the intent: grounding reads the real repo from that one line.
        transition_cmd("beginGrounding", "draft -> grounding", requires=(("summary", "body"),)),
        # grounding -> decomposition is gated on the grounding having produced a base. From here the
        # child feature-briefs and the agent plan are authored.
        transition_cmd("beginDecomposition", "grounding -> decomposition", requires=(
            ("components", "items"),
            ("constraints", "items"),
            ("conflicts", "items"),
            ("documentation", "items"),
        )),
        # decomposition -> planReview reviews the split AND the agent plan together, before any agent
        # is dispatched: the agent-plan child must be `ready` (a page-status guard, store-checked).
        transition_cmd("submitPlan", "decomposition -> planReview",
                       requires=(("workstreams", "items"), ("questions", "items")),
                       guards=(ChildStateGuard("agent-plan", ("ready",),
                                               "the agent plan must be marked ready"),)),
        # planReview -> executing requires a verdict to have been recorded (the review happened).
        # `build-ready` is the signal to proceed; `needs-changes` should `reviseDecomposition`
        # instead. The verdict's VALUE is a soft guide (requires only checks presence).
        transition_cmd("approvePlan", "planReview -> executing", requires=(("review", "verdict"),)),
        transition_cmd("reviseDecomposition", "planReview -> decomposition (send the plan back)"),
        # executing -> review is guarded on every dispatch having been accepted (an element-status
        # guard across the pinned agent plan, checked in the store).
        transition_cmd("submitForReview", "executing -> review", guards=(
            ChildStateGuard("agent-plan", ("accepted",), "every dispatch must be accepted",
                            section="dispatches", field="items"),)),
        transition_cmd("reopenDecomposition", "executing -> decomposition"),
        transition_cmd("requestChanges", "review -> executing", agency="either"),
        # `ship` is a human gate AND is guarded: every child feature-brief must itself be `shipped`
        # (a page-status guard over the epic's NON-pinned children, checked in the store).
        transition_cmd("ship", "review -> shipped (human gate)", agency="human", guards=(
            ChildStateGuard("feature-brief", ("shipped",),
                            "every child feature-brief must be shipped"),)),
        transition_cmd("abandon", "drop the work -> abandoned", agency="human",
                       legal_in=("draft", "grounding", "decomposition", "planReview",
                                 "executing", "review")),
        add_link_cmd(),
        set_title_cmd(),
    ),
    fsm=FSMSpec(
        name="Epic",
        initial="draft",
        states=("draft", "grounding", "decomposition", "planReview",
                "executing", "review", "shipped", "abandoned"),
        terminal_states=("shipped", "abandoned"),
    ),
    # On createPage, create the pinned agent plan in the same commit; author into it.
    auto_children=(AutoChildSpec("agent-plan"),),
)


# An epic's pinned agent plan may only be finalized once the epic has reached `decomposition` or
# later - not while it is still in `draft` or `grounding` (the base is still being established),
# nor once `abandoned`. Enforced in the store.
_EPIC_IN_DECOMPOSITION_OR_LATER = ParentStateGuard(
    parent_type="epic",
    required_statuses=("decomposition", "planReview", "executing", "review", "shipped"),
    message="the epic must be in decomposition or later",
)


# Tiers rather than model ids: an id rots between releases, and a stale enum rejects valid input.
_MODEL_TIERS = ("cheap", "standard", "capable")


# One run of one agent against one workstream. `accepted` is the SINGLE success terminal, which is
# what lets an epic's `submitForReview` gate on it with a ChildStateGuard whose allowed set is just
# `accepted`. A fix round is a `redispatch` of the same element, not a new one, so the element itself
# records how many attempts a workstream took.
_DISPATCH_FSM = ElementFSMSpec(
    name="Dispatch",
    initial="pending", states=("pending", "dispatched", "reported", "accepted", "blocked"),
    transitions=(("dispatch", "pending", "dispatched", "agent"),
                 ("report", "dispatched", "reported", "agent"),
                 ("accept", "reported", "accepted", "agent"),
                 ("redispatch", "reported", "dispatched", "agent"),   # a fix round
                 ("redispatch", "blocked", "dispatched", "agent"),    # unblocked, try again
                 ("block", "dispatched", "blocked", "agent"),
                 ("block", "reported", "blocked", "agent")),
    checkmark_done="accepted",
)


_AGENT_PLAN = PageType(
    tag="agent-plan",
    name="Agent plan",
    description=(
        "Which subagents an epic creates, the order they are dispatched in, and how their results "
        "are reported back onto the feature-briefs. Auto-created as a child of an epic."
    ),
    sections=(
        SectionSpec("agents", "Agents", (
            _list("items", element_fields=("role", "model", "mission", "reports"), description="""
                Each one subagent this epic creates: the role it plays, the model tier it runs on,
                the mission it is given, and what it must hand back. Write the mission for a fresh
                agent with no history, because a dispatched agent is given exactly this and never
                inherits the controller's context. Name the tier on every agent: an omitted model
                silently inherits the session's most capable and most expensive one. Use cheap for
                mechanical single-file work whose content is already written down, standard for
                multi-file integration and judgement, and capable for architecture and final review.
                Define a role once and dispatch it many times rather than writing a near-identical
                agent per workstream.
                """),
        )),
        SectionSpec("dispatches", "Dispatches", (
            _list("items",
                  element_fields=("agentId", "workstreamId", "wave", "worktree", "handoff",
                                  "outcome", "blocker", "status"),
                  element_fsm=_DISPATCH_FSM, description="""
                Each one run of one agent against one workstream, in the order they are sent: the
                agent element id in agentId, the parent epic's workstream element id in
                workstreamId, the parallel group in wave, the checkout it works in in worktree, and
                in handoff the facts this run needs that neither the agent definition nor the target
                brief can know. Dispatches sharing a wave may run at once; a higher wave starts only
                once every lower one is accepted. Give every dispatch in a wave a DIFFERENT
                worktree - concurrent runs in one checkout overwrite each other's edits - and record
                it here rather than on the agent, because a role is defined once and dispatched many
                times, so it is the run that occupies a checkout and not the definition. Keep
                handoff to the contracts and decisions this run actually touches, never the
                session's accumulated history, and point at large artifacts such as diffs and
                reports by file path so they stay out of the controller's context. Record what came
                back in outcome and what stopped it in blocker. A fix round is a redispatch of this
                element, not a new one, so the element stays the record of how many attempts the
                workstream took (element-FSM pending -> dispatched -> reported -> accepted).
                """),
        )),
        SectionSpec("reporting", "Reporting contract", (
            _prose("body", description="""
                How a finished agent's work lands back on its feature-brief, written as a checklist
                the controller can verify before accepting a dispatch: which of the brief's own
                commands must have been run, what status the brief and its plans must be left in,
                and where the agent's full written report lives. An agent reports by driving its own
                brief, not by handing prose back, so name the page state you expect rather than the
                summary you want to read. Accepting a dispatch without this check is how a
                workstream comes to be believed done while its brief still says otherwise.
                """),
        )),
    ),
    commands=(
        *list_cmds("agents", label="agent", legal_in=("draft",),
                   add_args=(_text("role"), _text("model", choices=_MODEL_TIERS),
                             _text("mission"), _text("reports"))),
        # `singular=` is required on both list_cmds and element_cmds here: the plural rule would
        # derive 'dispatche' from 'dispatches' and name the commands addDispatche / dispatcheId.
        # workstreamId is integrity-checked against the PARENT epic's workstreams list.
        # `worktree` is required like agentId/workstreamId/wave: a run without a named checkout is
        # the one gap that turns a same-wave parallel dispatch into two agents editing one copy.
        *list_cmds("dispatches", singular="dispatch", label="dispatch", legal_in=("draft",),
                   add_args=(_text("agentId"), _text("workstreamId"), _integer("wave"),
                             _text("worktree"), _text("handoff", required=False)),
                   ref_check=RefCheck(arg="workstreamId", scope="parent",
                                      section="workstreams", field="items")),
        # Run marks stay legal once the plan is `ready`: the record moves while the plan does not.
        # Only the structural edits above are `draft`-only.
        *element_cmds("dispatches", singular="dispatch", legal_in=("draft", "ready"), marks=(
            ("markDispatched", "dispatch", "mark a dispatch as sent to its agent"),
            ("reportDispatch", "report", "record what the agent returned", (_text("outcome"),)),
            ("acceptDispatch", "accept", "accept a reported dispatch as done"),
            ("redispatch", "redispatch", "send a reported or blocked dispatch back to an agent"),
            ("blockDispatch", "block", "record a dispatch as blocked", (_text("blocker"),)),
        )),
        set_prose_cmd("reporting", label="reporting contract", legal_in=("draft",)),
        transition_cmd("markReady", "draft -> ready",
                       requires=(("agents", "items"), ("dispatches", "items"), ("reporting", "body")),
                       parent_guards=(_EPIC_IN_DECOMPOSITION_OR_LATER,)),
        transition_cmd("reopen", "ready -> draft (unlocks structural edits)"),
        add_link_cmd(),
        set_title_cmd(),
    ),
    fsm=FSMSpec(
        name="AgentPlan",
        initial="draft",
        states=("draft", "ready"),
    ),
)

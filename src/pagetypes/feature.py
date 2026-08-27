"""The feature lifecycle: a `feature-brief` and the three children pinned under it.

The four are declared together because they are designed against each other rather than
on their own, and reading one without the others would hide the coupling.
"""

from __future__ import annotations

from ._stage_guidance import (
    BUILDING,
    FEATURE_BRIEF_REVIEW,
    GROUNDING,
    PLANNING,
    PLAN_REVIEW,
    SPEC,
)
from . import (
    AutoChildSpec,
    ChildStateGuard,
    ElementBlocksSpec,
    ElementFSMSpec,
    FSMSpec,
    PageType,
    ParentStateGuard,
    RefCheck,
    SectionSpec,
    _blocks,
    _boolean,
    _code_block,
    _list,
    _paragraph_runs,
    _prose,
    _scalar,
    _text,
    _heading_text,
    _paragraph_text,
    BlockKindSpec,
    add_link_cmd,
    blocks_cmds,
    element_blocks_cmds,
    element_cmds,
    list_cmds,
    set_element_field_cmd,
    set_prose_cmd,
    set_scalar_cmd,
    set_title_cmd,
    transition_cmd,
)

_STEP_FSM = ElementFSMSpec(
    name="Step",
    initial="todo", states=("todo", "done"),
    transitions=(("markDone", "todo", "done", "agent"), ("reopen", "done", "todo", "agent")),
    checkmark_done="done",                       # a step is a checkbox: initial "todo" -> [ ], "done" -> [x]
)
_CASE_FSM = ElementFSMSpec(
    name="Case",
    initial="pending", states=("pending", "passed", "failed"),
    transitions=(("pass", "pending", "passed", "agent"), ("fail", "pending", "failed", "agent")),
    checkmark_done="passed",                     # initial "pending" -> [ ], "passed" -> [x], "failed" -> no box
)
_QUESTION_FSM = ElementFSMSpec(
    name="Question",
    initial="open", states=("open", "answered"),
    transitions=(("answer", "open", "answered", "agent"),),
)                                                # no checkmark_done -> open/answered render without a box


_VERDICTS = ("build-ready", "needs-changes", "needs-human-decision")
_SEVERITIES = ("blocking", "should-fix", "nit")
_FINDING_ACTIONS = ("addStep", "addCase", "addConstraint", "askQuestion", "edit")


# States allowing modifications to the commit log.
_COMMIT_LOG_STATES = ("building", "review", "shipped")


_FEATURE_BRIEF = PageType(
    tag="feature-brief",
    name="Feature (root)",
    description=(
        "The root of a feature you intend to build - drives new work from intent through "
        "grounding, planning, and a plan review to a human review gate. Lifecycle transitions "
        "are gated on the required content for that stage being present first. Every stage "
        "biases toward caution over speed: surface a confusion rather than assume past it, and "
        "keep what you write to the smallest thing that solves the stated problem."
    ),
    sections=(
        SectionSpec("summary", "Summary", (
            _prose("body", description="""
                The feature intent in a sentence or two: what you want to build and why it is worth
                building, restated in your own words rather than echoed back - where your
                restatement and the ask diverge is the first thing to settle. Write it before
                reading any code, because grounding searches the repo from this line. State the
                outcome you want, not the implementation you imagine, and say so here if the ask
                itself looks wrong or underspecified rather than quietly building past it.
                """),
        )),
        SectionSpec("components", "Components", (
            _list("items", element_fields=("name", "text"), description="""
                Each one part of the system this feature touches, named as a real file, module, or
                subsystem path you CONFIRMED exists while grounding. One per element. List only what
                the work will actually read or change: a guessed component sends the whole plan down
                the wrong path, and a missing one is discovered mid-build. Reach each one by
                following callers and imports rather than by guessing from names, so the list is the
                real blast radius. Say for each whether it holds pure logic or performs effects -
                I/O, storage, network, clock, randomness - because that is what decides where new
                code belongs.
                """),
        )),
        SectionSpec("constraints", "Constraints", (
            _list("items", element_fields=("text",), description="""
                Each one project-wide requirement the work must respect, with its exact values copied
                verbatim: version floors, dependency limits, naming and copy rules, platform and
                performance targets. Every plan step and every review inherits these, so a constraint
                recorded vaguely is a constraint that gets violated. Copy each value from where it is
                actually declared rather than from memory, and mark one you inferred rather than
                read, so a guess is not later spent as a fact.
                """),
        )),
        SectionSpec("conflicts", "Conflicts", (
            _list("items", element_fields=("text",), description="""
                Each one collision with what already exists, found while grounding: prior art that
                already solves part of this, an interface this feature would break, or a competing
                in-flight change. Name the file or page it collides with and say what has to give.
                Code that looks wrong, dead, or redundant belongs here as a collision to raise, not
                as something the build quietly deletes on its way past.
                """),
        )),
        SectionSpec("documentation", "Documentation", (
            _list("items", element_fields=("text",), description="""
                Each an existing pasta doc, architecture page or ADR this feature will make stale,
                found while grounding. Name the page and the specific part of it that will need to
                change, so reconciling it is mechanical rather than an investigation.
                """),
        )),
        SectionSpec("questions", "Questions", (
            _list("items", element_fields=("text", "answer", "needsHuman", "status"), element_fsm=_QUESTION_FSM, description="""
                Each one open question that blocks or reshapes the plan, asked as a single decidable
                question rather than a topic. A judgment call the user might reasonably disagree
                with is a question, not a decision to make quietly, and where the ask carries
                several readings all of them go here rather than one being picked in silence. Set
                needsHuman when only a person can settle it: a product call, a trade-off with no
                technically correct answer, or anything carrying cost or policy consequences.
                Answer it here once decided, then record the settled decision in the spec so it is
                not reopened during the build.
                """),
        )),
        # The plan review's outcome (populated in the `planReview` state): a verdict plus a summary
        # of the findings the review raised. The findings themselves must also be applied as edits.
        SectionSpec("review", "Plan review", (
            _scalar("verdict", choices=_VERDICTS, description="""
                The plan-review outcome. build-ready: an implementer could follow the plan end to end
                without getting stuck. needs-changes: at least one blocking finding must be applied
                before building. needs-human-decision: the plan cannot proceed until a person settles
                a question. Approve unless there are serious gaps, meaning a spec requirement no task
                covers, contradictory steps, placeholder content, or steps too vague to act on. A
                plan that builds more than the spec asked for, or abstracts something used once, is a
                serious gap too and not a matter of taste. Minor wording and style preferences are
                never a reason to withhold build-ready. Steps that say to update documentation need
                to be removed as that is a later activity once the implementation has landed.
                """),
            _list("findings", element_fields=("issue", "severity", "action"), description="""
                Each one plan-review finding: what is wrong, why it matters for implementation, its
                severity, and the action taken to apply it to the plan. blocking means an implementer
                would build the wrong thing or get stuck; should-fix is real but survivable; nit is
                polish. Record the finding here AND make the edit its action names, so the plan and
                this record agree. Say where it applies, by step or spec section.
                """),
        )),
        SectionSpec("pull_request", "Pull Request", (
            _scalar("url", description="""
                The pull request URL created for this feature review.
                """),
        )),
        SectionSpec("commits", "Commits", (
            _list("items", element_fields=("sha", "message", "stale"), description="""
                Each a commit made for this feature while building: its sha and subject line. Record
                each as you make it rather than reconstructing the list at the end, and flag one
                stale once its sha has left history, for example after a rebase.
                """),
        )),
    ),
    commands=(
        set_prose_cmd("summary"),
        *list_cmds("components", add_args=(_text("name"), _text())),
        *list_cmds("constraints", add_args=(_text(),)),
        *list_cmds("conflicts", add_args=(_text(),)),
        *list_cmds("documentation", add_args=(_text(),)),
        # Questions: a special add name (askQuestion) with an optional needsHuman flag and no remove,
        # an element-transition answer that also writes the answer, and an escalate that sets a flag.
        *list_cmds("questions", add_name="askQuestion", label="question", remove=False,
                   add_args=(_text(), _boolean("needsHuman", required=False))),
        *element_cmds("questions", marks=(
            ("answerQuestion", "answer", "answer a question (open -> answered)", (_text("answer"),)),)),
        set_element_field_cmd("questions", name="escalateQuestion",
                              description="flag a question as awaiting a human", const=("needsHuman", True)),
        # Plan-review recording - legal only in the `planReview` state, where the authored plan is
        # reviewed before any code is written. `approvePlan` (planReview -> building) requires a
        # verdict to have been recorded first (see its `requires=` below).
        set_scalar_cmd("review", "verdict", name="setReviewVerdict", choices=_VERDICTS,
                       legal_in=("planReview",)),
        *list_cmds("review", field="findings", label="finding",
                   add_args=(_text("issue"), _text("severity", choices=_SEVERITIES),
                             _text("action", required=False, choices=_FINDING_ACTIONS,
                                   description="how the finding was applied to the plan")),
                   legal_in=("planReview",)),
        set_scalar_cmd("pull_request", "url", name="setPullRequestUrl", label="pull request url",
                       legal_in=("review",)),
        *list_cmds("commits", add_name="recordCommit", label="recorded commit", remove=False,
                   add_args=(_text("sha"), _text("message")),
                   legal_in=_COMMIT_LOG_STATES),
        set_element_field_cmd("commits", name="markCommitStale", const=("stale", True),
                              description="flag a recorded commit as stale - its sha is no longer in history (e.g. after a rebase)",
                              legal_in=_COMMIT_LOG_STATES),
        # draft -> grounding needs only the intent: grounding reads the real repo from this
        # one-line summary and proposes the grounded base (components/constraints/conflicts + plans).
        transition_cmd("beginGrounding", "draft -> grounding", requires=(("summary", "body"),)),
        # grounding -> spec is gated on the grounding having produced a base: the components it
        # identified as touched, and what constrains / collides with / is made stale by the work.
        # From here only the feature-spec is authored - the two plans are held back.
        transition_cmd("beginSpec", "grounding -> spec", requires=(
            ("components", "items"),
            ("constraints", "items"),
            ("conflicts", "items"),
            ("documentation", "items"),
        )),
        transition_cmd("beginPlanning", "spec -> planning", requires=(("questions", "items"),), guards=(
            ChildStateGuard("feature-spec", "sealed", "the feature spec must be sealed"),
        )),
        # planning -> planReview is gated on the three planning artifacts being finalized: the
        # implementation-plan and testing-plan children each `ready` and the feature-spec `sealed`
        # (page-status guards checked across the brief's children by the store). The spec guard is
        # kept even though beginPlanning already required it - the spec can be `reopen`ed mid-planning,
        # and an unsealed spec must not reach review. There must be an authored plan before it can
        # be reviewed.
        transition_cmd("submitPlan", "planning -> planReview", guards=(
            ChildStateGuard("implementation-plan", "ready", "the implementation plan must be marked ready"),
            ChildStateGuard("testing-plan", "ready", "the testing plan must be marked ready"),
            ChildStateGuard("feature-spec", "sealed", "the feature spec must be sealed"),
        )),
        # planReview -> building requires a verdict to have been recorded (the review happened).
        # The verdict is a soft guide (requires only checks presence).
        transition_cmd("approvePlan", "planReview -> building", requires=(("review", "verdict"),)),
        transition_cmd("revisePlan", "planReview -> planning (send the plan back)"),
        transition_cmd("submitForReview", "building -> review"),
        transition_cmd("reopenPlanning", "building -> planning"),
        transition_cmd("requestChanges", "review -> building", agency="either"),
        # `ship` is a human gate AND is guarded: every implementation-plan step must be `done`
        # and every testing-plan case `passed` (checked across the brief's child pages by the store).
        transition_cmd("ship", "review -> shipped (human gate)", agency="human", guards=(
            ChildStateGuard("implementation-plan", "done", "every implementation-plan step must be done",
                            section="steps", field="items"),
            ChildStateGuard("testing-plan", "passed", "every testing-plan case must be passed",
                            section="cases", field="items"),
        )),
        transition_cmd("abandon", "drop the work -> abandoned", agency="human",
                       legal_in=("draft", "grounding", "spec", "planning", "planReview", "building", "review")),
        add_link_cmd(),
        set_title_cmd(),
    ),
    fsm=FSMSpec(
        name="FeatureBrief",
        initial="draft",
        states=("draft", "grounding", "spec", "planning", "planReview", "building", "review",
                "shipped", "abandoned"),
        terminal_states=("shipped", "abandoned"),
        state_guidance=(
            ("grounding", GROUNDING),
            ("spec", SPEC),
            ("planning", PLANNING),
            ("planReview", PLAN_REVIEW),
            ("building", BUILDING),
            ("review", FEATURE_BRIEF_REVIEW),
        ),
    ),
    # On createPage, create the three pinned children in the same commit; author into those.
    auto_children=(AutoChildSpec("implementation-plan"), AutoChildSpec("testing-plan"),
                   AutoChildSpec("feature-spec")),
)


# The two guards that stage the pinned children, both enforced in the store: the spec is unlocked
# a stage before the plans are.
_FEATURE_IN_SPEC_OR_LATER = ParentStateGuard(
    parent_type="feature-brief",
    required_statuses=("spec", "planning", "planReview", "building", "review", "shipped"),
    message="the feature-brief must be in spec or later",
)

_FEATURE_IN_PLANNING_OR_LATER = ParentStateGuard(
    parent_type="feature-brief",
    required_statuses=("planning", "planReview", "building", "review", "shipped"),
    message="the feature-brief must be in planning or later",
)


_FEATURE_SPEC = PageType(
    tag="feature-spec",
    name="Spec",
    description=(
        "The detailed product/UX specification for a feature, authored during the brief's `spec` "
        "stage on top of the grounded base. Sealing it allows advancing the brief to planning, so "
        "it is settled before a single step or case is written. Auto-created as a child of a "
        "feature-brief."
    ),
    sections=(
        SectionSpec("overview", "Overview", (
            _prose("body", description="""
                What this spec covers and what it deliberately leaves out, in a short paragraph
                written for someone who knows the codebase but not this feature. Keep the scope to one
                subsystem: a spec spanning several independent subsystems should be split into
                separate features, each able to ship on its own.
                """),
        )),
        SectionSpec("design", "Design", (
            _blocks("body", block_kinds=(
                _paragraph_text(),
                _heading_text(),
                _code_block(),
            ), description="""
                The design in enough detail that a plan can be written from it without making further
                decisions: the behaviour, the interfaces with their exact signatures and types, the
                data shapes, the states, and the error paths. Separate the pure logic from the code
                that performs effects and give each its own interfaces: what is a function of its
                inputs alone, and what needs I/O, storage, the clock or randomness. The rules belong
                on the pure side, and the effectful side should be thin enough to hold none of them.
                Use a heading per area and a code block for anything with a precise shape. No TBDs
                and no 'handle edge cases' placeholders, nothing that contradicts another part of the
                spec, and nothing that was not asked for. Emphasis and links are structured inline
                runs, not markdown syntax.
                """),
        )),
        SectionSpec("decisions", "Decisions", (
            _blocks("body", block_kinds=(
                BlockKindSpec("decision", args=(_text("questionId"), _text()),
                              ref_check=RefCheck(arg="questionId", scope="parent",
                                                 section="questions", field="items")),
            ), description="""
                One decision block per resolved question, linking the brief question it answers: the
                decision taken, the alternatives rejected, and why. This is what keeps a settled
                question from being reopened mid-build, so record the reasoning, not just the outcome.
                """),
        )),
    ),
    # Every authoring command is allowed in `draft`: sealing the spec locks ALL edits, so a
    # `sealed` spec is frozen and must be `reopen`ed to change.
    commands=(
        set_prose_cmd("overview"),
        # Each field's add and set are generated from the same declaration the validator reads.
        *blocks_cmds(
            "design",
            remove_name="removeDesignBlock", remove_desc="remove a design block",
            reorder_name="reorderDesignBlock",
            reorder_desc="move a design block to an anchored position (precedingId guards a stale read)"),
        *blocks_cmds(
            "decisions",
            remove_name="removeDecision", remove_desc="remove a decision",
            reorder_name="reorderDecision",
            reorder_desc="move a decision to an anchored position (precedingId guards a stale read)"),
        transition_cmd("markSealed", "draft -> sealed (locks authoring)",
                       requires=(("overview", "body"), ("design", "body"), ("decisions", "body")),
                       parent_guards=(_FEATURE_IN_SPEC_OR_LATER,)),
        transition_cmd("reopen", "sealed -> draft (unlocks authoring)"),
        add_link_cmd(),
        set_title_cmd(),
    ),
    fsm=FSMSpec(
        name="FeatureSpec",
        initial="draft",
        states=("draft", "sealed"),
        terminal_states=("sealed",),
    ),
)



_IMPLEMENTATION_PLAN = PageType(
    tag="implementation-plan",
    name="Implementation plan",
    description="The step-by-step build plan for a feature. Auto-created as a child of a feature-brief.",
    sections=(
        SectionSpec("steps", "Steps", (
            _list("items", element_fields=("detail", "status"), element_fsm=_STEP_FSM,
                  element_blocks=(ElementBlocksSpec("detail", (_paragraph_runs(), _code_block())),),
                  description="""
                Each one action an implementer can finish in a few minutes, ordered, written for a
                skilled developer who knows nothing about this codebase or its domain. Name the exact
                files to create or modify. Work test-first: write the failing test, run it and see it
                fail, write the minimal code to pass, run it and see it pass, commit. Keep a step on
                one side of the pure/effectful line - a step that adds a rule changes pure logic, a
                step that wires it to storage, the network or the clock changes the shell around it -
                and order the pure side first, so what depends on it has something settled to call.
                Put the actual content the step needs in the step as blocks: a code block for
                a snippet or the exact command to run and the output to expect, a paragraph for
                prose. Never write 'TBD', 'add error handling', 'write tests for the above', or 'same
                as step N' - repeat the detail instead, because steps are read out of order and in
                isolation. Mark a step done only once its test passes (element-FSM todo <-> done).
                """),
        )),
        SectionSpec("dataModels", "Data models", (
            _blocks("models", block_kinds=(_code_block(),), description="""
                One code block per data shape this feature introduces or changes, written as real
                declarations rather than prose: field names, types, and which are optional. Steps
                refer to these by name, so the names and types here must match the ones the steps use
                exactly - a shape called one thing here and another in a step is a bug.
                """),
        )),
    ),
    commands=(
        # The add carries the step's content, so one command writes a whole step and a batch of
        # them never has to name an id it has not committed.
        *list_cmds("steps", label="step", legal_in=("draft",), add_args=(),
                   element_blocks=("detail",)),
        # The step's detail, appended to once the step exists - without this an element's blocks
        # would be write-once, and fixing one would cost the step its id and its todo/done status.
        *element_blocks_cmds("steps", "detail", legal_in=("draft",)),
        # Execution-status marks stay legal once the plan is `ready`: progress is recorded while
        # building against a finalized plan. Only the structural edits above are `draft`-only.
        *element_cmds("steps", legal_in=("draft", "ready"),
                      marks=(("markStepDone", "markDone", "mark a step done"),
                             ("markStepTodo", "reopen", "reopen a step"))),
        # The field key is `models` under section `dataModels`, so the derived label would be
        # `models`; label= keeps the name the surface already reads with.
        *blocks_cmds(
            "dataModels", field="models", label="dataModels",
            remove_name="removeDataModel", remove_desc="remove a data-model block",
            reorder_name="reorderDataModel",
            reorder_desc="move a data-model block to an anchored position (precedingId guards a stale read)",
            legal_in=("draft",)),
        transition_cmd("markReady", "draft -> ready", requires=(("steps", "items"),),
                       parent_guards=(_FEATURE_IN_PLANNING_OR_LATER,)),
        transition_cmd("reopen", "ready -> draft (unlocks structural edits)"),
        add_link_cmd(),
        set_title_cmd(),
    ),
    fsm=FSMSpec(
        name="ImplementationPlan",
        initial="draft",
        states=("draft", "ready"),
    ),
)


_TESTING_PLAN = PageType(
    tag="testing-plan",
    name="Testing plan",
    description="The verification cases for a feature. Auto-created as a child of a feature-brief.",
    sections=(
        SectionSpec("cases", "Cases", (
            _list("items", element_fields=("text", "status"), element_fsm=_CASE_FSM, description="""
                Each one concrete check that proves the feature works, written so its outcome is
                unambiguous: the setup, the action, and the expected result. Verify real behaviour
                rather than mocked behaviour, and cover the failure and edge paths the spec implies,
                not just the happy one. Check pure logic directly - inputs in, result out, no setup
                and no test doubles - and keep the heavier setup for the thin effectful shell, where
                a few cases usually cover it. Name the test that carries the case where one exists.
                A case that cannot fail proves nothing. Mark a case passed only from a run you
                actually saw, and failed rather than quietly leaving it pending (element-FSM pending
                -> passed/failed).
                """),
        )),
    ),
    commands=(
        *list_cmds("cases", label="case", legal_in=("draft",),
                   add_args=(_text(),)),
        # Execution-status marks stay legal once the plan is `ready`: test results are recorded
        # while building against a finalized plan. Only the structural edits above are `draft`-only.
        *element_cmds("cases", legal_in=("draft", "ready"),
                      marks=(("markCasePassed", "pass", "mark a case passed"),
                             ("markCaseFailed", "fail", "mark a case failed"))),
        transition_cmd("markReady", "draft -> ready", requires=(("cases", "items"),),
                       parent_guards=(_FEATURE_IN_PLANNING_OR_LATER,)),
        transition_cmd("reopen", "ready -> draft (unlocks structural edits)"),
        add_link_cmd(),
        set_title_cmd(),
    ),
    fsm=FSMSpec(
        name="TestingPlan",
        initial="draft",
        states=("draft", "ready"),
    ),
)

"""The stage instructions a page type hands an agent on entering a status.

A page type declares these on its FSM as `status_guidance`. They live here rather than
inline so one working discipline can reach several page types, and so the text can be
read and revised as prose instead of between a page type's sections and commands. The
leading underscore sorts this module above every page-type module that draws on it.

A constant is named for its status. A bare name is the only text for that status anywhere;
a name carrying a page-type prefix is one type's own take on a status name that several
types claim.

Text is authored indented and normalized by `FSMSpec.__post_init__`, which dedents and
strips it exactly as it does a field description.
"""


# --- feature-brief -----------------------------------------------------------
GROUNDING = """
grounding - the summary is written and nothing else is known yet. This status is for
reading the real repository and recording what is actually there. The work of it:

- Find the code this feature touches and read it: the function, the file, the
  callers. Understand why it exists, not just what it does. A component whose
  purpose you cannot state is one you are not ready to plan against.
- Record only what you confirmed by opening it. Every component, constraint,
  conflict and stale doc here is read out of this repository, never inferred from a
  name or remembered from somewhere else.
- Follow callers and imports outward until the blast radius stops growing, and note
  as you go which components hold pure logic and which perform effects.
- Turn whatever reading could not settle into a question rather than an assumption.
  An unsurfaced assumption is the most expensive thing to carry out of this status.

No code is edited here and no design is started. If the summary turns out to be
wrong or underspecified once you can see the code, say so now, while nothing has
been built on it.
"""

SPEC = """
spec - the grounded base is recorded, and the design is settled once, in the
feature-spec child, before a single step or case exists. The work of it:

- Update the summary and update the page title to the correct understanding of the
  work and escalate any design questions early to the user now.
- Author the spec from the grounded base: the behaviour, the interfaces with their
  exact signatures, the data shapes, the states, the error paths. Decide everything
  a plan would otherwise have to decide for itself.
- Draw the line between pure logic and effects while designing rather than after.
  Decide what is a function of its inputs alone and what needs I/O, storage, the
  clock or randomness, and give each side its own interfaces. The rules live on the
  pure side; the effectful side stays thin enough to hold none of its own.
- Spec the smallest thing that delivers the summary. No configurability, extension
  points or generality nobody asked for, and no abstraction over a single use.
- Answer the brief's questions and record each decision with the alternatives it
  rejected and why, so a settled question is not reopened mid-build.
- Escalate what only a person can settle instead of settling it on their behalf.

Seal the spec once it is settled; sealing is what unlocks planning. Steps and cases
are not written here, because a design still moving is not one to plan against.
"""

PLANNING = """
planning - the spec is sealed and is now turned into an implementation plan and a
testing plan detailed enough to build from without deciding anything further. The
work of it:

- Write each step as one action a skilled stranger to this codebase could finish in
  a few minutes, naming the exact files and carrying the real content it needs.
  Steps are read out of order and alone, so repeat detail rather than refer back.
- Give every step its verification: what will be run, and what it should print or
  return. A step with no way to tell whether it worked is not yet a step.
- Order the work so the pure logic is built and tested before the effectful code
  that calls it, and keep a step on one side of that line: a step that adds a rule
  changes pure logic, a step that wires it to storage or the network changes the
  shell around it.
- Plan the failure paths the spec implies alongside the happy one - empty inputs,
  missing values, malformed data, boundaries - and say what the volume this will
  really see does to the approach.
- Ask rather than guess. A question is cheap here and expensive once a plan exists.
  Record and escalate questions to the user now.

Keep both plans to what the spec asks for and nothing besides. Mark each ready when
it is complete; submitting the plan needs both plans ready and the spec still
sealed.
"""

PLAN_REVIEW = """
plan-review - the plan is written, and this is the last point at which fixing it is
still cheap. This status is for reading the plan against the spec, not for building.
The work of it:

- Check every spec requirement against a step that delivers it, and every step
  against a spec requirement that asked for it. A requirement no step covers and a
  step nothing asked for are both findings.
- Check that the testing plan can actually fail: cases that assert real behaviour,
  that cover the spec's error paths, and that reach the pure logic directly instead
  of through a mock.
- Check that the plan is not larger than the problem, and that the pure and
  effectful sides stayed separate in it.
- Check that the steps do not update documentation as that is a later activity once
  the implementation has landed.
- Record each finding AND make the edit its action names, so the plan and this
  record agree. A finding recorded but never applied is worse than one never
  raised.

Set the verdict honestly: needs-changes when an implementer would build the wrong
thing or get stuck, needs-human-decision when a question has to go to a person.
Wording and style preferences are not grounds to withhold build-ready.
"""

BUILDING = """
building - the plan is approved, and this is where code is written. Work the steps
in their order and let the plan, not improvisation, decide what gets built. The work
of it:

- Work test-first: write the failing test, watch it fail, write the least code that
  passes it, watch it pass, commit. Mark a step done or a case passed only from a
  run you actually saw.
- Keep the pure logic pure. Decisions, derivations and transformations are functions
  of their arguments, with no I/O, no clock, no randomness and no reaching into
  shared mutable state, and the code that performs effects stays a thin shell that
  calls them and applies what they return. Where a step tangles the two, split them
  rather than reach for a mock.
- Name things for what they mean. A longer name that carries intent beats a short
  one that loses it, and an argument keeps its caller's name unless renaming
  genuinely clarifies. Comments say why, not what.
- Stay surgical. Touch only what the step needs; leave adjacent code, comments and
  formatting exactly as found, and match the style already there even where you
  would have chosen otherwise. Remove only the imports and helpers your own change
  orphaned, and raise anything else you notice as a conflict or a question instead
  of fixing it in passing.
- Handle the realistic failure cases the plan named, and flag a limitation you are
  knowingly leaving in rather than let it be discovered later.

Anything the plan did not anticipate is a question, or a reopened plan, not a quiet
improvisation. Record each commit as you make it.
"""

FEATURE_BRIEF_REVIEW = """
review - the build is done, and this is the last stop before the human ship gate.
This status is for verifying, not for finishing off. The work of it:

- Re-read the spec's design section and confirm every requirement it states is
  actually implemented, not merely planned.
- Confirm every implementation-plan step is done and every testing-plan case passed
  against a test that genuinely ran. A case marked passed without a run you saw is
  the one failure this gate exists to catch.
- Confirm nothing that worked before is broken now, and that the diff carries only
  what the plan called for: an unrelated change here is a change nobody reviewed.
- Confirm the pure logic stayed free of effects and the shell around it stayed free
  of rules.
- Review the comments added for the change. Avoid verbosity of comments and avoid
  naming the specifics of other parts of code and instead keep comments to general
  principles and intents. Comments should only refer to the current code, not the
  previous implementation. Uppercase words and emphasis markers are inappropriate in
  tone and single line comments that are self-evident by code should be removed.

Three things are deliberately not part of this status, so do not start them here:
rebasing onto main happens at ship, not before; recording commits happens after ship,
once the shas are final; and reconciling the documentation pages the brief named as
going stale also happens at ship.

If any of this turns up outstanding work, use requestChanges to go back to building
rather than ship with a known gap.
"""


# --- simple-change and bug-report, shared ------------------------------------
REVIEW = """
review - the work is written, and this is the last stop before it is marked done and
left for the human close gate. This status is for verifying, not for finishing off. The
work of it:

- Re-read what the page said the work was and confirm every part of it is actually
  implemented rather than merely intended: each acceptance criterion on a change, the
  reported behaviour on a bug.
- Confirm each check against a run you actually saw. A test you believe passes is the
  one failure this gate exists to catch, so run it.
- Confirm nothing that worked before is broken now, and that the diff carries only what
  the work called for: an unrelated change here is a change nobody reviewed.
- Confirm the pure logic stayed free of effects and the shell around it stayed free of
  rules.
- Review the comments added for the change. Avoid verbosity of comments and avoid
  naming the specifics of other parts of code and instead keep comments to general
  principles and intents. Comments should only refer to the current code, not the
  previous implementation. Uppercase words and emphasis markers are inappropriate in
  tone and single line comments that are self-evident by code should be removed.

Two things are deliberately not part of this status: rebasing onto main happens at
close, not before, and the commit is recorded by close itself, so there is nothing to
record here. If any of this turns up outstanding work, use requestChanges to go back to
open rather than mark it done with a known gap.
"""


# --- simple-change -----------------------------------------------------------
SIMPLE_CHANGE_DRAFT = """
draft - the change has been asked for and nothing is written down yet. This status is
for saying what changes and why, precisely enough that someone else could start from
the page alone. The work of it:

- Name the one component the change touches. If it needs a second, this is not a
  simple change and belongs in a feature-brief before anything more is written here.
- State the behaviour today and the behaviour you want in its place. A summary naming
  only the new behaviour leaves the reader guessing what it replaces.
- Say who asked and what they actually asked for, so the reason survives the moment
  that prompted it.
- Write acceptance criteria that can come out false: the new behaviour, and the
  nearby behaviour that must keep working. A criterion settled by opinion is not one.
- Say here if the ask looks wrong, or bigger than it sounds, while nothing has been
  built on it.

Nothing is edited here. Open the change once the page would let a stranger to this
codebase start work from it.
"""

SIMPLE_CHANGE_OPEN = """
open - the change is described, and this one status carries it from reading the code to
a change that works. There is no separate grounding, spec or planning stage here, so
the discipline those would enforce has to be kept in this one. The work of it:

- Read the code the change touches before editing it: the function, its callers, why
  it exists. Follow callers and imports outward until the blast radius stops growing.
  A change made against a guess is what this status exists to prevent.
- Keep it the size it was scoped at. If the reading shows the work spans components,
  needs a design settled first, or wants a plan, raise that rather than let a simple
  change grow into an unplanned feature.
- Work test-first: write the failing test, watch it fail, write the least code that
  passes it, watch it pass. Each acceptance criterion should end up with something
  that would fail if the behaviour regressed.
- Keep the pure logic pure. Decisions, derivations and transformations are functions
  of their arguments, with no I/O, no clock, no randomness and no reaching into
  shared mutable state, and the code that performs effects stays a thin shell that
  calls them and applies what they return.
- Stay surgical. Touch only what the change needs; leave adjacent code, comments and
  formatting exactly as found, and match the style already there even where you would
  have chosen otherwise. Anything else you notice is raised, not fixed in passing.

Ask rather than guess, and flag a limitation you are knowingly leaving in rather than
let it be discovered later. Submit for review once the change is written, not once it
is intended.
"""


# --- bug-report --------------------------------------------------------------
BUG_REPORT_DRAFT = """
draft - a defect has been noticed and the report does not exist yet. This status is for
making it reproducible by someone who was not there. The work of it:

- Name the symptom you can observe, specific enough to tell this defect from a similar
  one. The cause you suspect and the fix you have in mind are not the summary.
- Record where it was seen: the component, the platform with versions, and the version
  or commit sha, so the report stays pinnable to a point in history.
- Write the repro as ordered actions from a stated starting state, with the exact
  commands, inputs and data, ending on the step that exposes the defect. A repro
  nobody else can follow is the most common reason a report goes nowhere.
- Ground the expected behaviour in something outside your own judgement: a spec line,
  a doc, a passing test, an established convention. Without that the report is an
  opinion.
- Quote the observed behaviour rather than paraphrase it - the literal message, stack
  trace, exit code or wrong output - and say whether it reproduces every time.

No fix is attempted here and no cause is asserted. Open the report once someone else
could reproduce the defect from the page alone.
"""

BUG_REPORT_OPEN = """
open - the report is written, and this one status carries the defect from reproducing it
to a fix that holds. There is no separate grounding, spec or planning stage here, so
the discipline those would enforce has to be kept in this one. The work of it:

- Reproduce it yourself first, by the recorded steps. A fix written against a failure
  you never saw is a guess, and a repro that does not reproduce is itself the finding -
  record that rather than quietly fixing something else.
- Find the cause by reading the code and following it outward, not by matching the
  shape of the symptom. Fix the cause: a symptom suppressed where it surfaced leaves
  the defect in place under a new name.
- Work test-first: write the test that fails for the reason the report names, watch it
  fail, write the least code that makes it pass, watch it pass. That test is what stops
  the defect coming back.
- Keep the pure logic pure and the effectful shell thin, and stay surgical - touch only
  what the fix needs, leave adjacent code, comments and formatting exactly as found,
  and raise anything else you notice rather than fixing it in passing.
- Say what the fix does not cover: a related path left broken, an intermittent case you
  could not reproduce, a workaround taken where a real fix was too large.

If the cause turns out to be a design problem rather than a defect, that is a feature
brief, not a larger bug fix. Submit for review once the fix is written and its test
genuinely ran.
"""


# --- architecture ------------------------------------------------------------
ARCHITECTURE_AUTHORING = """
authoring - documents a part of the system that already exists, written by reading
that code rather than recalling it. The work of it:

- Fix the boundary first: one part, one granularity, a job stated in one line. If
  that line needs an "and", it is two nodes.
- Describe what is there today. Aspirational architecture belongs in a feature
  brief, and a page mixing the two describes neither.
- Write what one file cannot show: why it exists, where its boundary runs, what
  crosses it, what must stay true. Point at a symbol rather than copy it.
- Say which side of the pure/effectful line the node sits on, and where that line
  runs inside it. A reader deciding where new behaviour belongs needs that first.
- Write invariants that can be checked and broken, with what breaks when violated.
  "Stays consistent" is not one.
- Record dependencies in the direction this node experiences them, naming what
  crosses the boundary.
- Confirm each code reference by opening it, then record the commit you read at.

Why it is shaped this way belongs in a decision record, linked from here. Keep the
page at the scale of its siblings, and when the code moves on mark it stale rather
than leave it describing an older system - a stale page is locked until markCurrent
brings it back here.
"""


# --- decision-record ---------------------------------------------------------
DECISION_RECORD_AUTHORING = """
authoring - captures why one decision was taken, while the reasons are still in
someone's head. The shape it produces belongs on an architecture page; the reasoning
that shape cannot show belongs here. The work of it:

- One decision per record, titled with the position taken rather than the topic:
  "store sessions in the database", not "session storage".
- Write the context first, for someone who was not there: the forces, the
  constraints, what was traded against what. It is worth the most in a year and
  skipped the most often.
- State the decision in the active voice, before any detail. A reader should not
  have to infer it from a discussion of the options.
- Record the options weighed and what ruled each out. Without a rejected
  alternative, nothing stops the question being reopened.
- Give consequences both ways. Only benefits reads as advocacy to whoever inherits
  the cost.
- Say where the decision moves the line between pure logic and effectful code - an
  architecture page can show that boundary but not explain it.
- Fill in date, scope and deciders, so a later reader can weigh how much still
  applies.
"""

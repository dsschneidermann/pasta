"""The vocabulary the page types are written in.

The kind constants, and the frozen spec dataclasses that depend on neither a field
nor a page type.
"""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent
from typing import Any

from ...errors import ValidationError


# --- Command kinds -----------------------------------------------------------
SET_SCALAR = "set_scalar"
SET_PROSE = "set_prose"
ADD_ELEMENT = "add_element"                  # append to a `list` field, or positioned insert (index + precedingId)
SET_ELEMENT_FIELD = "set_element_field"
REMOVE_ELEMENT = "remove_element"
ELEMENT_TRANSITION = "element_transition"   # fire a list element's own FSM (todo->done, ...)
ADD_BLOCK = "add_block"                      # append to a `blocks` field, or positioned insert (index + precedingId)
REMOVE_BLOCK = "remove_block"
REORDER_ELEMENT = "reorder_element"          # move one element to an anchored position in a `list` field
REORDER_BLOCK = "reorder_block"              # move one block to an anchored position in a `blocks` field
TRANSITION = "transition"
COMPOUND = "compound"
ADD_LINK = "add_link"                        # append a typed edge to Page.links (the universal authoring link)
SET_TITLE = "set_title"                      # set Page.title (the universal rename alias for renamePage)

# --- Field kinds -------------------------------------------------------------
SCALAR = "scalar"
PROSE = "prose"
LIST = "list"
BLOCKS = "blocks"                            # ordered typed blocks (paragraph/heading/code/list/table/quote/...)

# --- Element headings --------------------------------------------------------
# The element field names that title their element, in precedence order. A heading is DECLARED,
# never inferred from what an author typed: a list field naming one of these renders every one of
# its elements with a heading, and a field naming neither renders none of them with one, so an
# element's shape is a property of its type rather than of its values.
TITLE_ELEMENT_FIELDS = ("title", "name")

# --- Inline-run content shapes (for `blocks` fields) -------------------------
# The rich text inside a block is an ordered array of **inline runs**. An `ArgSpec` may
# declare which inline shape its (array) value must satisfy so the command layer can
# structurally validate it before anything is written. A "run" is one of:
#   - a plain string                                    - literal text;
#   - {"text": str, "bold"?: bool, "italic"?: bool, "href"?: str} - marked/linked text;
#   - {"code": str}                                     - an inline code span;
#   - {"ref": "<pageId>"}                               - a page reference (label render-derived).
# Markdown syntax inside a text run is rejected - emphasis is expressed with a structured run.
INLINE_RUNS = "inline_runs"            # value: [run, ...]                       (a paragraph/heading body)
INLINE_RUN_LISTS = "inline_run_lists"  # value: [[run, ...], ...]                (list items / quote paragraphs / table header cells)
INLINE_RUN_GRID = "inline_run_grid"    # value: [[[run, ...], ...], ...]         (table rows of cells)
TABLE_ALIGN = "table_align"            # value: ["left"|"center"|"right"|None, ...]
# A LIST add's create-with-content argument: the blocks an element is created holding, so making
# an element and filling it is one command and a batch never names an id it has not committed.
BLOCK_ARRAY = "block_array"            # value: [{"kind": <kind>, ...that kind's body args}, ...]

_ALIGN_VALUES = ("left", "center", "right", None)
# Markdown emphasis/code/link tokens rejected inside a plain-text run. Kept deliberately narrow
# (bold/code/link markers) so ordinary prose containing a lone `*` or `_` or even a file with
# double '__' is not falsely rejected.
_MARKDOWN_TOKENS = ("**", "`", "](")


# --- Spec dataclasses --------------------------------------------------------
@dataclass(frozen=True)
class FSMSpec:
    """A page's status FSM: its state set and initial state ONLY.

    The transition table is NOT stored here - it is DERIVED from the page type's transition/compound
    commands by `status_transitions(page_type)`, where each such command declares its source state(s)
    via `legal_in=` and its destination via `dest=`. So a status edge lives in exactly one place (the
    command), and `legal_in` is the uniform "where is this command legal" declaration across every
    command kind. (Element lifecycles are a separate concept - see `ElementFSMSpec`.)

    `terminal_states` names states in which the work is finished. While a page sits in one, `legal_commands`
    locks every authoring command (describeMutations reports them unavailable; mutatePageBatch rejects
    them) - but any remaining status transitions stay legal, so a terminal state can still offer, e.g., a
    `reopen` edge. This is an explicit declaration, NOT inferred from a state merely lacking outgoing
    transitions: only states listed here are authoring-locked. An authoring command can opt out by naming
    the terminal state in `legal_in`.

    `state_guidance` is the stage instruction for a status - what the state you just entered is
    for - echoed by the write path and used to open that state's generated doc page. It is a
    tuple of `(state, text)` pairs, not a mapping, because this spec is the `@lru_cache` key in
    `fsm._machine_class`. Leaving a state undeclared is the normal case.
    """
    name: str
    initial: str
    states: tuple[str, ...]
    transitions: tuple[tuple[str, str, str, str], ...] = ()
    terminal_states: tuple[str, ...] = ()
    state_guidance: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        # Setup only: normalize each guidance text as authored (dedent a
        # newline-stripped block, then rstrip). The state names are checked by
        # validate_fsm_spec, not here.
        normalized = tuple((state, dedent(text.strip("\n")).rstrip())
                           for state, text in self.state_guidance)
        object.__setattr__(self, "state_guidance", normalized)

    def guidance_for(self, state: str) -> str | None:
        """The stage instruction for `state`, or None when the type declares none for it."""
        for name, text in self.state_guidance:
            if name == state:
                return text
        return None


@dataclass(frozen=True)
class ElementFSMSpec:
    """A list element's own tiny lifecycle (a step's todo/done, a case's pending/passed/failed, ...).

    Unlike a page's status FSM, an element FSM keeps its own transition table: an `element_transition`
    command only names the `event` it fires (its `legal_in` is the PAGE status lock, not the element
    source state), so there is nothing to derive from and no duplication to remove - this table is the
    single source of truth.

    For an element rendered as a task checkbox, `checkmark_done` names the state shown as a checked box
    `[x]`; the FSM's `initial` state is then the unchecked box `[ ]`, and every other state - and every
    element FSM that leaves this None - renders with no box.
    """
    name: str
    initial: str
    states: tuple[str, ...]
    # (event, source, dest, agency)
    transitions: tuple[tuple[str, str, str, str], ...]
    checkmark_done: str | None = None


@dataclass(frozen=True)
class RefCheck:
    """A cross-page integrity precondition: `arg` must name an existing element id.

    Evaluated in the store (which can see other pages). `scope="parent"` means the id must
    be an element in `section.field` of this page's parent. A dangling id aborts the commit.
    """
    arg: str
    scope: str                                  # "parent"
    section: str
    field: str


@dataclass(frozen=True)
class ChildStateGuard:
    """A cross-page transition guard over the state of a page's children, evaluated in the store.

    For every child page of type `child_type`, in one of two forms:
    - element form (`section`/`field` given): every element in that list field must have a status
      in `allowed`;
    - page form (`section`/`field` omitted): the child page's own status must be in `allowed`.
    `allowed` is the set of statuses that satisfy the guard - usually one, but more than one lets a
    guard treat several as equivalent (e.g. a step counts as addressed when done or skipped).
    Otherwise the transition is rejected with `message`.
    """
    child_type: str
    allowed: tuple[str, ...]
    message: str
    section: str | None = None                  # element form; omit for the page-status form
    field: str | None = None


@dataclass(frozen=True)
class ParentStateGuard:
    """A cross-page transition guard over the state of a page's PARENT, evaluated in the store.

    The parent page's own status must be one of `required_statuses`, otherwise the transition is
    rejected with `message`. The mirror image of `ChildStateGuard` (which looks down at children):
    this looks up at the parent - e.g. to gate a pinned child's finalize transition on its parent
    having reached a given stage. Only enforced when the page actually has a parent of `parent_type`;
    a page with no parent, or a parent of another type, is unconstrained.
    """
    parent_type: str
    required_statuses: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class AutoChildSpec:
    """A page auto-created as a pinned, protected child when a page of the declaring type is made.

    `type` is the child page-type tag. Being an auto-child is what makes a page 'pinned' - it cannot
    be reparented, reordered, or archived/unarchived on its own (the store enforces this). The fact
    lives here, on the parent type, and is never stored as a field on the child Page.
    """
    type: str

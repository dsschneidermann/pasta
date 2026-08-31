"""The argument helpers a command declaration is written with, and the block-kind
vocabulary built out of them.

The two belong together: the block-kind helpers build a BlockKindSpec out of the same arg
helpers a command's arg list uses, and standard_blocks() collects the standard kinds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...errors import ValidationError
from .specs import (
    ADD_LINK,
    INLINE_RUNS,
    INLINE_RUN_GRID,
    INLINE_RUN_LISTS,
    SET_TITLE,
    TABLE_ALIGN,
    ChildStateGuard,
    ParentStateGuard,
    RefCheck,
)


@dataclass(frozen=True)
class ArgSpec:
    name: str
    type: str = "string"                        # JSON Schema type
    required: bool = True
    choices: tuple[str, ...] | None = None
    description: str = ""
    # for an `array` arg carrying inline runs: which inline-run shape it must satisfy
    # (INLINE_RUNS / INLINE_RUN_LISTS / INLINE_RUN_GRID / TABLE_ALIGN / BLOCK_ARRAY).
    # None = no shape check.
    content: str | None = None
    # for a BLOCK_ARRAY arg: the vocabulary it accepts, copied from the field's declaration
    # - what makes the value checkable, and what describe reads to build the arg's schema
    block_kinds: tuple[BlockKindSpec, ...] | None = None


@dataclass(frozen=True)
class CommandSpec:
    name: str
    kind: str
    description: str = ""
    section: str | None = None
    field: str | None = None
    args: tuple[ArgSpec, ...] = ()
    event: str | None = None                    # FSM event for TRANSITION / COMPOUND
    # for TRANSITION / COMPOUND: the destination state. Paired with `legal_in` (the source state(s)),
    # this is the single home for a status edge - `status_transitions(page_type)` derives the whole
    # page FSM table from these.
    dest: str | None = None
    # for ADD_ELEMENT / SET_ELEMENT_FIELD / ELEMENT_TRANSITION: (elementField, argName) pairs
    # mapping args onto the element. The id-taking kinds treat args[0] as the target element id.
    element_map: tuple[tuple[str, str], ...] = ()
    # for SET_ELEMENT_FIELD / ELEMENT_TRANSITION: literal (elementField, value) pairs to stamp
    # onto the target element (the flag-setting shape).
    element_const: tuple[tuple[str, Any], ...] = ()
    # for ADD_BLOCK / REMOVE_BLOCK / REORDER_BLOCK: the LIST element field holding the blocks.
    # None = the section's own blocks field. When set, args[0] is the element id and - for
    # remove/reorder - args[1] is the block id.
    element_field: str | None = None
    # for COMPOUND: ordered sub-commands applied atomically. (ELEMENT_TRANSITION fires the
    # element-FSM event named in `event` on the target element.)
    steps: tuple["CommandSpec", ...] = ()
    # for TRANSITION / COMPOUND: (section, field) pairs that must be populated before the
    # transition is legal - a required-content precondition on top of the FSM topology.
    requires: tuple[tuple[str, str], ...] = ()
    # Where this command is legal (None = any status). The uniform "where-legal" declaration:
    #   - content command: the statuses it may run in (a status-scoped lock);
    #   - TRANSITION / COMPOUND: the SOURCE state(s) of the edge (paired with `dest`), from which the
    #     page FSM table is derived. Not surfaced in the command summary for transitions (the source
    #     is already reported via the derived FSM transition list), so describe output is unchanged.
    legal_in: tuple[str, ...] | None = None
    # cross-page integrity check / transition guard (evaluated in the store)
    ref_check: RefCheck | None = None
    guards: tuple[ChildStateGuard, ...] = ()
    # cross-page guard over the PARENT's state (evaluated in the store) - see ParentStateGuard
    parent_guards: tuple[ParentStateGuard, ...] = ()
    agency: str = "agent"                       # "agent" | "human" | "either" (informational this pass)
    generated: bool = False


@dataclass(frozen=True)
class BlockKindSpec:
    """One block kind a blocks field accepts.

    `args` is the kind's body - the arguments a block of this kind carries, built by a block-kind
    helper (or spelled out for a custom kind). The same kind name can carry a different body in a
    different field, which is what a per-field override is. `ref_check` is the kind's cross-page
    integrity rule, enforced in the store per block - it lives here because the referencing
    argument lives inside a block, not flat on a command.
    """
    kind: str
    args: tuple[ArgSpec, ...]
    ref_check: RefCheck | None = None

    def body_args(self) -> tuple[ArgSpec, ...]:
        return self.args


@dataclass(frozen=True)
class ElementBlocksSpec:
    """A LIST element field that holds an ordered array of blocks instead of a scalar value.

    `block_kinds` is the closed vocabulary the field accepts - the same BlockKindSpec tuple a
    page-level blocks field declares, which is what makes the two levels one mechanism.
    """
    field: str
    block_kinds: tuple[BlockKindSpec, ...]


# --- Arg helpers -------------------------------------------------------------
# Tiny ArgSpec factories so a command's arg list reads as `(_text("file"), _integer("level"), ...)`
# instead of spelling out `ArgSpec(..., type=...)` each time. `_text()` is the common single-value
# `text` arg. `_same_named` derives an element_map from an arg list (each arg -> a same-named field),
# which is why no helper call site passes element_map: the arg names ARE the field names.
def _text(name: str = "text", *, required: bool = True,
          choices: tuple[str, ...] | None = None, description: str = "") -> ArgSpec:
    return ArgSpec(name, required=required, choices=choices, description=description)


def add_link_cmd() -> CommandSpec:
    """The universal reference-link authoring command: add an outgoing typed edge (this --role--> toId)
    to Page.links. Added to every authorable page type's command surface, so linking is discoverable as
    a page command and not only through the separate top-level `link` tool. Always legal - no legal_in /
    requires - so it runs in any status. The store's _check_link precheck enforces the cross-page rules
    shared with link_page, before the pure core appends."""
    return CommandSpec(
        name="addLink",
        kind=ADD_LINK,
        description="add a typed reference link from this page to another (this --role--> toId)",
        args=(_text("toId", description="the target page id to link to"),
              _text("role", description="the edge role, e.g. depends-on / relates-to")),
    )


def set_title_cmd() -> CommandSpec:
    """The universal page-rename authoring command: set this page's title - an alias for the top-level
    renamePage tool, exposed as a page command (like add_link_cmd's addLink) so a title can be fixed in
    the same authoring surface (describeMutations / mutatePageBatch) as the content it describes. Added to
    every authorable page type EXCEPT the command-less toc. Always legal - no legal_in / requires - so it
    runs in any status (locked only in a terminal state, as all authoring is). The pure core sets
    Page.title after rejecting a blank title, exactly as renamePage does."""
    return CommandSpec(
        name="setTitle",
        kind=SET_TITLE,
        description="set this page's title (an alias for the renamePage operation)",
        args=(_text("title", description="the new page title (must be non-empty)"),),
    )


def _integer(name: str, *, required: bool = True, description: str = "") -> ArgSpec:
    return ArgSpec(name, type="integer", required=required, description=description)


def _boolean(name: str, *, required: bool = True, description: str = "") -> ArgSpec:
    return ArgSpec(name, type="boolean", required=required, description=description)


def _array(name: str, *, content: str | None = None, required: bool = True, description: str = "",
           block_kinds: tuple[BlockKindSpec, ...] | None = None) -> ArgSpec:
    return ArgSpec(name, type="array", required=required, content=content, description=description,
                   block_kinds=block_kinds)


def _object(name: str, *, content: str | None = None, required: bool = True, description: str = "",
            block_kinds: tuple[BlockKindSpec, ...] | None = None) -> ArgSpec:
    return ArgSpec(name, type="object", required=required, content=content, description=description,
                   block_kinds=block_kinds)


def _same_named(args: tuple[ArgSpec, ...]) -> tuple[tuple[str, str], ...]:
    """The element_map for `args`: each arg mapped onto a same-named element/block field."""
    return tuple((arg.name, arg.name) for arg in args)


# Positioning args shared by add-block / add-element commands (both optional: omit `index` to append).
# `index` is the destination slot; `precedingId` is the stale-read guard - the id the caller expects
# immediately before that slot (null/omit for the front). The reorder_element / reorder_block kinds
# use a required `toIndex` plus this same `precedingId`. The guard itself lives in commands._resolve_slot.
_INDEX = _integer("index", required=False,
                  description="insert position (append if omitted); when given, requires precedingId")
_PRECEDING = _text("precedingId", required=False,
                   description="stale-read guard: the id expected just before the slot (null/omit for the front)")


# --- Block-kind helpers ------------------------------------------------------
# Factories that build a BlockKindSpec the way _text builds an ArgSpec, so a field's vocabulary
# reads as `(_paragraph_runs(), _code_block())` rather than spelling out each spec's body args.
# One per standard kind, two text-only variants whose body is a single plain `text` arg, and
# `standard_blocks()` for the whole vocabulary.
def _paragraph_runs() -> BlockKindSpec:
    return BlockKindSpec("paragraph", args=(_array("inlines", content=INLINE_RUNS),))


def _heading_runs() -> BlockKindSpec:
    return BlockKindSpec("heading", args=(_integer("level"), _array("inlines", content=INLINE_RUNS)))


def _code_block() -> BlockKindSpec:
    return BlockKindSpec("code", args=(_text("language"), _text("source")))


def _list_block() -> BlockKindSpec:
    return BlockKindSpec("list", args=(_boolean("ordered"), _array("items", content=INLINE_RUN_LISTS)))


def _quote_block() -> BlockKindSpec:
    return BlockKindSpec("quote", args=(_array("paragraphs", content=INLINE_RUN_LISTS),))


def _table_block() -> BlockKindSpec:
    return BlockKindSpec("table", args=(_array("header", content=INLINE_RUN_LISTS),
                                        _array("rows", content=INLINE_RUN_GRID),
                                        _array("align", required=False, content=TABLE_ALIGN)))


def _divider_block() -> BlockKindSpec:
    return BlockKindSpec("divider", args=())


def _paragraph_text() -> BlockKindSpec:
    """A paragraph whose body is one plain text arg rather than rich inline runs."""
    return BlockKindSpec("paragraph", args=(_text(),))


def _heading_text() -> BlockKindSpec:
    """A heading whose body is a level and one plain text arg rather than rich inline runs."""
    return BlockKindSpec("heading", args=(_integer("level"), _text()))


def standard_blocks() -> tuple[BlockKindSpec, ...]:
    """Every standard kind, in the canonical order - what a field passes to accept them all."""
    return (_paragraph_runs(), _heading_runs(), _code_block(), _list_block(), _quote_block(), _table_block(), _divider_block())

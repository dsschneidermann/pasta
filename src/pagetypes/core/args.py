"""The argument helpers a command declaration is written with, and the block-kind
vocabulary built out of them.

The two belong together: the block-kind helpers build a BlockKindSpec out of the same arg
helpers a command's arg list uses, and standard_blocks() collects the standard kinds.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...errors import ValidationError
from .specs import (
    INLINE_RUNS,
    INLINE_RUN_GRID,
    INLINE_RUN_LISTS,
    TABLE_ALIGN,
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
class BlockKindSpec:
    """One block kind a blocks field accepts.

    `body_args` is the kind's body - the arguments a block of this kind carries, built by a block-kind
    helper (or spelled out for a custom kind). The same kind name can carry a different body in a
    different field, which is what a per-field override is. `ref_check` is the kind's cross-page
    integrity rule, enforced in the store per block - it lives here because the referencing
    argument lives inside a block, not flat on a command.
    """
    kind: str
    body_args: tuple[ArgSpec, ...]
    ref_check: RefCheck | None = None


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
    return BlockKindSpec("paragraph", body_args=(_array("inlines", content=INLINE_RUNS),))


def _heading_runs() -> BlockKindSpec:
    return BlockKindSpec("heading", body_args=(_integer("level"), _array("inlines", content=INLINE_RUNS)))


def _code_block() -> BlockKindSpec:
    return BlockKindSpec("code", body_args=(_text("language"), _text("source")))


def _list_block() -> BlockKindSpec:
    return BlockKindSpec("list", body_args=(_boolean("ordered"), _array("items", content=INLINE_RUN_LISTS)))


def _quote_block() -> BlockKindSpec:
    return BlockKindSpec("quote", body_args=(_array("paragraphs", content=INLINE_RUN_LISTS),))


def _table_block() -> BlockKindSpec:
    return BlockKindSpec("table", body_args=(_array("header", content=INLINE_RUN_LISTS),
                                        _array("rows", content=INLINE_RUN_GRID),
                                        _array("align", required=False, content=TABLE_ALIGN)))


def _divider_block() -> BlockKindSpec:
    return BlockKindSpec("divider", body_args=())


def _paragraph_text() -> BlockKindSpec:
    """A paragraph whose body is one plain text arg rather than rich inline runs."""
    return BlockKindSpec("paragraph", body_args=(_text(),))


def _heading_text() -> BlockKindSpec:
    """A heading whose body is a level and one plain text arg rather than rich inline runs."""
    return BlockKindSpec("heading", body_args=(_integer("level"), _text()))


def standard_blocks() -> tuple[BlockKindSpec, ...]:
    """Every standard kind, in the canonical order - what a field passes to accept them all."""
    return (_paragraph_runs(), _heading_runs(), _code_block(), _list_block(), _quote_block(), _table_block(), _divider_block())

"""A field's declaration: what kind it is, what it accepts, and the helpers a page
type declares one with."""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from .args import (
    BlockKindSpec,
    ElementBlocksSpec,
)
from .specs import BLOCKS, LIST, PROSE, SCALAR, TITLE_ELEMENT_FIELDS, ElementFSMSpec


@dataclass(frozen=True)
class FieldSpec:
    key: str
    kind: str                                   # SCALAR | PROSE | LIST | BLOCKS
    choices: tuple[str, ...] | None = None      # allowed values for a scalar enum
    element_fields: tuple[str, ...] | None = None  # for LIST: each element's field names
    element_fsm: ElementFSMSpec | None = None   # for LIST: a per-element lifecycle (todo/done, ...)
    # for LIST: element fields that hold blocks rather than a scalar value
    element_blocks: tuple[ElementBlocksSpec, ...] = ()
    block_kinds: tuple[BlockKindSpec, ...] = ()
    description: str = ""

    def __post_init__(self):
        object.__setattr__(self, "description", dedent(self.description.strip("\n")).rstrip())


def get_element_blocks(self: FieldSpec, element_field: str) -> ElementBlocksSpec | None:
    """The block declaration for `element_field`, or None when it holds a scalar value."""
    for blocks in self.element_blocks:
        if blocks.field == element_field:
            return blocks
    return None


def block_element_fields(self: FieldSpec) -> tuple[str, ...]:
    """The element field names that hold blocks - what every consumer skips when it is
    treating an element's fields as scalar text."""
    return tuple(blocks.field for blocks in self.element_blocks)


def title_element_field(self: FieldSpec) -> str | None:
    """The element field whose value heads each of this list's elements, or None when the
    type declares no heading and every element renders as its ordinal plus labelled rows.
    Read from the declaration alone, so every element of one field renders the same shape.
    A block-bearing field is not a heading candidate - a heading is one line of text."""
    declared = self.element_fields or ()
    blocks = block_element_fields(self)
    for key in TITLE_ELEMENT_FIELDS:
        if key in declared and key not in blocks:
            return key
    return None


@dataclass(frozen=True)
class SectionSpec:
    key: str
    name: str
    fields: tuple[FieldSpec, ...]


# --- Declaration helpers (readability only) ----------------------------------
def _scalar(key: str, *, choices: tuple[str, ...] | None = None, description: str = "") -> FieldSpec:
    return FieldSpec(key=key, kind=SCALAR, choices=choices, description=description)


def _prose(key: str, *, description: str = "") -> FieldSpec:
    return FieldSpec(key=key, kind=PROSE, description=description)


def _list(key: str, element_fields: tuple[str, ...], element_fsm: ElementFSMSpec | None = None,
          *, element_blocks: tuple[ElementBlocksSpec, ...] = (), description: str = "") -> FieldSpec:
    return FieldSpec(key=key, kind=LIST, element_fields=element_fields,
                     element_fsm=element_fsm, element_blocks=element_blocks,
                     description=description)


def _blocks(key: str, block_kinds: tuple[BlockKindSpec, ...],
            description: str = "") -> FieldSpec:
    return FieldSpec(key=key, kind=BLOCKS, block_kinds=block_kinds, description=description)

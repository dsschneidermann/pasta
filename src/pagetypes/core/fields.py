"""A field's declaration: what kind it is, what it accepts, and the helpers a page
type declares one with."""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from .args import (
    BlockKindSpec,
    ElementBlocksSpec,
    _reject_duplicate_kinds,
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
        # An instruction is authored as an indented triple-quoted block wrapped at the source
        # margin; strip that shared indentation so consumers get the text as authored. The wrap
        # breaks are kept - markdown reflows a paragraph's newlines away.
        object.__setattr__(self, "description", dedent(self.description.strip("\n")).rstrip())
        # Checked where it is declared, so a typo fails at import rather than at authoring time.
        if self.block_kinds and self.kind != BLOCKS:
            raise ValueError(f"{self.key}: block_kinds is only valid on a blocks field.")
        if self.kind == BLOCKS and not self.block_kinds:
            raise ValueError(f"{self.key}: a blocks field declares no block kinds.")
        _reject_duplicate_kinds(self.key, self.block_kinds)
        # A block-bearing element field is checked where it is declared, so a typo fails at import
        # rather than producing a field nothing can ever author.
        seen: set[str] = set()
        for spec in self.element_blocks:
            if self.kind != LIST:
                raise ValueError(f"{self.key}: element_blocks is only valid on a list field.")
            if spec.field not in (self.element_fields or ()):
                raise ValueError(
                    f"{self.key}: element_blocks names '{spec.field}', which is not one of " +
                    f"element_fields."
                )
            if spec.field in seen:
                raise ValueError(f"{self.key}: element_blocks names '{spec.field}' twice.")
            seen.add(spec.field)

    def element_blocks_spec(self, element_field: str) -> ElementBlocksSpec | None:
        """The block declaration for `element_field`, or None when it holds a scalar value."""
        for spec in self.element_blocks:
            if spec.field == element_field:
                return spec
        return None

    def block_element_fields(self) -> tuple[str, ...]:
        """The element field names that hold blocks - what every consumer skips when it is
        treating an element's fields as scalar text."""
        return tuple(spec.field for spec in self.element_blocks)

    def title_element_field(self) -> str | None:
        """The element field whose value heads each of this list's elements, or None when the
        type declares no heading and every element renders as its ordinal plus labelled rows.
        Read from the declaration alone, so every element of one field renders the same shape.
        A block-bearing field is not a heading candidate - a heading is one line of text."""
        declared = self.element_fields or ()
        blocks = self.block_element_fields()
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

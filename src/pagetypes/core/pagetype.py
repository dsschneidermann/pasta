"""The page type itself: what a page of this type is made of, and how it is set up.

Its post-init hook does the two things a declaration needs before anything reads it -
resolves each block argument's vocabulary from the field it targets, and derives the
FSM transition table from the commands - then hands the finished declaration to the
validators.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ...errors import ValidationError
from .args import ArgSpec, CommandSpec
from .fields import FieldSpec, SectionSpec
from .specs import (
    ADD_ELEMENT,
    BLOCKS,
    BLOCK_ARRAY,
    COMPOUND,
    LIST,
    PROSE,
    TRANSITION,
    AutoChildSpec,
    FSMSpec,
)
from .validation import (
    validate_pagetype_field_setters,
    validate_pagetype_setter_descriptions,
)


@dataclass(frozen=True)
class PageType:
    tag: str
    name: str
    description: str
    sections: tuple[SectionSpec, ...]
    commands: tuple[CommandSpec, ...]
    fsm: FSMSpec
    # auto-created pinned children created in the same commit as this page (see AutoChildSpec)
    auto_children: tuple[AutoChildSpec, ...] = ()

    def __post_init__(self):
        self._resolve_block_vocabularies()
        object.__setattr__(self.fsm, "transitions", status_transitions(self))
        validate_pagetype_setter_descriptions(self)
        validate_pagetype_field_setters(self)

    def _resolve_block_vocabularies(self) -> None:
        """Fill each block-carrying argument's accepted kinds in from the field it targets.

        A command factory names the section and field it builds for; this is the first point
        that can turn that into a vocabulary, because it is the first point holding both.
        """
        object.__setattr__(self, "commands", tuple(
            replace(command,
                    args=tuple(self._resolved_arg(command, arg) for arg in command.args))
            for command in self.commands))

    def _resolved_arg(self, command: CommandSpec, arg: ArgSpec) -> ArgSpec:
        """`arg` with its vocabulary filled in, or unchanged when it carries no blocks.

        Raises rather than leaving one unresolved: every consumer reads block_kinds as
        "not a block argument" when it is None, so an unresolved argument would accept any
        block, describe itself as an untyped array, and lose its cross-page ref check, with
        nothing raising anywhere.
        """
        if arg.content != BLOCK_ARRAY:
            return arg
        if command.section is None or command.field is None:
            raise ValueError(
                f"{self.tag}: command '{command.name}' carries blocks but targets no field.")
        field_spec = self.field_spec(command.section, command.field)
        if field_spec is None:
            raise ValueError(
                f"{self.tag}: command '{command.name}' carries blocks for "
                f"{command.section}.{command.field}, which is not a declared field.")
        # A list add carries one block argument per block-bearing element field, named after
        # it; an element-scoped block command names that field on the command instead.
        element_field = command.element_field or (
            arg.name if command.kind == ADD_ELEMENT else None)
        if element_field is None:
            if field_spec.kind != BLOCKS:
                raise ValueError(
                    f"{self.tag}: command '{command.name}' carries blocks for "
                    f"{command.section}.{command.field}, which is not a blocks field.")
            return replace(arg, block_kinds=field_spec.block_kinds)
        spec = field_spec.element_blocks_spec(element_field)
        if spec is None:
            raise ValueError(
                f"{self.tag}: command '{command.name}' carries blocks for "
                f"{command.section}.{command.field}.{element_field}, which is not declared "
                f"as a block-bearing element field.")
        return replace(arg, block_kinds=spec.kinds)



    def command(self, name: str) -> CommandSpec | None:
        for command in self.commands:
            if command.name == name:
                return command
        return None

    def field_spec(self, section_key: str, field_key: str) -> FieldSpec | None:
        for section in self.sections:
            if section.key == section_key:
                for field_spec in section.fields:
                    if field_spec.key == field_key:
                        return field_spec
        return None


def status_transitions(page_type: PageType) -> tuple[tuple[str, str, str, str], ...]:
    """The page's status-FSM transition table, DERIVED from its transition/compound commands.

    Each top-level command with a page-status event (kind TRANSITION or COMPOUND, `event` set) owns one
    edge: `legal_in` is its source state(s) and `dest` its destination. A command legal in several
    states expands to one `(event, source, dest, agency)` per source.
    Nested COMPOUND sub-steps are NOT walked - the outer command carries the edge - so the inner
    transition step does not double-count. Iteration follows command-declaration order.
    """
    edges: list[tuple[str, str, str, str]] = []
    for command in page_type.commands:
        if command.kind in (TRANSITION, COMPOUND) and command.event is not None and command.dest is not None:
            for source in (command.legal_in or ()):
                edges.append((command.event, source, command.dest, command.agency))
    return tuple(edges)


def initial_sections(
    page_type: PageType, existing: dict[str, dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """The section/field state a page of this type starts with, or `existing` backfilled onto it.

    Idempotent: a section or field already present in `existing` is carried over untouched: only
    a section or field the page type declares but `existing` lacks gets its empty default. This
    lets the same function seed a freshly created page (no `existing`) and backfill an older page
    against a page type that has since gained a section or field.
    """
    sections: dict[str, dict[str, Any]] = {}
    for section in page_type.sections:
        field_values: dict[str, Any] = dict((existing or {}).get(section.key, {}))
        for field_spec in section.fields:
            if field_spec.key in field_values:
                continue
            if field_spec.kind == PROSE:
                field_values[field_spec.key] = ""
            elif field_spec.kind in (LIST, BLOCKS):
                field_values[field_spec.key] = []
            else:  # SCALAR
                field_values[field_spec.key] = None
        sections[section.key] = field_values
    return sections

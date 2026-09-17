"""The page type itself: what a page of this type is made of, and how it is set up.

Its post-init hook does the setup steps a declaration needs before anything reads it - resolves
each block argument's vocabulary from the field it targets, derives the FSM transition table from
the commands, and builds the machines those describe - the status machine, and every element
machine the type declares on its list fields. Whether the finished declaration is well-formed is
a separate concern, checked by the validators in `validation.py` - including what the build
settles, which is reported there rather than raised from here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ...errors import ValidationError
from ...fsm import try_build_machine
from .args import ArgSpec
from .commands import CommandSpec
from .fields import FieldSpec, SectionSpec, get_element_blocks
from .specs import (
    ADD_ELEMENT,
    BLOCKS,
    BLOCK_ARRAY,
    COMPOUND,
    LIST,
    PROSE,
    TRANSITION,
    AutoChildSpec,
    ElementFSMSpec,
    FSMSpec,
    WorkspaceGuidanceSpec,
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
    # mutable per-workspace guidance texts this type surfaces at some of its statuses
    workspace_guidance: tuple[WorkspaceGuidanceSpec, ...] = ()

    def __post_init__(self):
        self._resolve_block_vocabularies()
        object.__setattr__(self.fsm, "transitions", _status_transitions(self))
        self._build_machines()

    def _build_machines(self) -> None:
        """Build the status machine and every element machine this type declares.

        The build is each spec's own well-formedness check, so it runs with the declaration
        rather than on first use, and a failure is held rather than raised for the validator to
        report. An element spec may be declared on more than one page type and carries one
        machine either way, so a spec already built is left as it is.
        """
        _store_machine(self.fsm)
        for _section, _field, element_fsm in element_fsm_sites(self):
            if element_fsm.machine is None and element_fsm.machine_error is None:
                _store_machine(element_fsm)

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
        """`arg` with its block kinds filled in, or unchanged when it carries no blocks or the
        target cannot be resolved.

        Best-effort: this only ever fills block kinds in, never checks. A block argument whose
        target does not resolve is left with block_kinds None - the "not a block argument"
        sentinel - and validate_pagetype_block_args reports it. That is safe because the primary
        flows validate before serving, so an unresolved argument never reaches a consumer.
        """
        if arg.content != BLOCK_ARRAY:
            return arg
        if command.section is None or command.field is None:
            return arg
        field_spec = get_pagetype_field(self, command.section, command.field)
        if field_spec is None:
            return arg
        # A list add carries one block argument per block-bearing element field, named after
        # it; an element-scoped block command names that field on the command instead.
        element_field = command.element_field or (
            arg.name if command.kind == ADD_ELEMENT else None)
        if element_field is None:
            if field_spec.kind != BLOCKS:
                return arg
            return replace(arg, block_kinds=field_spec.block_kinds)
        element_blocks = get_element_blocks(field_spec, element_field)
        if element_blocks is None:
            return arg
        return replace(arg, block_kinds=element_blocks.block_kinds)


def _store_machine(spec: FSMSpec | ElementFSMSpec) -> None:
    """Build `spec`'s machine and keep whichever of the class or the error came back."""
    machine, error = try_build_machine(spec)
    object.__setattr__(spec, "machine", machine)
    object.__setattr__(spec, "machine_error", error)


def get_pagetype_command(self: PageType, name: str) -> CommandSpec | None:
    for command in self.commands:
        if command.name == name:
            return command
    return None


def get_pagetype_field(self: PageType, section_key: str, field_key: str) -> FieldSpec | None:
    for section in self.sections:
        if section.key == section_key:
            for field_spec in section.fields:
                if field_spec.key == field_key:
                    return field_spec
    return None


def element_fsm_sites(page_type: PageType) -> tuple[tuple[str, str, ElementFSMSpec], ...]:
    """Every element FSM this type declares, with the section and field it hangs off.

    A list field is the only place an element FSM can be attached, so this walk is the whole
    set: an element FSM no page type declares is unreachable, and nothing builds it.
    """
    return tuple(
        (section.key, field_spec.key, field_spec.element_fsm)
        for section in page_type.sections
        for field_spec in section.fields
        if field_spec.element_fsm is not None)


def _status_transitions(page_type: PageType) -> tuple[tuple[str, str, str, str], ...]:
    """The page's status-FSM transition table, DERIVED from its transition/compound commands.

    Each top-level command with a page-status event (kind TRANSITION or COMPOUND, `event` set) owns one
    edge: `legal_in` is its source status(es) and `dest` its destination. A command legal in several
    statuses expands to one `(event, source, dest, agency)` per source.
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

"""Pure introspection: shape a PageType / a page's legal mutations for the tools.

`describe_page_type` powers the `describePageType` tool (type schema without an
instance); `describe_mutations` powers `describeMutations` (per-command arg schema
plus current legality for a specific page).
"""

from __future__ import annotations

from typing import Any

from .commands import legal_commands
from .model import Page
from .pagetypes import (BLOCKS, BLOCK_ARRAY, COMPOUND, TRANSITION, BlockKindSpec, CommandSpec,
                        PageType)


def _block_schema(kinds: tuple[BlockKindSpec, ...]) -> dict[str, Any]:
    """The schema for one block: a oneOf branch per accepted kind, each built from that kind's
    declared body args - the same source validate_block reads, so the schema and the grammar
    agree. A caller learns a field's whole vocabulary from this alone, which is what replaces
    reading the kind off a per-kind command name."""
    branches: list[dict[str, Any]] = []
    for spec in kinds:
        properties: dict[str, Any] = {"kind": {"const": spec.kind}}
        required = ["kind"]
        for body in spec.body_args():
            properties[body.name] = {"type": body.type}
            if body.required:
                required.append(body.name)
        branches.append({"type": "object", "properties": properties,
                         "required": required, "additionalProperties": False})
    return {"oneOf": branches}


def command_arg_schema(command: CommandSpec) -> dict[str, Any]:
    """A JSON Schema object for a command's arguments."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for arg in command.args:
        prop: dict[str, Any] = {"type": arg.type}
        if arg.content == BLOCK_ARRAY and arg.block_kinds is not None:
            prop = {"type": "array", "items": _block_schema(arg.block_kinds)}
        if arg.choices is not None:
            prop["enum"] = list(arg.choices)
        if arg.description:
            prop["description"] = arg.description
        properties[arg.name] = prop
        if arg.required:
            required.append(arg.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _command_summary(command: CommandSpec) -> dict[str, Any]:
    return {
        "name": command.name,
        "kind": command.kind,
        # a short line on what the command does; a field setter's instruction is not here, it lives
        # on the (section, field) FieldSpec reported in this type's `sections` listing
        "description": command.description,
        "section": command.section,
        "field": command.field,
        "event": command.event,
        "agency": command.agency,
        # statuses a CONTENT command is allowed in. Suppressed for a transition/compound command:
        # there `legal_in` is the edge's SOURCE state, already reported in the FSM transition list,
        # so hiding it here keeps the describe output unclobbered.
        "legalIn": (list(command.legal_in) if command.legal_in
                    and command.kind not in (TRANSITION, COMPOUND) else None),
        # (section, field) content that must be present before this transition is legal.
        "requires": [{"section": section, "field": field} for section, field in command.requires],
        "args": command_arg_schema(command),
    }


def describe_fsm(page_type: PageType) -> dict[str, Any]:
    return {
        "initial": page_type.fsm.initial,
        "states": list(page_type.fsm.states),
        "transitions": [
            {"event": event, "source": source, "dest": dest, "agency": agency}
            for event, source, dest, agency in page_type.fsm.transitions
        ],
        # A dict is safe here - a projection, unlike the FSMSpec, which must stay hashable.
        "stateGuidance": dict(page_type.fsm.state_guidance),
    }


def describe_page_type(page_type: PageType) -> dict[str, Any]:
    return {
        "tag": page_type.tag,
        "name": page_type.name,
        "description": page_type.description,
        "fsm": describe_fsm(page_type),
        "sections": [
            {
                "key": section.key,
                "name": section.name,
                "fields": [
                    {
                        "key": field_spec.key,
                        "kind": field_spec.kind,
                        "choices": list(field_spec.choices) if field_spec.choices else None,
                        "elementFields": list(field_spec.element_fields) if field_spec.element_fields else None,
                        # for a list with a per-element lifecycle: its states (e.g. todo/done)
                        "elementStates": list(field_spec.element_fsm.states) if field_spec.element_fsm else None,
                        # for a list whose element fields hold blocks: each field and the kinds it accepts
                        "elementBlocks": ([{"field": spec.field,
                                            "kinds": [kind.kind for kind in spec.kinds]}
                                           for spec in field_spec.element_blocks] or None),
                        # for a blocks field: the kinds it accepts. The only place a caller can
                        # read a page-level field's vocabulary, now that no command name carries it.
                        "blockKinds": ([kind.kind for kind in field_spec.block_kinds]
                                       if field_spec.kind == BLOCKS else None),
                        "description": field_spec.description,
                    }
                    for field_spec in section.fields
                ],
            }
            for section in page_type.sections
        ],
        "commands": [_command_summary(command) for command in page_type.commands],
    }


def describe_mutations(page: Page, page_type: PageType, ignore_requirements: bool = False) -> list[dict[str, Any]]:
    """Every command for `page` with its arg schema and whether it is legal right now.

    `ignore_requirements` is forwarded to `legal_commands`; the live MCP tool leaves it False,
    and doc generation sets it True to surface content-gated transitions (see legal_commands).
    """
    legal = legal_commands(page, page_type, ignore_requirements=ignore_requirements)
    return [
        {**_command_summary(command), "available": legal.get(command.name, False)}
        for command in page_type.commands
    ]

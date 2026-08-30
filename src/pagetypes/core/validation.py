"""The blocks grammar: what an inline run may be, and what a block may be."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ...errors import ValidationError

if TYPE_CHECKING:
    # Annotation only: a page type calls these, so importing it here at runtime
    # would point the dependency back the way it came.
    from .pagetype import PageType
from .args import BlockKindSpec, CommandSpec, ElementBlocksSpec
from .commands import is_field_setter
from .fields import FieldSpec
from .specs import ADD_ELEMENT, BLOCKS, LIST, SET_PROSE, SET_SCALAR, FSMSpec
from .specs import (
    BLOCK_ARRAY,
    INLINE_RUNS,
    INLINE_RUN_GRID,
    INLINE_RUN_LISTS,
    TABLE_ALIGN,
    _ALIGN_VALUES,
    _MARKDOWN_TOKENS,
)


# --- Inline-run validation (the `blocks` field grammar) ----------------------
# These are pure predicates the command layer runs before applying an add/set-block command.
# They enforce the run grammar above; a dangling `ref`'s *existence* is a cross-page concern
# checked in the store, not here (the pure core cannot see other pages).
def _reject_markdown(text: str) -> None:
    for token in _MARKDOWN_TOKENS:
        if token in text:
            raise ValidationError(
                f"Markdown syntax ('{token}') is not allowed in a text run - express emphasis " +
                f"or a link with a structured run (e.g. {{'text': '…', 'bold': true}}) instead."
            )


def _validate_run(run: Any) -> None:
    """Validate one inline run against the run grammar."""
    if isinstance(run, str):
        _reject_markdown(run)
        return
    if not isinstance(run, dict):
        raise ValidationError(f"An inline run must be a string or an object, got {type(run).__name__}.")
    keys = set(run)
    if "code" in run:
        if keys != {"code"} or not isinstance(run["code"], str):
            raise ValidationError("An inline code run must be exactly {'code': <string>}.")
        return
    if "ref" in run:
        if keys != {"ref"} or not isinstance(run["ref"], str):
            raise ValidationError("An inline ref run must be exactly {'ref': <pageId>}.")
        return
    if "text" in run:
        extra = keys - {"text", "bold", "italic", "href"}
        if extra:
            raise ValidationError(f"A text run has unknown keys: {sorted(extra)}.")
        if not isinstance(run["text"], str):
            raise ValidationError("A text run's 'text' must be a string.")
        for flag in ("bold", "italic"):
            if flag in run and not isinstance(run[flag], bool):
                raise ValidationError(f"A text run's '{flag}' must be a boolean.")
        if "href" in run and not isinstance(run["href"], str):
            raise ValidationError("A text run's 'href' must be a string.")
        _reject_markdown(run["text"])
        return
    raise ValidationError("An inline run object must be a text run, {'code': …}, or {'ref': …}.")


def _validate_runs(runs: Any) -> None:
    if not isinstance(runs, list):
        raise ValidationError("An inline-run value must be an array of runs.")
    for run in runs:
        _validate_run(run)


def validate_inline_content(content: str, value: Any) -> None:
    """Validate `value` against the declared inline-content `content` shape (raises ValidationError)."""
    if content == INLINE_RUNS:
        _validate_runs(value)
    elif content == INLINE_RUN_LISTS:
        if not isinstance(value, list):
            raise ValidationError("Expected an array of inline-run arrays.")
        for entry in value:
            _validate_runs(entry)
    elif content == INLINE_RUN_GRID:
        if not isinstance(value, list):
            raise ValidationError("Expected an array of table rows.")
        for row in value:
            if not isinstance(row, list):
                raise ValidationError("Each table row must be an array of cells.")
            for cell in row:
                _validate_runs(cell)
    elif content == TABLE_ALIGN:
        if not isinstance(value, list):
            raise ValidationError("Table 'align' must be an array.")
        for entry in value:
            if entry not in _ALIGN_VALUES:
                raise ValidationError(
                    f"Table alignment must be one of left/center/right/null, got {entry!r}."
                )


def validate_block(entry: Any, block_kinds: tuple[BlockKindSpec, ...]) -> None:
    """Validate one block against the block kinds its field declares.

    A block is an object carrying a `kind` the field declares plus exactly that kind's body args,
    each checked the way the per-kind command used to check it - so a block that is legal to
    create is legal to set, and the reverse. Both paths share this grammar rather than restating
    it. The body args come from the matched BlockKindSpec, which the kind carries directly, so a
    per-field override is honoured.
    """
    if not isinstance(entry, dict):
        raise ValidationError(
            f"A block must be an object with a 'kind', got {type(entry).__name__}."
        )
    kind = entry.get("kind")
    block = next((block for block in block_kinds if block.kind == kind), None)
    if block is None:
        raise ValidationError(
            f"Block kind {kind!r} is not accepted here - one of {[block.kind for block in block_kinds]}."
        )
    args = block.body_args()
    extra = set(entry) - {arg.name for arg in args} - {"kind"}
    if extra:
        raise ValidationError(f"A '{kind}' block has unknown keys: {sorted(extra)}.")
    for arg in args:
        present = arg.name in entry and entry[arg.name] is not None
        if arg.required and not present:
            raise ValidationError(f"A '{kind}' block requires '{arg.name}'.")
        if present and arg.content is not None:
            validate_inline_content(arg.content, entry[arg.name])
    if kind == "table":
        validate_table(entry.get("header", []), entry.get("rows", []), entry.get("align"))


def validate_blocks(value: Any, block_kinds: tuple[BlockKindSpec, ...]) -> None:
    """Validate an array of blocks against the block kinds its field declares."""
    if not isinstance(value, list):
        raise ValidationError("A blocks value must be an array of blocks.")
    for entry in value:
        validate_block(entry, block_kinds)


def collect_ref_ids(content: str, value: Any,
                    block_kinds: tuple[BlockKindSpec, ...] | None = None) -> list[str]:
    """Every `{ref: pageId}` page id carried by an arg `value` of the given `content` shape.

    Used by the store to integrity-check inline page references before a write (the pure core cannot
    see other pages). Deliberately defensive - it only pulls a string `ref` out of a dict run and
    ignores anything else - because it runs *before* the grammar validation in `apply_command`, just
    as the existing cross-page ref check does; a malformed run is left for that validation to reject.
    `TABLE_ALIGN` carries no runs, so it yields nothing.

    `block_kinds` is the kinds a BLOCK_ARRAY arg accepts. Without it there is no way to know which of a
    block's keys hold runs, so that shape yields nothing rather than guessing - and reading the
    body args off the declared kinds is what keeps an overridden kind's runs reachable.
    """
    ids: list[str] = []

    def from_runs(runs: Any) -> None:
        if isinstance(runs, list):
            ids.extend(run["ref"] for run in runs
                       if isinstance(run, dict) and isinstance(run.get("ref"), str))

    if content == INLINE_RUNS:
        from_runs(value)
    elif content == INLINE_RUN_LISTS:
        for entry in value if isinstance(value, list) else []:
            from_runs(entry)
    elif content == INLINE_RUN_GRID:
        for row in value if isinstance(value, list) else []:
            for cell in row if isinstance(row, list) else []:
                from_runs(cell)
    elif content == BLOCK_ARRAY:
        # A block carries its runs one level deeper; without this the store's precheck could not
        # see them and a dangling ref would be written.
        for entry in value if isinstance(value, list) else []:
            ids.extend(_block_ref_ids(entry, block_kinds))
    return ids


def _block_ref_ids(entry: Any, block_kinds: tuple[BlockKindSpec, ...] | None) -> list[str]:
    """Every inline page ref one block carries, read through the body args its kind declares.

    Stays defensive like its caller: this runs before the grammar validation, so a malformed
    entry or a kind the field does not declare yields nothing and is left for validate_block.
    """
    if not isinstance(entry, dict) or block_kinds is None:
        return []
    block = next((block for block in block_kinds if block.kind == entry.get("kind")), None)
    if block is None:
        return []
    ids: list[str] = []
    for body in block.body_args():
        if body.content is not None:
            ids.extend(collect_ref_ids(body.content, entry.get(body.name)))
    return ids


def validate_table(header: Any, rows: Any, align: Any) -> None:
    """Table width consistency: every row (and `align`, if given) matches the header's column count."""
    width = len(header)
    for index, row in enumerate(rows):
        if len(row) != width:
            raise ValidationError(
                f"Table row {index} has {len(row)} cells but the header has {width} - widths must match."
            )
    if align is not None and len(align) != width:
        raise ValidationError(
            f"Table 'align' has {len(align)} entries but the header has {width} columns."
        )


def validate_pagetype_field_setters(page_type: PageType) -> list[str]:
    """A type declares at most one do-eligible setter for one (section, field).

    A `do` field edge names one command, so a second would be silently dropped from the
    self-direction rollup - a failure that shows up as missing guidance rather than an error.
    Before the block commands collapsed, five blocks fields each declared one add per kind;
    now every field has exactly one, and this keeps it that way.
    """
    errors: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for command in page_type.commands:
        if command.section is None or command.field is None:
            continue
        if not is_field_setter(command):
            continue
        target = (command.section, command.field)
        if target in seen:
            errors.append(
                f"{target[0]}.{target[1]} has two field setters "
                f"('{seen[target]}' and '{command.name}') - a `do` edge names one command."
            )
            continue
        seen[target] = command.name
    return errors


def validate_pagetype_setter_descriptions(page_type: PageType) -> list[str]:
    """A field setter (SET_SCALAR / SET_PROSE / ADD_ELEMENT) carries a short description of what it
    does ('set the summary', 'add a constraint'), never its field's authoring instruction: that
    lives once on the FieldSpec.description, and reaches an agent through describePageType's
    `sections` listing and the `instruction` key of a `next` field edge. Freeform blocks
    (ADD_BLOCK) already carry a short description and are untouched.
    """
    errors: list[str] = []
    for command in page_type.commands:
        if command.kind not in (SET_SCALAR, SET_PROSE, ADD_ELEMENT):
            continue
        section, field = command.section, command.field
        field_spec = (page_type.field_spec(section, field)
                      if section is not None and field is not None else None)
        if field_spec is None:
            errors.append(
                f"field setter '{command.name}' targets unknown field " +
                f"'{command.section}.{command.field}'."
            )
            continue
        if not command.description:
            errors.append(
                f"field setter '{command.name}' has no description; it must carry a " +
                f"short line saying what it sets."
            )
        if "\n" in command.description or command.description == field_spec.description:
            errors.append(
                f"field setter '{command.name}' carries the authoring instruction as " +
                f"its description; that text belongs once on the " +
                f"'{command.section}.{command.field}' FieldSpec, and the setter takes a short " +
                f"one-line description instead."
            )
    return errors


def validate_pagetype_block_args(page_type: PageType) -> list[str]:
    """Every block-carrying argument resolves to the block kinds its field declares.

    The check side of PageType's best-effort block-vocab resolution: an argument the resolver
    left unfilled (block_kinds None) means the command targets no field, an undeclared field, a
    non-blocks field, or an element field the list does not declare as block-bearing. Each such
    argument would otherwise accept any block, describe itself as an untyped array, and lose its
    cross-page ref check.
    """
    errors: list[str] = []
    for command in page_type.commands:
        for arg in command.args:
            if arg.content != BLOCK_ARRAY:
                continue
            if command.section is None or command.field is None:
                errors.append(f"command '{command.name}' carries blocks but targets no field.")
                continue
            field_spec = page_type.field_spec(command.section, command.field)
            if field_spec is None:
                errors.append(
                    f"command '{command.name}' carries blocks for "
                    f"{command.section}.{command.field}, which is not a declared field.")
                continue
            element_field = command.element_field or (
                arg.name if command.kind == ADD_ELEMENT else None)
            if element_field is None:
                if field_spec.kind != BLOCKS:
                    errors.append(
                        f"command '{command.name}' carries blocks for "
                        f"{command.section}.{command.field}, which is not a blocks field.")
                continue
            if field_spec.element_blocks_spec(element_field) is None:
                errors.append(
                    f"command '{command.name}' carries blocks for "
                    f"{command.section}.{command.field}.{element_field}, which is not declared "
                    f"as a block-bearing element field.")
    return errors


def validate_page_type(page_type: PageType) -> list[str]:
    """Every declaration rule for one page type, as a flat list of tag-prefixed messages.

    Walks the whole structure once - every section's every field, the status FSM, and the
    page-level command rules - and prefixes the page tag onto each message the per-part
    validators return, so a caller aggregating across a registry can locate every finding.
    """
    errors: list[str] = []
    for section in page_type.sections:
        for field in section.fields:
            errors.extend(validate_field_spec(field))
    errors.extend(validate_fsm_spec(page_type.fsm))
    errors.extend(validate_pagetype_field_setters(page_type))
    errors.extend(validate_pagetype_setter_descriptions(page_type))
    errors.extend(validate_pagetype_block_args(page_type))
    return [f"{page_type.tag}: {error}" for error in errors]


def validate_workspace_guidance(registry: Mapping[str, PageType]) -> list[str]:
    """Validate the workspace-guidance declarations across the registry, collecting the errors."""
    errors: list[str] = []
    descriptions: dict[str, tuple[str, str]] = {}   # field -> (description, first tag to declare it)
    for tag, page_type in registry.items():
        states = set(page_type.fsm.states)
        for spec in page_type.workspace_guidance:
            if not spec.field:
                errors.append(f"{tag}: a workspace guidance declares an empty field name.")
            if not spec.guidance_for:
                errors.append(
                    f"{tag}: workspace guidance '{spec.field}' declares no guidance_for statuses.")
            for status in spec.guidance_for:
                if status not in states:
                    errors.append(
                        f"{tag}: workspace guidance '{spec.field}' names unknown status '{status}'.")
            if not spec.description:
                errors.append(f"{tag}: workspace guidance '{spec.field}' has an empty description.")
            prior = descriptions.get(spec.field)
            if prior is None:
                descriptions[spec.field] = (spec.description, tag)
            elif prior[0] != spec.description:
                errors.append(
                    f"{tag}: workspace guidance '{spec.field}' description disagrees with '{prior[1]}'.")
    return errors


def validate_page_types(registry: Mapping[str, PageType]) -> None:
    """Validate every page type in `registry` in one pass, collecting all errors.

    Raises a single ValueError listing every declaration error found across the registry, or
    returns None when all are well-formed. This is where validation runs - the primary flows
    call it once at load, rather than each construction raising on the first problem it meets.
    """
    errors: list[str] = []
    for page_type in registry.values():
        errors.extend(validate_page_type(page_type))
    errors.extend(validate_workspace_guidance(registry))
    if errors:
        raise ValueError(
            "Invalid page-type declarations:\n" + "\n".join(f"- {error}" for error in errors))


def validate_fsm_spec(fsm: FSMSpec) -> list[str]:
    """Every state_guidance pair names a declared state, and no state twice."""
    errors: list[str] = []
    seen: set[str] = set()
    for state, _text in fsm.state_guidance:
        if state not in fsm.states:
            errors.append(f"{fsm.name}: state_guidance names unknown state '{state}'.")
        elif state in seen:
            errors.append(f"{fsm.name}: state_guidance names '{state}' twice.")
        seen.add(state)
    return errors


def _duplicate_block_kind_errors(where: str, block_kinds: tuple[BlockKindSpec, ...]) -> list[str]:
    """A field naming one kind twice is a declaration bug - the second is unreachable."""
    errors: list[str] = []
    seen: set[str] = set()
    for block in block_kinds:
        if block.kind in seen:
            errors.append(f"{where}: block kinds name '{block.kind}' twice.")
        seen.add(block.kind)
    return errors


def validate_element_blocks_spec(spec: ElementBlocksSpec) -> list[str]:
    """A block-bearing element field names a non-empty, duplicate-free set of block kinds."""
    errors: list[str] = []
    if not spec.block_kinds:
        errors.append(f"{spec.field}: a block-bearing element field declares no block kinds.")
    errors.extend(_duplicate_block_kind_errors(spec.field, spec.block_kinds))
    for block in spec.block_kinds:
        if not block.kind:
            errors.append("A block kind must be a non-empty name.")
    return errors


def validate_field_spec(field: FieldSpec) -> list[str]:
    """A field's block kinds and block-bearing element fields are well-formed.

    Block kinds are only valid on a blocks field, a blocks field declares them, and no kind
    is named twice; element_blocks are only valid on a list field, each naming one of its element
    fields exactly once. Recurses into every declared kind and element-blocks spec.
    """
    errors: list[str] = []
    if field.block_kinds and field.kind != BLOCKS:
        errors.append(f"{field.key}: block_kinds is only valid on a blocks field.")
    if field.kind == BLOCKS and not field.block_kinds:
        errors.append(f"{field.key}: a blocks field declares no block kinds.")
    errors.extend(_duplicate_block_kind_errors(field.key, field.block_kinds))
    for block in field.block_kinds:
        if not block.kind:
            errors.append("A block kind must be a non-empty name.")
    seen: set[str] = set()
    for blocks in field.element_blocks:
        if field.kind != LIST:
            errors.append(f"{field.key}: element_blocks is only valid on a list field.")
        if blocks.field not in (field.element_fields or ()):
            errors.append(
                f"{field.key}: element_blocks names '{blocks.field}', which is not one of " +
                f"element_fields."
            )
        if blocks.field in seen:
            errors.append(f"{field.key}: element_blocks names '{blocks.field}' twice.")
        seen.add(blocks.field)
        errors.extend(validate_element_blocks_spec(blocks))
    return errors

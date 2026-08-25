"""The blocks grammar: what an inline run may be, and what a block may be."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...errors import ValidationError

if TYPE_CHECKING:
    # Annotation only: a page type calls these, so importing it here at runtime
    # would point the dependency back the way it came.
    from .pagetype import PageType
from .args import BlockKindSpec, CommandSpec
from .commands import is_field_setter
from .specs import ADD_ELEMENT, SET_PROSE, SET_SCALAR
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


def validate_block(entry: Any, kinds: tuple[BlockKindSpec, ...]) -> None:
    """Validate one block against the vocabulary its field declares.

    A block is an object carrying a `kind` the field declares plus exactly that kind's body args,
    each checked the way the per-kind command used to check it - so a block that is legal to
    create is legal to set, and the reverse. Both paths share this grammar rather than restating
    it. The body args come from the matched BlockKindSpec, never from the global BLOCK_ARGS,
    which is what makes a per-field override real.
    """
    if not isinstance(entry, dict):
        raise ValidationError(
            f"A block must be an object with a 'kind', got {type(entry).__name__}."
        )
    kind = entry.get("kind")
    spec = next((k for k in kinds if k.kind == kind), None)
    if spec is None:
        raise ValidationError(
            f"Block kind {kind!r} is not accepted here - one of {[k.kind for k in kinds]}."
        )
    specs = spec.body_args()
    extra = set(entry) - {spec.name for spec in specs} - {"kind"}
    if extra:
        raise ValidationError(f"A '{kind}' block has unknown keys: {sorted(extra)}.")
    for spec in specs:
        present = spec.name in entry and entry[spec.name] is not None
        if spec.required and not present:
            raise ValidationError(f"A '{kind}' block requires '{spec.name}'.")
        if present and spec.content is not None:
            validate_inline_content(spec.content, entry[spec.name])
    if kind == "table":
        validate_table(entry.get("header", []), entry.get("rows", []), entry.get("align"))


def validate_blocks(value: Any, kinds: tuple[BlockKindSpec, ...]) -> None:
    """Validate an array of blocks against the vocabulary its field declares."""
    if not isinstance(value, list):
        raise ValidationError("A blocks value must be an array of blocks.")
    for entry in value:
        validate_block(entry, kinds)


def collect_ref_ids(content: str, value: Any,
                    kinds: tuple[BlockKindSpec, ...] | None = None) -> list[str]:
    """Every `{ref: pageId}` page id carried by an arg `value` of the given `content` shape.

    Used by the store to integrity-check inline page references before a write (the pure core cannot
    see other pages). Deliberately defensive - it only pulls a string `ref` out of a dict run and
    ignores anything else - because it runs *before* the grammar validation in `apply_command`, just
    as the existing cross-page ref check does; a malformed run is left for that validation to reject.
    `TABLE_ALIGN` carries no runs, so it yields nothing.

    `kinds` is the vocabulary of a BLOCK_ARRAY arg. Without it there is no way to know which of a
    block's keys hold runs, so that shape yields nothing rather than guessing - and reading the
    body args off the vocabulary rather than BLOCK_ARGS is what keeps an overridden kind's runs
    reachable.
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
            ids.extend(_block_ref_ids(entry, kinds))
    return ids


def _block_ref_ids(entry: Any, kinds: tuple[BlockKindSpec, ...] | None) -> list[str]:
    """Every inline page ref one block carries, read through the body args its kind declares.

    Stays defensive like its caller: this runs before the grammar validation, so a malformed
    entry or a kind the field does not declare yields nothing and is left for validate_block.
    """
    if not isinstance(entry, dict) or kinds is None:
        return []
    spec = next((kind for kind in kinds if kind.kind == entry.get("kind")), None)
    if spec is None:
        return []
    ids: list[str] = []
    for arg in spec.body_args():
        if arg.content is not None:
            ids.extend(collect_ref_ids(arg.content, entry.get(arg.name)))
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


def validate_pagetype_field_setters(page_type: PageType) -> None:
    """Reject a type declaring two do-eligible setters for one (section, field).

    A `do` field edge names one command, so a second would be silently dropped from the
    self-direction rollup - a failure that shows up as missing guidance rather than an error.
    Before the block commands collapsed, five blocks fields each declared one add per kind;
    now every field has exactly one, and this keeps it that way.
    """
    seen: dict[tuple[str, str], str] = {}
    for command in page_type.commands:
        if command.section is None or command.field is None:
            continue
        if not is_field_setter(command):
            continue
        target = (command.section, command.field)
        if target in seen:
            raise ValueError(
                f"{page_type.tag}: {target[0]}.{target[1]} has two field setters "
                f"('{seen[target]}' and '{command.name}') - a `do` edge names one command."
            )
        seen[target] = command.name


def validate_pagetype_setter_descriptions(page_type: PageType) -> None:
    """A field setter (SET_SCALAR / SET_PROSE / ADD_ELEMENT) carries a short description of what it
    does ('set the summary', 'add a constraint'), never its field's authoring instruction: that
    lives once on the FieldSpec.description, and reaches an agent through describePageType's
    `sections` listing and the `instruction` key of a `next` field edge. Freeform blocks
    (ADD_BLOCK) already carry a short description and are untouched.
    """
    for command in page_type.commands:
        if command.kind not in (SET_SCALAR, SET_PROSE, ADD_ELEMENT):
            continue
        section, field = command.section, command.field
        field_spec = (page_type.field_spec(section, field)
                      if section is not None and field is not None else None)
        if field_spec is None:
            raise ValueError(
                f"{page_type.tag}: field setter '{command.name}' targets unknown field " +
                f"'{command.section}.{command.field}'."
            )
        if not command.description:
            raise ValueError(
                f"{page_type.tag}: field setter '{command.name}' has no description; it must carry a " +
                f"short line saying what it sets."
            )
        if "\n" in command.description or command.description == field_spec.description:
            raise ValueError(
                f"{page_type.tag}: field setter '{command.name}' carries the authoring instruction as " +
                f"its description; that text belongs once on the " +
                f"'{command.section}.{command.field}' FieldSpec, and the setter takes a short " +
                f"one-line description instead."
            )

"""How a page type declares its commands, and the page type itself."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ...errors import ValidationError
from .args import (
    _INDEX,
    _PRECEDING,
    ArgSpec,
    CommandSpec,
    _array,
    _integer,
    _object,
    _same_named,
    _text,
)
from .fields import FieldSpec, SectionSpec
from .specs import (
    ADD_BLOCK,
    ADD_ELEMENT,
    BLOCK,
    BLOCKS,
    BLOCK_ARRAY,
    COMPOUND,
    ELEMENT_TRANSITION,
    LIST,
    PROSE,
    REMOVE_BLOCK,
    REMOVE_ELEMENT,
    REORDER_BLOCK,
    REORDER_ELEMENT,
    SET_BLOCK,
    SET_ELEMENT_FIELD,
    SET_PROSE,
    SET_SCALAR,
    TRANSITION,
    AutoChildSpec,
    ChildStateGuard,
    FSMSpec,
    ParentStateGuard,
    RefCheck,
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
        self._validate_field_setter_descriptions()
        self._validate_single_field_setter()

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
        if arg.content not in (BLOCK, BLOCK_ARRAY):
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
            return replace(arg, block_kinds=field_spec.block_vocabulary())
        spec = field_spec.element_blocks_spec(element_field)
        if spec is None:
            raise ValueError(
                f"{self.tag}: command '{command.name}' carries blocks for "
                f"{command.section}.{command.field}.{element_field}, which is not declared "
                f"as a block-bearing element field.")
        return replace(arg, block_kinds=spec.vocabulary())

    def _validate_single_field_setter(self) -> None:
        """Reject a type declaring two do-eligible setters for one (section, field).

        A `do` field edge names one command, so a second would be silently dropped from the
        self-direction rollup - a failure that shows up as missing guidance rather than an error.
        Before the block commands collapsed, five blocks fields each declared one add per kind;
        now every field has exactly one, and this keeps it that way.
        """
        seen: dict[tuple[str, str], str] = {}
        for command in self.commands:
            if command.section is None or command.field is None:
                continue
            if not is_field_setter(command):
                continue
            target = (command.section, command.field)
            if target in seen:
                raise ValueError(
                    f"{self.tag}: {target[0]}.{target[1]} has two field setters "
                    f"('{seen[target]}' and '{command.name}') - a `do` edge names one command."
                )
            seen[target] = command.name

    def _validate_field_setter_descriptions(self) -> None:
        """A field setter (SET_SCALAR / SET_PROSE / ADD_ELEMENT) carries a short description of what it
        does ('set the summary', 'add a constraint'), never its field's authoring instruction: that
        lives once on the FieldSpec.description, and reaches an agent through describePageType's
        `sections` listing and the `instruction` key of a `next` field edge. Freeform blocks
        (ADD_BLOCK / SET_BLOCK) already carry a short description and are untouched.
        """
        for command in self.commands:
            if command.kind not in (SET_SCALAR, SET_PROSE, ADD_ELEMENT):
                continue
            section, field = command.section, command.field
            field_spec = (self.field_spec(section, field)
                          if section is not None and field is not None else None)
            if field_spec is None:
                raise ValueError(
                    f"{self.tag}: field setter '{command.name}' targets unknown field " +
                    f"'{command.section}.{command.field}'."
                )
            if not command.description:
                raise ValueError(
                    f"{self.tag}: field setter '{command.name}' has no description; it must carry a " +
                    f"short line saying what it sets."
                )
            if "\n" in command.description or command.description == field_spec.description:
                raise ValueError(
                    f"{self.tag}: field setter '{command.name}' carries the authoring instruction as " +
                    f"its description; that text belongs once on the " +
                    f"'{command.section}.{command.field}' FieldSpec, and the setter takes a short " +
                    f"one-line description instead."
                )

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


# --- Field-op command helpers (the CommandSpec analog of _scalar/_prose/_list/_blocks) --------
# Each is a PURE factory returning a CommandSpec (or a tuple of them) to spread into a PageType's
# `commands=(...)`, in the same family/style as the FieldSpec helpers above. The first positional
# argument is the section; every other argument is named. Command names and the remove/reorder
# `<noun>Id` arg are DERIVED from the section/field so the minimal call carries no name plumbing
# (list_cmds("constraints", ...) -> addConstraint/removeConstraint/reorderConstraint). A `name=`
# override preserves a name the derivation would not produce. A flow-populated list asks list_cmds for
# a subset (e.g. reorder only, add=False/remove=False); an add-only list drops remove/reorder the same
# way.


def _cap(word: str) -> str:
    """'summary' -> 'Summary' (leaving the rest of the word as-is, so 'dataModel' -> 'DataModel')."""
    return word[:1].upper() + word[1:]


def _a(word: str) -> str:
    """The indefinite article for `word`, so a generated description reads 'an invariant' / 'a step'."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def _singular(word: str) -> str:
    """A small, rule-based singularizer for deriving a command noun from a (plural) field/section key
    ('codeReferences'->'codeReference', 'dependencies'->'dependency', 'documentation' unchanged). A
    plural the rule mishandles is fixed by passing `singular=` to the helper."""
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("s"):
        return word[:-1]
    return word


def _setter_label(section: str, field: str, label: str | None) -> str:
    """The noun a field setter's short description reads with: the explicit `label`, else the word its
    command name is derived from - the section for the conventional 'body' field, the field key
    otherwise. A camelCase key, or a section displayed under another name, passes `label=`."""
    return label or (section if field == "body" else field)


def set_prose_cmd(section: str, *, field: str = "body", name: str | None = None,
                  label: str | None = None,
                  legal_in: tuple[str, ...] | None = None) -> CommandSpec:
    """A SET_PROSE command for a prose field; name defaults to set<Section> (setSummary, setOverview)
    and the description to 'set the <section>'. The field's instruction is not copied here."""
    return CommandSpec(name or f"set{_cap(section)}", SET_PROSE,
                       f"set the {_setter_label(section, field, label)}",
                       section=section, field=field, args=(_text(),), legal_in=legal_in)


def set_scalar_cmd(section: str, field: str, *, name: str | None = None,
                   label: str | None = None,
                   choices: tuple[str, ...] | None = None,
                   legal_in: tuple[str, ...] | None = None) -> CommandSpec:
    """A SET_SCALAR command; name defaults to set<Field> (setKind, setComponent) and the description
    to 'set the <field>'. The single arg is named after the field and carries the field's `choices`
    when it is an enum. The field's instruction is not copied here."""
    return CommandSpec(name or f"set{_cap(field)}", SET_SCALAR,
                       f"set the {_setter_label(section, field, label)}",
                       section=section, field=field,
                       args=(_text(field, choices=choices),), legal_in=legal_in)


def list_cmds(section: str, *, field: str = "items", singular: str | None = None,
              label: str | None = None, add_args: tuple[ArgSpec, ...] | None = None,
              element_blocks: tuple[str, ...] = (),
              legal_in: tuple[str, ...] | None = None, ref_check: RefCheck | None = None,
              add: bool = True, remove: bool = True, reorder: bool = True,
              add_name: str | None = None, remove_name: str | None = None,
              reorder_name: str | None = None) -> tuple[CommandSpec, ...]:
    """The add/remove/reorder commands for a list field; select a subset with add=/remove=/reorder=
    (a flow-populated list that is filled elsewhere asks for its reorder only). The noun (command
    names + `<noun>Id` arg) is the singular of the field when it is not the generic 'items', else
    of the section; `singular=` overrides an irregular plural. The add's element_map is derived
    from `add_args` (each mapped onto a same-named element field, so `add_args` names are the
    field names), and _INDEX/_PRECEDING are appended to it; the reorder carries the anchored
    (toIndex + precedingId) stale-read guard - so the 'every list field has a reorder' and 'every
    add supports positioned insert' invariants hold by construction. An add that references
    another page carries a `ref_check`; the remove and reorder name an element already on this
    page, so they do not.

    `element_blocks` names the element fields whose blocks the add can carry. Each gives the
    add one optional array arg named after that element field, holding the blocks the element is
    created with - so creating an element and giving it content is one command, and a batch never
    has to name an id it has not committed. Those args stay out of element_map, because the raw
    argument is never written onto the element; the add converts it into id'd blocks. Only the
    names are given here, because they decide the add's argument list; the kinds each one accepts
    are resolved by PageType from the field's own declaration, and a name the field does not
    declare as block-bearing is rejected there."""
    noun = singular or _singular(field if field != "items" else section)
    cap, id_arg, label = _cap(noun), f"{noun}Id", label or noun
    block_args = tuple(
        _array(name, content=BLOCK_ARRAY, required=False,
               description=f"the {name} blocks to create the {noun} with")
        for name in element_blocks)
    out: list[CommandSpec] = []
    if add:
        out.append(CommandSpec(add_name or f"add{cap}", ADD_ELEMENT, f"add {_a(label)} {label}",
                               section=section, field=field,
                               args=(*(add_args or tuple()), *block_args, _INDEX, _PRECEDING),
                               element_map=_same_named(add_args or tuple()),
                               ref_check=ref_check, legal_in=legal_in))
    if remove:
        out.append(CommandSpec(remove_name or f"remove{cap}", REMOVE_ELEMENT, f"remove {_a(label)} {label}",
                               section=section, field=field, args=(_text(id_arg),), legal_in=legal_in))
    if reorder:
        out.append(CommandSpec(
            reorder_name or f"reorder{cap}", REORDER_ELEMENT,
            f"move {_a(label)} {label} to an anchored position (precedingId guards a stale read)",
            section=section, field=field,
            args=(_text(id_arg), _integer("toIndex"), _PRECEDING), legal_in=legal_in))
    return tuple(out)


def element_cmds(section: str, *, field: str = "items", singular: str | None = None,
                 marks: tuple[tuple[str, str, str, Any] | tuple[str, str, str], ...], legal_in: tuple[str, ...] | None = None) -> tuple[CommandSpec, ...]:
    """ELEMENT_TRANSITION commands for an element-FSM list. Each `marks` entry is
    (name, event, description) or (name, event, description, extra_args) - the derived `<noun>Id` arg
    identifies the element, and any `extra_args` are appended and mapped onto same-named element fields
    (so a transition can also write a field, not just fire its event)."""
    id_arg = f"{singular or _singular(section)}Id"
    out: list[CommandSpec] = []
    for mark in marks:
        name, event, description = mark[0], mark[1], mark[2]
        extra = mark[3] if len(mark) > 3 else ()
        out.append(CommandSpec(name, ELEMENT_TRANSITION, description, section=section, field=field,
                               event=event, args=(_text(id_arg), *extra),
                               element_map=_same_named(extra), legal_in=legal_in))
    return tuple(out)


def set_element_field_cmd(section: str, *, name: str, const: tuple[str, Any], description: str = "",
                          field: str = "items", singular: str | None = None,
                          legal_in: tuple[str, ...] | None = None) -> CommandSpec:
    """A SET_ELEMENT_FIELD command that stamps a constant `(field, value)` onto the id'd element - the
    flag-setting shape (raise a fixed flag on one element without touching the rest)."""
    id_arg = f"{singular or _singular(section)}Id"
    return CommandSpec(name, SET_ELEMENT_FIELD, description, section=section, field=field,
                       args=(_text(id_arg),), element_const=(const,), legal_in=legal_in)


def is_field_setter(command: CommandSpec) -> bool:
    """Whether `command` writes typed field content into a (section, field).

    A SET_SCALAR, a SET_PROSE, an ADD_ELEMENT (including the element-FSM adds addStep / addCase /
    askQuestion), or a page-level ADD_BLOCK. Not setters: SET_BLOCK (an edit of one existing
    block), an element-scoped ADD_BLOCK (the element it fills must exist first, so it is not a
    stage's own work), REMOVE_* / REORDER_* (structure, not content), SET_ELEMENT_FIELD (a flag on
    an existing element), ELEMENT_TRANSITION (fires an element's own FSM), TRANSITION / COMPOUND
    (page-status edges), and the universal ADD_LINK / SET_TITLE.

    The page-type-agnostic classifier behind the self-direction `do` list, where each field gets
    one entry naming the one command that authors it. It lives here rather than beside that
    rollup because PageType's post-init validation needs it too and this module cannot import
    commands.py, so the rollup and the declaration-time check read one rule.
    """
    if command.kind == ADD_BLOCK:
        return command.element_field is None
    return command.kind in (SET_SCALAR, SET_PROSE, ADD_ELEMENT)


def blocks_cmds(section: str, *, field: str = "body", label: str | None = None,
                add_name: str | None = None, set_name: str | None = None,
                remove_name: str = "removeBlock", remove_desc: str = "remove a block",
                reorder_name: str = "reorderBlock", reorder_desc: str | None = None,
                legal_in: tuple[str, ...] | None = None) -> tuple[CommandSpec, ...]:
    """A blocks field's whole authoring surface: add, set, remove, reorder - four commands.

    The field declares its vocabulary once and PageType resolves it onto these arguments, so the
    commands and the validator read one declaration. A block's kind travels as data inside the
    argument, which is what replaces one add (and one set) per kind.

        add -> add<Label>(blocks, index?, precedingId?)      ADD_BLOCK
        set -> set<Label>Block(blockId, block)               SET_BLOCK

    `Label` follows _setter_label - the section for the conventional `body` field, the field key
    otherwise - so a call carries no name plumbing; `label=` / `add_name=` / `set_name=` override
    where the derivation reads badly. remove and reorder keep the names, defaults and argument
    shapes they have always had. A type with more than one blocks field passes distinct
    remove_name / reorder_name so command names stay unique.
    """
    noun = _setter_label(section, field, label)
    cap = _cap(noun)
    reorder_desc = reorder_desc or (
        "move a block to an anchored position (precedingId guards a stale read)")
    add = CommandSpec(
        add_name or f"add{cap}", ADD_BLOCK, f"add blocks to the {noun}",
        section=section, field=field,
        args=(_array("blocks", content=BLOCK_ARRAY,
                     description="the blocks to add, each naming its own kind"),
              _INDEX, _PRECEDING),
        legal_in=legal_in)
    set_cmd = CommandSpec(
        set_name or f"set{cap}Block", SET_BLOCK, f"replace one block in the {noun}",
        section=section, field=field,
        args=(_text("blockId"),
              _object("block", content=BLOCK,
                      description="the replacement block, naming its own kind")),
        legal_in=legal_in)
    remove = CommandSpec(remove_name, REMOVE_BLOCK, remove_desc, section=section, field=field,
                         args=(_text("blockId"),), legal_in=legal_in)
    reorder = CommandSpec(reorder_name, REORDER_BLOCK, reorder_desc, section=section, field=field,
                          args=(_text("blockId"), _integer("toIndex"), _PRECEDING),
                          legal_in=legal_in)
    return (add, set_cmd, remove, reorder)


def element_blocks_cmds(section: str, element_field: str, *, field: str = "items",
                        singular: str | None = None,
                        legal_in: tuple[str, ...] | None = None) -> tuple[CommandSpec, ...]:
    """The same four commands for one block-bearing element field, each led by the element id.

        add     -> add<Noun><Field>(<noun>Id, blocks, index?, precedingId?)
        set     -> set<Noun><Field>Block(<noun>Id, blockId, block)
        remove  -> remove<Field>Block(<noun>Id, blockId)
        reorder -> reorder<Field>Block(<noun>Id, blockId, toIndex, precedingId?)

    The element noun leads and the declared field key follows, with no pluralizing, so a step's
    detail reads addStepDetail. remove and reorder keep the names they have always had.
    Every command carries CommandSpec.element_field, which is the single seam routing it to an
    element's block array instead of the section's own.

    The add that creates an element holding its blocks stays on list_cmds; this is the surface
    for appending to an element that already exists, which is what keeps a step's detail from
    becoming write-once at creation.
    """
    noun = singular or _singular(field if field != "items" else section)
    id_arg, cap, fcap = f"{noun}Id", _cap(noun), _cap(element_field)
    return (
        CommandSpec(f"add{cap}{fcap}", ADD_BLOCK,
                    f"add blocks to {_a(noun)} {noun}'s {element_field}",
                    section=section, field=field, element_field=element_field,
                    args=(_text(id_arg),
                          _array("blocks", content=BLOCK_ARRAY,
                                 description="the blocks to add, each naming its own kind"),
                          _INDEX, _PRECEDING),
                    legal_in=legal_in),
        CommandSpec(f"set{cap}{fcap}Block", SET_BLOCK,
                    f"replace one block in {_a(noun)} {noun}'s {element_field}",
                    section=section, field=field, element_field=element_field,
                    args=(_text(id_arg), _text("blockId"),
                          _object("block", content=BLOCK,
                                  description="the replacement block, naming its own kind")),
                    legal_in=legal_in),
        CommandSpec(f"remove{cap}{fcap}", REMOVE_BLOCK,
                    f"remove a block from {_a(noun)} {noun}'s {element_field}",
                    section=section, field=field, element_field=element_field,
                    args=(_text(id_arg), _text("blockId")), legal_in=legal_in),
        CommandSpec(f"reorder{cap}{fcap}", REORDER_BLOCK,
                    f"move a block in {_a(noun)} {noun}'s {element_field} to an anchored "
                    f"position (precedingId guards a stale read)",
                    section=section, field=field, element_field=element_field,
                    args=(_text(id_arg), _text("blockId"), _integer("toIndex"), _PRECEDING),
                    legal_in=legal_in),
    )


def transition_cmd(name: str, description: str, *, legal_in: tuple[str, ...] | None = None,
                   event: str | None = None, agency: str = "agent",
                   requires: tuple[tuple[str, str], ...] = (),
                   guards: tuple[ChildStateGuard, ...] = (),
                   parent_guards: tuple[ParentStateGuard, ...] = ()) -> CommandSpec:
    """A page-status TRANSITION command whose `description` carries the edge as 'from -> to'. A written
    '->' is substituted once to the arrow glyph '→', which the edge is then split on. The dest is the
    first word after the arrow - a trailing parenthetical is ignored (so 'a -> b (note)' resolves to
    b) - and is NOT overridable; it names the destination state. The source is the text before the
    arrow, which `legal_in` overrides (for a multi-source edge, or one whose 'from' is prose rather
    than a state name). `event` defaults to `name` (every transition fires an event of its own name)."""
    description = description.replace("->", "→")
    before, arrow, after = description.partition("→")
    words = after.split()
    if not arrow or not words:
        raise ValueError(f"transition_cmd({name!r}): description must read 'from -> to', got {description!r}")
    sources = tuple(legal_in) if legal_in is not None else (before.strip(),)
    return CommandSpec(name, TRANSITION, description, event=event or name, dest=words[0], legal_in=sources,
                       agency=agency, requires=requires, guards=guards, parent_guards=parent_guards)


def transition_on_add_cmd(name: str, t_description: str, *, legal_in: tuple[str, ...] | None = None, section: str,
                 field: str, add_args: tuple[ArgSpec, ...], description: str = "", event: str | None = None,
                 agency: str = "agent", requires: tuple[tuple[str, str], ...] = (),
                 guards: tuple[ChildStateGuard, ...] = (),
                 parent_guards: tuple[ParentStateGuard, ...] = ()) -> CommandSpec:
    """A COMPOUND that atomically adds an element to a list field AND fires a page transition.
    `t_description` carries the edge as 'from -> to'. The outer command owns
    the FSM edge (event/source/dest/agency) and its args; the element_map is derived
    from `add_args` (same-named); the two inner steps are the add and the transition."""
    t_description = t_description.replace("->", "→")
    before, arrow, after = t_description.partition("→")
    words = after.split()
    if not arrow or not words:
        raise ValueError(f"transition_on_add_cmd({name!r}): t_description must read 'from -> to', got {t_description!r}")
    sources = tuple(legal_in) if legal_in is not None else (before.strip(),)
    return CommandSpec(
        name, COMPOUND, f"{description} ({t_description})", event=event or name, dest=words[0], legal_in=sources, agency=agency,
        args=add_args, requires=requires, guards=guards, parent_guards=parent_guards,
        steps=(
            CommandSpec(f"_{name}Add", ADD_ELEMENT, section=section, field=field,
                        element_map=_same_named(add_args)),
            CommandSpec(f"_{name}", TRANSITION, event=event or name),
        ),
    )


def initial_sections(page_type: PageType) -> dict[str, dict[str, Any]]:
    """The empty section/field state a freshly created page of this type starts with."""
    sections: dict[str, dict[str, Any]] = {}
    for section in page_type.sections:
        field_values: dict[str, Any] = {}
        for field_spec in section.fields:
            if field_spec.kind == PROSE:
                field_values[field_spec.key] = ""
            elif field_spec.kind in (LIST, BLOCKS):
                field_values[field_spec.key] = []
            else:  # SCALAR
                field_values[field_spec.key] = None
        sections[section.key] = field_values
    return sections

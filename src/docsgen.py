"""Pure generation of Sphinx docs for every page type AND every one of its FSM states.

For each registered page type this captures ``describe_page_type`` (the type-level schema)
and, for each reachable status, ``describe_mutations`` on a content-less page pinned at that
status - then emits one Markdown page per page-type-state (``<tag>-<state>``): a
``statemachine-diagram`` (``:format: dot``) navigated to the state via ``:events:`` (the
shortest transition path that reaches it), the transitions legal out of the state (linked to
the sibling state docs), and the authoring commands legal there.

Everything here is pure - it reads the registry and returns strings, performing no I/O. The
``scripts/gen_page_type_docs.py`` driver writes the returned docs into the docsite. Two design
points make the FSM walk trivial:

- The ``StateMachine`` classes (``src.statecharts``) are guardless - required-content
  preconditions and status-scoped command locks live in ``commands.py``, not the machine - so the
  diagram directive can replay any ``:events:`` path with no content, and a plain breadth-first walk
  over ``FSMSpec.transitions`` yields the path to every state.
- To *enumerate* a state's outgoing transitions on a content-less page, ``describe_mutations`` is
  called with ``ignore_requirements=True`` so a content-gated transition still reports available on
  the topology alone; the ``legal_in`` status-lock still applies, so any status-scoped command lock
  is documented truthfully.
"""

from __future__ import annotations

from collections import deque
from types import ModuleType
from typing import Any

from .describe import describe_mutations, describe_page_type
from .model import Page
from .pagetypes.core.specs import COMPOUND, TRANSITION, FSMSpec
from .pagetypes.core.pagetype import PageType, initial_sections
from .pagetypes._registry import is_test_mode, registered_pagetypes

# Kinds that fire the *page-status* FSM (element_transition fires an element's own FSM, so it is an
# authoring command from the page's point of view, not a status transition).
_PAGE_TRANSITION_KINDS = (TRANSITION, COMPOUND)

_DIAGRAM_FORMAT = "dot"
STATES_INDEX_STEM = "states"          # the generated toctree index (page-types/states.md)


# --- FSM navigation ----------------------------------------------------------
def reachable_states(fsm: FSMSpec) -> dict[str, list[str]]:
    """Every state reachable from the FSM's initial state, mapped to the shortest sequence of
    event names that reaches it (breadth-first; the initial state maps to ``[]``).

    This event path is exactly what the ``statemachine-diagram`` ``:events:`` option replays to
    navigate the (guardless) machine to the state, so the shortest path keeps the option short.
    """
    outgoing: dict[str, list[tuple[str, str]]] = {}
    for event, source, dest, _agency in fsm.transitions:
        outgoing.setdefault(source, []).append((event, dest))

    paths: dict[str, list[str]] = {fsm.initial: []}
    queue: deque[str] = deque([fsm.initial])
    while queue:
        state = queue.popleft()
        for event, dest in outgoing.get(state, []):
            if dest not in paths:
                paths[dest] = paths[state] + [event]
                queue.append(dest)
    return paths


def _seed_page(page_type: PageType, state: str) -> Page:
    """A content-less page of ``page_type`` pinned at ``state`` - enough for ``describe_mutations``
    to report the command surface (FSM topology + ``legal_in``) at that state."""
    return Page(
        id=f"{page_type.tag}:doc",
        type=page_type.tag,
        title=f"{page_type.name} ({state})",
        status=state,
        sections=initial_sections(page_type),
    )


# --- the machine a state page draws ------------------------------------------
def _bindings_module() -> ModuleType:
    """The module binding the machine classes for the page types being documented.

    Imported here rather than at the top so the fixtures stay out of a live server's import
    graph."""
    if is_test_mode():
        from . import testcharts

        return testcharts
    from . import statecharts

    return statecharts


def page_machine_qualname(tag: str) -> str:
    """The importable dotted path of the page-status machine bound for page type `tag`.

    The diagram directive imports the class by this path. A machine is built under its spec's own
    name and bound under that name with a `Machine` suffix, so the path is derived from the page
    type; checking that it really is bound is what keeps a type whose binding was never added from
    going silently undocumented.
    """
    page_type = registered_pagetypes().get(tag)
    if page_type is None:
        raise KeyError(f"Unknown page type {tag!r}.")
    module = _bindings_module()
    name = f"{page_type.fsm.name}Machine"
    if not hasattr(module, name):
        raise KeyError(
            f"No page-status machine is bound in {module.__name__} for page type {tag!r}.")
    return f"{module.__name__}.{name}"


# --- Markdown rendering ------------------------------------------------------
def _stem(tag: str, state: str) -> str:
    return f"{tag}-{state}"


def _diagram_block(qualname: str, tag: str, state: str, events: list[str]) -> str:
    lines = ["```{statemachine-diagram} " + qualname, f":format: {_DIAGRAM_FORMAT}"]
    if events:
        lines.append(":events: " + ", ".join(events))
    else:
        lines.append(":events:")
    lines.append("```")
    return "\n".join(lines)


def _transitions_section(tag: str, state: str,
                         transitions: list[dict[str, Any]], event_dest: dict[str, str]) -> str:
    if not transitions:
        return f"`{state}` is a terminal state - it has no outgoing transitions."
    bullets = [f"From `{state}` these transitions are available:", ""]
    for command in transitions:
        dest = event_dest[command["event"]]
        link = f"[{dest}]({_stem(tag, dest)}.md)"
        note = ""
        if command["requires"]:
            fields = ", ".join(f"`{req['section']}.{req['field']}`" for req in command["requires"])
            note = f" - blocked until {fields} is populated"
        bullets.append(f"- **{command['name']}** → {link} *({command['agency']})*{note}")
    return "\n".join(bullets)


def _authoring_section(state: str, authoring: list[dict[str, Any]]) -> str:
    if not authoring:
        return f"No authoring commands are legal in `{state}`."
    bullets = ["The authoring commands legal here via `describeMutations`:", ""]
    for command in authoring:
        bullets.append(_bullet(f"- `{command['name']}`", command["description"], []))
    return "\n".join(bullets)


def _arg_signature(args_schema: dict[str, Any]) -> str:
    """A compact `name, optional?` signature from a command's JSON-Schema arg object."""
    required = set(args_schema.get("required", []))
    return ", ".join(name if name in required else f"{name}?"
                     for name in args_schema.get("properties", {}))


def _bullet(marker: str, description: str, notes: list[str]) -> str:
    """One markdown list item: a `marker`, its description, and any schema `notes`.

    A one-line description stays inline after the marker, with the notes trailing. A multi-line one
    (a wrapped field instruction) becomes an indented block instead - flush left it would close the
    list item - and the notes move up onto the marker line, beside the key they describe.
    """
    suffix = " · " + " · ".join(notes) if notes else ""
    if "\n" not in description:
        return marker + (f" - {description}" if description else "") + suffix
    indent = " " * (len(marker) - len(marker.lstrip()) + 2)   # past this item's "- " bullet
    return "\n".join([marker + suffix,
                      *(f"{indent}{line}".rstrip() for line in description.splitlines())])


def _field_line(field: dict[str, Any]) -> str:
    notes = []
    if field["choices"]:
        notes.append("one of " + ", ".join(f"`{choice}`" for choice in field["choices"]))
    if field["elementFields"]:
        notes.append("element fields: " + ", ".join(f"`{name}`" for name in field["elementFields"]))
    if field["elementStates"]:
        notes.append("element states: " + ", ".join(f"`{name}`" for name in field["elementStates"]))
    if field["elementBlocks"]:
        notes.append("block element fields: " + ", ".join(
            f"`{spec['field']}` ({', '.join(spec['kinds'])})" for spec in field["elementBlocks"]))
    if field.get("blockKinds"):
        notes.append("block kinds: " + ", ".join(f"`{kind}`" for kind in field["blockKinds"]))
    return _bullet(f"  - `{field['key']}` *({field['kind']})*", field["description"], notes)


def _command_line(command: dict[str, Any]) -> str:
    notes = []
    if command["requires"]:
        notes.append("requires " + ", ".join(f"`{req['section']}.{req['field']}`"
                                              for req in command["requires"]))
    if command["legalIn"]:
        notes.append("legal in " + ", ".join(f"`{status}`" for status in command["legalIn"]))
    return _bullet(f"- `{command['name']}({_arg_signature(command['args'])})` *({command['kind']})*",
                   command["description"], notes)


def _page_type_section(described: dict[str, Any]) -> str:
    """Render the full `describe_page_type` result - the type's sections/fields and its complete
    command set. Identical across every state of a type; captured on each state page so a state page
    is a self-contained reference for its page type."""
    sections = ["### Sections", ""]
    for section in described["sections"]:
        sections.append(f"- **{section['name']}** (`{section['key']}`)")
        sections.extend(_field_line(field) for field in section["fields"])
    commands = ["### Commands", ""] + [_command_line(command) for command in described["commands"]]
    return "\n\n".join([
        "## Page type schema",
        (f"The `{described['tag']}` page-type schema via `describePageType`."),
        "### Description",
        described['description'],
        "\n".join(sections),
        "\n".join(commands),
    ])


def _all_states_line(tag: str, current: str, states: list[str]) -> str:
    parts = [f"**{state}**" if state == current else f"[{state}]({_stem(tag, state)}.md)"
             for state in states]
    return "**All states:** " + " · ".join(parts)


def _render_state_doc(page_type: PageType, state: str, events: list[str],
                      described: dict[str, Any], mutations: list[dict[str, Any]],
                      states: list[str], qualname: str) -> str:
    tag = page_type.tag
    available = [command for command in mutations if command["available"]]
    transitions = [command for command in available if command["kind"] in _PAGE_TRANSITION_KINDS]
    authoring = [command for command in available if command["kind"] not in _PAGE_TRANSITION_KINDS]
    event_dest = {edge["event"]: edge["dest"]
                  for edge in described["fsm"]["transitions"] if edge["source"] == state}

    # A state's own guidance opens its page; states declaring none keep the placeholder.
    guidance = described["fsm"]["statusGuidance"].get(state)
    intro = (guidance or f"The `{state}` state of the `{tag}` page type.") + "\n\n"

    parts = [
        f"# {tag} - {state}",
        intro,
        _diagram_block(qualname, tag, state, events),
        _all_states_line(tag, state, states),
        "## Transitions\n\n" + _transitions_section(tag, state, transitions, event_dest),
        "## Authoring commands\n\n" + _authoring_section(state, authoring),
        _page_type_section(described),
    ]
    return "\n\n".join(parts) + "\n"


# --- Public API --------------------------------------------------------------
def state_docs(page_type: PageType) -> dict[str, str]:
    """One Markdown doc per reachable state of ``page_type``, keyed ``<tag>-<state>``.

    Captures ``describe_page_type`` once, then ``describe_mutations`` per state (with
    ``ignore_requirements`` so content-gated transitions still enumerate) - the two captures the
    docs are built from.
    """
    described = describe_page_type(page_type)
    qualname = page_machine_qualname(page_type.tag)
    paths = reachable_states(page_type.fsm)
    # Footer/order follows declaration order for readability; all declared states are reachable.
    ordered_states = [state for state in page_type.fsm.states if state in paths]

    docs: dict[str, str] = {}
    for state in ordered_states:
        mutations = describe_mutations(_seed_page(page_type, state), page_type,
                                       ignore_requirements=True)
        docs[_stem(page_type.tag, state)] = _render_state_doc(
            page_type, state, paths[state], described, mutations, ordered_states, qualname)
    return docs


def all_state_docs(registry: dict[str, PageType] | None = None) -> dict[str, str]:
    """Every page-type-state doc across the registry, keyed ``<tag>-<state>``."""
    registry = registered_pagetypes() if registry is None else registry
    docs: dict[str, str] = {}
    for page_type in registry.values():
        docs.update(state_docs(page_type))
    return docs


def render_states_index(registry: dict[str, PageType] | None = None) -> str:
    """The generated ``states.md`` - a toctree over every state doc, so they are reachable."""
    registry = registered_pagetypes() if registry is None else registry
    lines = [
        "# Page-type states",
        "",
        ("A generated reference for every state of every page type: the status FSM navigated to "
         "that state, the transitions out of it, and the authoring commands legal there. "
         "Availability reflects the pure page-type rules (FSM topology plus any status-scoped "
         "command locks); cross-page and store-level guards are not shown. Regenerate with "
         "`scripts/gen_page_type_docs.py`."),
        "",
        "```{toctree}",
        ":titlesonly:",
        "",
    ]
    for page_type in registry.values():
        paths = reachable_states(page_type.fsm)
        for state in page_type.fsm.states:
            if state in paths:
                lines.append(_stem(page_type.tag, state) + ".md")
    lines.append("```")
    return "\n".join(lines) + "\n"

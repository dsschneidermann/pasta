"""The `architecture` page type."""

from __future__ import annotations

from ._stage_guidance import ARCHITECTURE_AUTHORING
from . import (
    FSMSpec,
    PageType,
    SectionSpec,
    _blocks,
    _code_block,
    _list,
    _paragraph_runs,
    _prose,
    _scalar,
    _text,
    add_link_cmd,
    blocks_cmds,
    list_cmds,
    set_prose_cmd,
    set_scalar_cmd,
    set_title_cmd,
    transition_cmd,
)

_NODE_KINDS = ("module", "component", "subsystem", "service", "layer", "package")
_CODE_REF_KINDS = ("file", "function", "class", "type", "interface", "constant")
_DEP_ROLES = ("depends-on", "exposes", "implements", "owns", "calls")


_ARCHITECTURE = PageType(
    tag="architecture",
    name="Architecture node",
    description=(
        "Documents a part of the system that already exists in the codebase: its purpose, "
        "data model, code references, dependencies, and whether it is current or has drifted."
    ),
    sections=(
        SectionSpec("summary", "Summary", (
            _scalar("kind", choices=_NODE_KINDS, description="""
                The granularity of the thing this page documents, one of
                module/component/subsystem/service/layer/package. Pick the narrowest kind that
                honestly fits, and stay consistent with the sibling architecture pages so the tree
                reads at a uniform scale.
                """),
            _prose("body", description="""
                One line, in the present tense, naming what this part of the system IS and the job it
                does. Describe the code as it exists today, not as it is meant to become. Leave the
                why to Purpose and the shapes to Data model.
                """),
        )),
        SectionSpec("purpose", "Purpose", (
            _prose("body", description="""
                Why this part exists: the problem it solves and the role it plays for the rest of the
                system. Say what would break, or become much harder, if it were deleted. Do not
                restate the summary in longer words.
                """)
        ,)),
        SectionSpec("usage", "Usage", (
            _prose("body", description="""
                How callers actually use this part: the entry points they go through, who those
                callers are, and any required call order or lifecycle. Name the real functions,
                endpoints, or commands rather than describing them in the abstract, so a reader can
                grep for them.
                """)
        ,)),
        SectionSpec("dataModel", "Data model", (
            _prose("body", description="""
                The data this part owns: its key types and their fields, which values are persisted
                versus derived, and how long each lives. State who is allowed to mutate the state and
                through what path.
                """)
        ,)),
        SectionSpec("details", "Details", (
            _blocks("body", block_kinds=(_paragraph_runs(), _code_block()), description="""
                Design notes that do not fit the fixed sections: rationale, trade-offs that were
                considered and rejected, gotchas, and worked examples. Use a code block for anything a
                reader would otherwise have to reconstruct from prose. Emphasis and links are
                structured inline runs, not markdown syntax.
                """),
        )),
        SectionSpec("codeReferences", "Code references", (
            _list("items", element_fields=("file", "symbol", "kind"), description="""
                Each a pointer into real source: the repo-relative file path, the symbol when the
                reference is narrower than the whole file, and that symbol's kind
                (file/function/class/type/interface/constant). Add one per place a reader must open to
                understand or change this node. Confirm the path and symbol exist before recording
                them; a stale pointer is worse than none.
                """),
        )),
        SectionSpec("dependencies", "Dependencies", (
            _list("items", element_fields=("target", "role", "note"), description="""
                Each an edge from this node to another page: the target, the role this node plays
                toward it (depends-on/exposes/implements/owns/calls), and a note saying what actually
                crosses the boundary. Record the direction this node experiences, not the reverse, and
                keep one edge per element.
                """),
        )),
        SectionSpec("invariants", "Invariants", (
            _list("items", element_fields=("statement",), description="""
                Each one property that must always hold for this node, written as a checkable
                assertion about state or behaviour rather than an aspiration, and paired with what
                breaks when it is violated. One invariant per element.
                """),
        )),
        SectionSpec("sync", "Sync", (
            _scalar("commit", description="""
                The commit sha this page was last reconciled against. Record the sha you actually read
                the code at, so a later reader can diff from it to find exactly what has drifted.
                """),
        )),
    ),
    commands=(
        set_scalar_cmd("summary", "kind", choices=_NODE_KINDS),
        set_prose_cmd("summary"),
        set_prose_cmd("purpose"),
        set_prose_cmd("usage"),
        set_prose_cmd("dataModel", label="data model"),
        # `details` is a `blocks` field with a deliberately narrow surface: a prose note (inline
        # runs) or a code note, plus the universal remove/reorder.
        *blocks_cmds(
            "details",
            remove_name="removeNote", remove_desc="remove a note block",
            reorder_name="reorderNote",
            reorder_desc="move a note block to an anchored position (precedingId guards a stale read)"),
        *list_cmds("codeReferences", label="code reference",
                   add_args=(_text("file"), _text("symbol", required=False),
                             _text("kind", required=False, choices=_CODE_REF_KINDS))),
        *list_cmds("dependencies",
                   add_args=(_text("target"), _text("role", choices=_DEP_ROLES),
                             _text("note", required=False))),
        *list_cmds("invariants", add_args=(_text("statement"),)),
        set_scalar_cmd("sync", "commit", name="recordSync", label="sync commit"),
        transition_cmd("markCurrent", "authoring -> current"),
        transition_cmd("author", "current -> authoring"),
        add_link_cmd(),
        set_title_cmd(),
    ),
    fsm=FSMSpec(
        name="Architecture",
        initial="authoring",
        states=("authoring", "current"),
        terminal_states=("current",),
        state_guidance=(("authoring", ARCHITECTURE_AUTHORING),),
    ),
)

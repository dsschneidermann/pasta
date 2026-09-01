"""The `document` page type."""

from __future__ import annotations

from .core.specs import FSMSpec
from .core.args import standard_blocks
from .core.commands import add_link_cmd, set_title_cmd, blocks_cmds
from .core.fields import SectionSpec, _blocks
from .core.pagetype import PageType

_DOCUMENT = PageType(
    tag="document",
    name="Document",
    description=(
        "A general-purpose prose page for content that doesn't fit a typed page - notes, guides, "
        "references, narratives. The richest block-editing surface in the wiki."
    ),
    sections=(
        SectionSpec("body", "Body", (
            _blocks("body", block_kinds=standard_blocks(), description="""
                The document body, built from structured blocks: headings so a reader can navigate,
                paragraphs for prose, code blocks for anything with a precise shape, and tables for
                anything genuinely tabular. Lead with what the reader needs first. Emphasis and links
                are structured inline runs, not markdown syntax.
                """),
        )),
    ),
    commands=(*blocks_cmds("body"), add_link_cmd(), set_title_cmd()),
    fsm=FSMSpec(
        name="Document",
        initial="active",
        states=("active",),
    ),
)

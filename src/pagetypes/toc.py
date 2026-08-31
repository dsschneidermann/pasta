"""The `toc` page type."""

from __future__ import annotations

from .core.specs import FSMSpec
from .core.pagetype import PageType

_TOC = PageType(
    tag="toc",
    name="Table of contents",
    description=(
        "A container / landing node whose only content is the child pages placed under it. It holds "
        "no subject matter of its own - pages are filed by reparenting them beneath the toc (normal "
        "reparent rules apply), and the rendered Child pages list IS the table of contents. There are "
        "no authoring commands: a toc is shaped entirely by what lives under it, not by editing it."
    ),
    # No content sections and no authoring commands - a toc carries nothing of its own. Its single
    # purpose is to be a parent you place child pages under, so the whole page IS its Child pages list.
    # It is the one page type WITHOUT the universal addLink command: it cannot be authored at all.
    sections=(),
    commands=(),
    fsm=FSMSpec(
        name="Toc",
        initial="active",
        states=("active",),
    ),
)

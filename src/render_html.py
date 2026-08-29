"""Structured HTML rendering of one page for the web view. Pure: model objects and page types
in, an HTML string out.

A second render path, deliberately: the Markdown renderer is a returned contract and stays as it
is. This one exists because a list element carrying several fields reads badly as one flattened
bullet - here each element gets its own titled block with labelled rows.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from wenmode import Wenmode
from wenmode.presets import github

from .model import Page
from .pagetypes import BLOCKS, LIST, PROSE, SCALAR, FieldSpec, PageType, SectionSpec
from .render import RefContext, checkbox_state, render_blocks

# The one Markdown-to-HTML engine, defined here because this is where HTML is produced.
md2html = Wenmode(github)


# Shown for an empty field, so a page's shape is visible before it is filled.
_NONE_HTML = '<p class="empty">None.</p>'

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def _escape(value: Any) -> str:
    """An author-provided leaf, safe to place directly into the emitted HTML."""
    return html.escape(str(value), quote=True)


def _text_html(text: str) -> str:
    """Author plain text as escaped paragraphs: blank lines separate paragraphs, and a single
    newline inside one becomes a space, the way a Markdown reflow reads it today."""
    paragraphs: list[str] = []
    for block in _PARAGRAPH_BREAK.split(text.strip()):
        joined = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if joined:
            paragraphs.append(f"<p>{_escape(joined)}</p>")
    return "".join(paragraphs)


def _url_link(value: str) -> str:
    """A field named `url` as a clickable link that opens in a new tab. The value is escaped for
    both the href and the visible text, so an author-provided leaf stays safe to place directly in
    the emitted HTML; the new tab carries no opener reference back to this page."""
    escaped = _escape(value)
    return f'<a href="{escaped}" target="_blank" rel="noopener noreferrer">{escaped}</a>'


@dataclass(frozen=True)
class ElementView:
    """One list element decomposed for display, before any HTML exists.

    A row value of None means the field is declared but empty.
    """
    element_id: str
    index: int
    title: str | None
    check: str | None
    status: str | None
    rows: tuple[tuple[str, str | None], ...]
    # declared block fields, in declared order, rendered after the plain rows
    block_rows: tuple[tuple[str, tuple[dict[str, Any], ...]], ...] = ()


def element_view(element: dict[str, Any], index: int, field_spec: FieldSpec) -> ElementView:
    """Decompose one stored element for display. The single home of the title, row and checkbox
    rules: an element is headed by the field its type declares as the heading (`title` or `name`)
    and by no other, so every element of one field renders with the same shape whatever its
    values are - a list whose type declares neither is headed by its ordinal alone."""
    declared = field_spec.element_fields or ()
    # A block-bearing field is neither a heading nor a plain row - it has its own tuple.
    block_fields = field_spec.block_element_fields()
    # The declared heading field, consumed by the heading and never repeated as a row - so a
    # field's row set is fixed by its declaration and does not move with an author's edits.
    title_key = field_spec.title_element_field()
    # Declared order first, then any stored key the type does not declare, so nothing on the
    # element is hidden. `id` is structural and `status` has its own chip.
    extra = [key for key in element if key not in ("id", "status") and key not in declared]
    keys = [key for key in declared
            if key not in ("status", title_key) and key not in block_fields] + extra

    raw_status = element.get("status")
    status = raw_status if isinstance(raw_status, str) else None

    # An empty heading value leaves the element with its ordinal alone; the field stays the
    # heading either way, so it never reappears as a row.
    head = element.get(title_key) if title_key is not None else None
    title = (str(head).strip() or None) if head is not None else None

    def row_value(key: str) -> str | None:
        value = element.get(key)
        if value is None:
            return None
        text = str(value)
        return text if text.strip() else None

    return ElementView(
        element_id=str(element.get("id") or ""),
        index=index,
        title=title,
        check=checkbox_state(status, field_spec.element_fsm),
        status=status,
        rows=tuple((key, row_value(key)) for key in keys),
        block_rows=tuple((key, tuple(element.get(key) or ()))
                         for key in declared if key in block_fields),
    )


# The checkbox glyphs: a ballot box with a check, and an empty ballot box.
_CHECK_GLYPH = {"done": "&#9745;", "todo": "&#9744;"}


def _element_html(view: ElementView, ref_context: RefContext | None) -> str:
    """One decomposed element: an ordinal, an optional checkbox and title, an optional status
    chip, and a labelled row per remaining field. The ordinal links to the element's own anchor,
    so one item of a long list can be pointed at, and any value naming a page becomes a link to
    it rather than a bare id."""
    anchor = f' id="element-{_escape(view.element_id)}"' if view.element_id else ""
    if view.element_id:
        index = (f'<a class="element-index" href="#element-{_escape(view.element_id)}">'
                 f"{view.index}</a>")
    else:
        index = f'<span class="element-index">{view.index}</span>'
    head = [index]
    if view.check is not None:
        head.append(f'<span class="element-check" data-check="{view.check}">' +
                    f'{_CHECK_GLYPH[view.check]}</span>')
    if view.title is not None:
        titled = _page_link(view.title, ref_context)
        head.append(f'<h3 class="element-title">{titled or _escape(view.title)}</h3>')
    if view.status is not None:
        head.append(f'<span class="element-status">{_escape(view.status)}</span>')
    cells: list[str] = []
    for label, value in view.rows:
        if value is None:
            cell = '<dd class="empty">&mdash;</dd>'
        else:
            linked = _page_link(value, ref_context) or (_url_link(value) if label == "url" else None)
            cell = f"<dd><p>{linked}</p></dd>" if linked else f"<dd>{_text_html(value)}</dd>"
        cells.append(f"<dt>{_escape(label)}</dt>" + cell)
    for label, blocks in view.block_rows:
        # Rich content takes the same Markdown route a page-level blocks field takes, so a code
        # block, a nested list and an inline page ref all read the same wherever they are written.
        markdown = render_blocks(list(blocks), ref_context) if blocks else None
        cell = (f'<dd class="element-blocks">{md2html.render(markdown)}</dd>' if markdown
                else '<dd class="empty">&mdash;</dd>')
        cells.append(f"<dt>{_escape(label)}</dt>" + cell)
    rows = "".join(cells)
    body = f'<dl class="field-rows">{rows}</dl>' if rows else ""
    return (f'<li class="element"{anchor}>'
            f'<div class="element-head">{"".join(head)}</div>{body}</li>')


def _list_html(field_spec: FieldSpec, elements: list[dict[str, Any]],
               ref_context: RefContext | None) -> str:
    """A list field as an ordered stack of element blocks."""
    items = "".join(_element_html(element_view(element, index, field_spec), ref_context)
                    for index, element in enumerate(elements, start=1))
    return f'<ol class="element-list">{items}</ol>'


def _field_html(field_spec: FieldSpec, value: Any, ref_context: RefContext | None) -> str:
    """One field as HTML, or the empty fallback when it holds no content. A scalar keeps its
    label either way, so the field stays named even when unset."""
    if field_spec.kind == SCALAR:
        if value in (None, ""):
            shown = '<dd class="empty">None.</dd>'
        elif field_spec.key == "url":
            shown = f"<dd>{_url_link(str(value))}</dd>"
        else:
            shown = f"<dd>{_escape(value)}</dd>"
        return f'<dl class="field-rows"><dt>{_escape(field_spec.key)}</dt>{shown}</dl>'
    if field_spec.kind == PROSE:
        return _text_html(str(value)) if value else _NONE_HTML
    if field_spec.kind == LIST:
        return _list_html(field_spec, value, ref_context) if value else _NONE_HTML
    if field_spec.kind == BLOCKS:
        # Rich content - headings, code, tables, quotes, inline page refs - keeps the Markdown
        # pipeline, applied to a fragment for this one field rather than the whole document.
        markdown = render_blocks(value, ref_context) if value else None
        return md2html.render(markdown) if markdown else _NONE_HTML
    return _NONE_HTML


def _page_link(page_id: str, ref_context: RefContext | None) -> str | None:
    """The titled `type · status` link for a page id, or None when it resolves to no page.

    A page id reaches the reader in more places than a child or a reference edge: a list element
    field can hold one too, and there it is stored as ordinary text. Resolving on the value rather
    than on a declared field kind means any such field reads as its target rather than as an id.

    An archived target is marked before the link, so wherever a page is named the reader can see
    that it has been archived without following it.
    """
    if ref_context is None or page_id not in ref_context.titles:
        return None
    flag = ('<strong class="archived-flag">(A)</strong> '
            if page_id in ref_context.archived_ids else "")
    query = "?archived=true" if ref_context.show_archived else ""
    return (f"{flag}" +
            f'<a href="/{_escape(ref_context.workspace_id)}/page/{_escape(page_id)}{query}">' +
            f"{_escape(ref_context.titles[page_id])}</a>" +
            f' <span class="link-type">{_escape(ref_context.types[page_id])}&nbsp;·</span>' +
            f' <span class="link-status">{_escape(ref_context.statuses[page_id])}</span>')


def _link_html(page_id: str, ref_context: RefContext | None,
               role: str | None = None) -> str:
    """One link row: the target page as a titled link, with an optional edge role after it.
    Without a resolvable context the id stands alone."""
    suffix = f' <span class="link-role">{_escape(role)}</span>' if role is not None else ""
    return f"<li>{_page_link(page_id, ref_context) or _escape(page_id)}{suffix}</li>"


def _children_html(page: Page, ref_context: RefContext | None) -> str:
    """The page's direct children as links. Archived children sink below active ones and are
    hidden unless the context asks for them."""
    def is_archived(child_id: str) -> bool:
        return ref_context is not None and child_id in ref_context.archived_ids

    rows: list[str] = []
    for child_id in sorted(page.child_ids, key=is_archived):
        archived = is_archived(child_id)
        if archived and not page.archived and ref_context and not ref_context.show_archived:
            continue
        rows.append(_link_html(child_id, ref_context))
    return f'<ul class="link-list">{"".join(rows)}</ul>' if rows else _NONE_HTML


def _references_html(page: Page, ref_context: RefContext | None) -> str:
    """The page's outgoing reference edges, each the target link plus its role."""
    rows: list[str] = []
    for link in page.links:
        to_id, role = link["to"], link["role"]
        archived = ref_context is not None and to_id in ref_context.archived_ids
        if archived and ref_context and not ref_context.show_archived:
            continue
        rows.append(_link_html(to_id, ref_context, role))
    return f'<ul class="link-list">{"".join(rows)}</ul>' if rows else _NONE_HTML


def _section_count(section: SectionSpec, page: Page) -> int:
    """How many list elements the section holds, across all its list fields."""
    total = 0
    for field_spec in section.fields:
        if field_spec.kind == LIST:
            value = page.sections.get(section.key, {}).get(field_spec.key)
            total += len(value) if value else 0
    return total


def _contents_html(page_type: PageType, page: Page) -> str:
    """A strip of links to every section, each carrying its element count when it has one, so a
    page of long lists can be navigated without scrolling it."""
    links: list[str] = []
    for section in page_type.sections:
        count = _section_count(section, page)
        badge = f'<span class="count">{count}</span>' if count else ""
        links.append(f'<a href="#section-{_escape(section.key)}">' +
                     f"{_escape(section.name)}{badge}</a>")
    links.append('<a href="#section-references">References</a>')
    links.append('<a href="#section-children">Child pages</a>')
    return f'<nav class="page-contents" aria-label="Sections">{"".join(links)}</nav>'


def _section_html(section: SectionSpec, page: Page, ref_context: RefContext | None) -> str:
    count = _section_count(section, page)
    badge = f' <span class="count">{count}</span>' if count else ""
    fields = "".join(
        f'<div class="field field-{field_spec.kind}">' +
        f"{_field_html(field_spec, page.sections.get(section.key, {}).get(field_spec.key), ref_context)}" +
        "</div>"
        for field_spec in section.fields
    )
    return (f'<section class="page-section" id="section-{_escape(section.key)}">'
            f'<h2 class="section-title">{_escape(section.name)}{badge}</h2>{fields}</section>')


def render_page_html(page: Page, page_type: PageType,
                     ref_context: RefContext | None = None) -> str:
    """One page as structured HTML: a header, a contents strip, every declared section and field,
    then `References` and `Child pages` - so a page's shape is visible before it is filled.

    A toc carries no subject matter and can hold no outgoing link, so it renders as its header and
    its child list alone: that list IS the table of contents.
    """
    revision = ""
    if page.status_revision_token is not None:
        revision = (f'<span class="page-meta-sep">&nbsp;·&nbsp;rev&nbsp;</span>'
                    f'<span class="page-revision">{_escape(page.status_revision_token)}</span>')
    head = (f'<header class="page-head"><h1 class="page-title">{_escape(page.title)}</h1>'
            f'<p class="page-meta"><span class="page-type">{_escape(page.type)}&nbsp;·&nbsp;</span>'
            f'<span class="page-status">{_escape(page.status)}</span>{revision}</p></header>')
    if page_type.tag == "toc":
        return f'<article class="pasta-page">{head}{_children_html(page, ref_context)}</article>'
    sections = "".join(_section_html(section, page, ref_context)
                       for section in page_type.sections)
    tail = ('<section class="page-section" id="section-references">'
            '<h2 class="section-title">References</h2>'
            f"{_references_html(page, ref_context)}</section>"
            '<section class="page-section" id="section-children">'
            '<h2 class="section-title">Child pages</h2>'
            f"{_children_html(page, ref_context)}</section>")
    return (f'<article class="pasta-page">{head}'
            f"{_contents_html(page_type, page)}{sections}{tail}</article>")

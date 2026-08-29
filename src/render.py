"""Pure rendering: a `Page` (or a whole workspace tree) to Markdown, plus a flat text
projection used by search. No I/O - takes model objects + page types in, returns strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .model import Page, Workspace
from .pagetypes import BLOCKS, LIST, PROSE, SCALAR, ElementFSMSpec, FieldSpec, PageType, get_page_type


# --- plain-text escaping (web render only) -----------------------------------
# The web view renders a page to Markdown and then runs the whole string through one
# top-level Markdown->HTML pass, which cannot tell the renderer's deliberate structural
# Markdown (headings, list markers, checkboxes, tables, links) from plain-text field
# values that happen to contain Markdown-special characters. So on the web path every
# author-provided text leaf is escaped as it is emitted, before it reaches that pass;
# the MCP render path never escapes (its Markdown is returned as-is).

# Markdown-special anywhere in a line. Backslash MUST be first, so the backslashes the
# rules below add are not themselves re-escaped.
_GLOBAL_ESCAPES = (
    ("\\", "\\\\"),
    ("*", "\\*"),
    ("`", "\\`"),
    ("_", "\\_"),
    ("[", "\\["),
    ("]", "\\]"),
)

# A line opening a block construct - unordered list ("-" or "+ "), ATX heading, tilde
# code fence, blockquote, or a setext "=" underline - gets a leading backslash so it
# renders as text. (Backtick fences and */_ thematic breaks are already covered by the
# global replacements above.)
_BLOCK_LINE = re.compile(r"^(?=-|\+ |#{1,6} |~~~|>|=+[ \t]*$)", re.MULTILINE)
# Ordered-list line "N. ": escape the dot, not the digit - a backslash before a digit
# renders as a visible backslash.
_ORDERED_LINE = re.compile(r"^(\d+)\. ", re.MULTILINE)


def escape_markdown(text: str) -> str:
    """Escape a plain-text value so a whole-document Markdown->HTML pass renders it
    verbatim. Applied by the renderer at each plain-text leaf on the WEB path only;
    the MCP render path returns Markdown unescaped."""
    if not text:
        return text
    for needle, replacement in _GLOBAL_ESCAPES:
        text = text.replace(needle, replacement)
    text = _BLOCK_LINE.sub("\\\\", text)
    text = _ORDERED_LINE.sub(r"\1\\. ", text)
    return text


@dataclass(frozen=True)
class RefContext:
    """The workspace view the renderer needs to turn page ids into titled, annotated links - for both
    inline `{ref: pageId}` runs and a page's Child-pages list.

    `titles`, `types`, and `statuses` map every page id in the workspace (archived pages included) to
    its title, page type, and status: a ref's or a child's label is its target's title, and a child
    link is annotated with its `type · status`. `archived_ids` is the subset of those ids that are
    archived, so the child list can hide (or flag) them the way the tree render does. The link points
    at that page's web route, carrying `show_archived` (as `?archived=true`) so following it keeps the
    archived view. It is built by the store-driven render path; a direct render with no context falls
    back to bare ids.

    `escape_plain_text` is the render-mode flag (like `show_archived`): when set, the renderer
    markdown-escapes every plain-text leaf it emits - the web path sets it; the MCP path leaves it off.
    """
    workspace_id: str
    titles: dict[str, str]
    types: dict[str, str]
    statuses: dict[str, str]
    show_archived: bool = False
    archived_ids: frozenset[str] = frozenset()
    escape_plain_text: bool = False


def _plain(text: str, ref_context: RefContext | None) -> str:
    """A plain-text leaf, markdown-escaped for the web render (`ref_context.escape_plain_text`).
    On the MCP path - no context, or the flag off - the value passes through unchanged."""
    if ref_context is not None and ref_context.escape_plain_text:
        return escape_markdown(text)
    return text


def checkbox_state(status: str | None, element_fsm: ElementFSMSpec | None) -> str | None:
    """An element's checkbox state, taken from its FSM: `"done"` for the checkmark_done state,
    `"todo"` for the FSM's initial (unchecked) state, and None for any other state or a
    non-checkbox FSM (one with no checkmark_done, or a list field with no element FSM at all).

    Public so every renderer answers this question the same way and then spells it its own.
    """
    if element_fsm is None or element_fsm.checkmark_done is None:
        return None
    if status == element_fsm.checkmark_done:
        return "done"
    if status == element_fsm.initial:
        return "todo"
    return None


def _checkbox(status: str | None, element_fsm: ElementFSMSpec | None) -> str:
    """The task checkbox for an element's status: `[x] ` for a done element, `[ ] ` for an
    unchecked one, and `` where the element carries no checkbox at all."""
    state = checkbox_state(status, element_fsm)
    return "[x] " if state == "done" else "[ ] " if state == "todo" else ""


def _indent_list_content(markdown: str) -> str:
    """Block Markdown indented to sit inside its list item - without it a fenced code block or a
    nested list would close the item. A blank line stays blank: the repo trims trailing whitespace.
    """
    return "\n".join(f"  {line}" if line else "" for line in markdown.splitlines())


def _render_list(elements: list[dict[str, Any]], field_spec: FieldSpec | None = None,
                 ref_context: RefContext | None = None) -> str | None:
    element_fsm = field_spec.element_fsm if field_spec is not None else None
    block_fields = field_spec.block_element_fields() if field_spec is not None else ()
    lines: list[str] = []
    for element in elements:
        # A block-bearing field is rendered below the bullet, never flattened into it.
        fields = {key: value for key, value in element.items()
                  if key != "id" and value is not None and key not in block_fields}
        status = fields.pop("status", None)
        text = _plain(str(fields.pop("text", "")), ref_context)
        body = "; ".join(f"{key}: {_plain(str(value), ref_context)}" for key, value in fields.items())
        bodysep = "; " if text and body else ""
        mark = _checkbox(status, element_fsm)
        lines.append(f"- {mark}{text}{bodysep}{body}" + (f" _[{status}]_" if status else ""))
        for block_field in block_fields:
            rendered = render_blocks(element.get(block_field) or [], ref_context)
            if rendered:
                lines += ["", _indent_list_content(rendered)]
    return "\n".join(lines) if lines else None


_ALIGN_SEP = {"left": ":---", "center": ":---:", "right": "---:", None: "---"}


def _render_run(run: dict[str, Any] | str, ref_context: RefContext | None = None) -> str:
    """One inline run to Markdown. Bounded to the run grammar; unknown shapes render as text."""
    if isinstance(run, str):
        return _plain(run, ref_context)
    if "code" in run:
        return f"`{run['code']}`"
    if "ref" in run:
        # A ref renders as its target page's title, linked to that page's web route. With no
        # workspace context, or an id that resolves to no page, we cannot build a correct
        # /<workspaceId>/page/<id> link - fall back to the bare id as plain text.
        ref = run["ref"]
        if ref_context is None or ref not in ref_context.titles:
            return ref
        query = "?archived=true" if ref_context.show_archived else ""
        return f"[{_plain(ref_context.titles[ref], ref_context)}](/{ref_context.workspace_id}/page/{ref}{query})"
    text = _plain(str(run.get("text", "")), ref_context)
    if run.get("italic"):
        text = f"*{text}*"
    if run.get("bold"):
        text = f"**{text}**"
    if run.get("href"):
        text = f"[{text}]({run['href']})"
    return text


def _render_inlines(runs: list[Any], ref_context: RefContext | None = None) -> str:
    """An inline-run array to a single Markdown string."""
    return "".join(_render_run(run, ref_context) for run in runs)


def _inline_or_text(block: dict[str, Any], ref_context: RefContext | None = None) -> str:
    """A block's text: the rich inline runs if present, else the bounded plain-text `text`."""
    if "inlines" in block:
        return _render_inlines(block["inlines"], ref_context)
    return _plain(str(block.get("text", "")), ref_context)


def _render_table(block: dict[str, Any], ref_context: RefContext | None = None) -> str:
    header = block.get("header", [])
    align = block.get("align") or [None] * len(header)
    rows = block.get("rows", [])

    def row_line(cells: list[Any]) -> str:
        return "| " + " | ".join(_render_inlines(cell, ref_context) for cell in cells) + " |"

    lines = [row_line(header), "| " + " | ".join(_ALIGN_SEP.get(a, "---") for a in align) + " |"]
    lines += [row_line(row) for row in rows]
    return "\n".join(lines)


def render_blocks(blocks: list[dict[str, Any]], ref_context: RefContext | None = None) -> str | None:
    out: list[str] = []
    for block in blocks:
        kind = block.get("kind")
        if kind == "heading":
            level = min(max(int(block.get("level", 1)), 1), 6)
            out.append(f"{'#' * level} {_inline_or_text(block, ref_context)}")
        elif kind == "paragraph":
            out.append(_inline_or_text(block, ref_context))
        elif kind == "code":
            out.append(f"```{block.get('language', '')}\n{block.get('source', '')}\n```")
        elif kind == "list":
            marker = (lambda i: f"{i}.") if block.get("ordered") else (lambda i: "-")
            out.append("\n".join(f"{marker(i)} {_render_inlines(item, ref_context)}"
                                 for i, item in enumerate(block.get("items", []), start=1)))
        elif kind == "quote":
            out.append("\n>\n".join(f"> {_render_inlines(p, ref_context)}"
                                    for p in block.get("paragraphs", [])))
        elif kind == "table":
            out.append(_render_table(block, ref_context))
        elif kind == "divider":
            out.append("---")
        elif kind == "decision":
            out.append(f"- **decision** (question `{block.get('questionId', '')}`): {block.get('text', '')}")
        else:
            out.append(str(block))
    return "\n\n".join(out) if out else None


# Shown for an empty field (and a childless page) so a page's shape - its sections and fields -
# is visible before it is filled. Rendered italic in the final Markdown.
_NONE = "*None.*"


def _field_content(field_spec: FieldSpec, value, ref_context: RefContext | None = None) -> str:
    """One field's Markdown, or the italic `*None.*` fallback when it holds no content.

    Scalars keep their `**key:**` label so the field stays named even when empty; prose, list, and
    blocks fields render bare (named by their section heading), matching how a filled field renders.
    """
    if field_spec.kind == SCALAR:
        shown = _plain(str(value), ref_context) if value not in (None, "") else _NONE
        return f"- **{field_spec.key}:** {shown}"
    if field_spec.kind == PROSE:
        return _plain(str(value), ref_context) if value else _NONE
    if field_spec.kind == LIST:
        rendered = _render_list(value, field_spec, ref_context) if value else None
        return rendered if rendered else _NONE
    if field_spec.kind == BLOCKS:
        rendered = render_blocks(value, ref_context) if value else None
        return rendered if rendered else _NONE
    return _NONE


def _render_child_pages(page: Page, ref_context: RefContext | None = None) -> str:
    """A bullet list linking to this page's DIRECT children (each child lists its own children).

    Archived children are hidden unless `ref_context.show_archived`, then listed with a bold `**(A)**`
    marker prefixed BEFORE the link - mirroring how the tree (workspace-link) render flags archiving.
    Links carry `show_archived` (as `?archived=true`) so a click keeps the archived view. With no
    `ref_context` (a direct render with no workspace) a child is listed as its bare id; an empty list
    renders the `*None.*` fallback.
    """
    # Stable partition: archived children sink below non-archived ones, each group's order preserved
    # (`sorted` is stable, `False < True`) - mirrors how Store.tree orders every level.
    def _is_archived(child_id: str) -> bool:
        return ref_context is not None and child_id in ref_context.archived_ids

    lines: list[str] = []
    for child_id in sorted(page.child_ids, key=_is_archived):
        archived = _is_archived(child_id)
        if archived and not page.archived and ref_context and not ref_context.show_archived:
            continue
        prefix = "**(A)** " if archived else ""
        if ref_context is None or child_id not in ref_context.titles:
            lines.append(f"- {prefix}{child_id}")
        else:
            query = "?archived=true" if ref_context.show_archived else ""
            lines.append(
                f"- {prefix}[{_plain(ref_context.titles[child_id], ref_context)}]" +
                f"(/{ref_context.workspace_id}/page/{child_id}{query}) *{ref_context.types[child_id]}* · `{ref_context.statuses[child_id]}`"
            )
    return "\n".join(lines) if lines else _NONE


def _render_references(page: Page, ref_context: RefContext | None = None) -> str:
    """A bullet list of the page's OUTGOING reference links (page.links): each the target page as a
    titled `type · status` link (the same form as a child link) followed by the edge `role`.

    Mirrors `_render_child_pages` archiving: an archived target is hidden unless
    `ref_context.show_archived`, then listed with a bold `**(A)**` marker prefixed BEFORE the link,
    with `?archived=true` on the link. With no `ref_context` the target is its bare id; an empty list
    renders the `*None.*` fallback.
    """
    lines: list[str] = []
    for link in page.links:
        to_id, role = link["to"], link["role"]
        archived = ref_context is not None and to_id in ref_context.archived_ids
        if archived and ref_context and not ref_context.show_archived:
            continue
        prefix = "**(A)** " if archived else ""
        if ref_context is None or to_id not in ref_context.titles:
            lines.append(f"- {prefix}{to_id} - {_plain(role, ref_context)}")
        else:
            query = "?archived=true" if ref_context.show_archived else ""
            lines.append(
                f"- {prefix}[{_plain(ref_context.titles[to_id], ref_context)}]" +
                f"(/{ref_context.workspace_id}/page/{to_id}{query})" +
                f" *{ref_context.types[to_id]}* · `{ref_context.statuses[to_id]}` - {_plain(role, ref_context)}"
            )
    return "\n".join(lines) if lines else _NONE


def render_page(page: Page, page_type: PageType, level: int = 1,
                ref_context: RefContext | None = None) -> str:
    """A page as Markdown: a title heading, then EVERY declared section and field, then
    `References` and `Child pages` lists. An empty field (and an empty list) renders the italic
    `*None.*` fallback, so a page's shape is visible before it is filled.

    A `toc` is the exception: it carries no subject matter and - as the one type with no authoring
    commands (hence no `addLink`) - can never hold an outgoing link, so its References list is always
    empty. A toc therefore renders as just its title, the `type · status` meta line, and the bare
    child list (no `References` / `Child pages` headings): that child list IS the table of contents.

    `ref_context` resolves inline `{ref}` runs and child-page links to titled links; omit it (a direct
    render with no workspace) and refs / children fall back to their bare ids.
    """
    meta = f"*{page.type}* · `{page.status}`"
    if page.status_revision_token is not None:
        meta += f" · rev `{page.status_revision_token}`"
    lines = [f"{'#' * level} {_plain(page.title, ref_context)}", "", meta, ""]
    section_heading = "#" * (level + 1)
    for section in page_type.sections:
        chunks = [_field_content(field_spec, page.sections.get(section.key, {}).get(field_spec.key), ref_context)
                  for field_spec in section.fields]
        lines += [f"{section_heading} {section.name}", "", "\n\n".join(chunks), ""]
    if page_type.tag == "toc":
        # A toc's References list is always empty (no addLink) and its whole purpose is the child list,
        # so emit only that list - unheadered - as the table of contents.
        lines += [_render_child_pages(page, ref_context), ""]
    else:
        lines += [f"{section_heading} References", "", _render_references(page, ref_context), ""]
        lines += [f"{section_heading} Child pages", "", _render_child_pages(page, ref_context), ""]
    return "\n".join(lines).rstrip() + "\n"


def build_ref_context(workspace: Workspace, show_archived: bool = False,
                      escape_plain_text: bool = False) -> RefContext:
    """A RefContext over `workspace`: a pageId->title map (archived pages included) plus the set of
    archived ids, for resolving inline refs and child-page links to titled links. `escape_plain_text`
    is the web render-mode flag (off for the MCP path)."""
    return RefContext(
        workspace_id=workspace.id,
        titles={page_id: page.title for page_id, page in workspace.pages.items()},
        types={page_id: page.type for page_id, page in workspace.pages.items()},
        statuses={page_id: page.status for page_id, page in workspace.pages.items()},
        show_archived=show_archived,
        archived_ids=frozenset(page_id for page_id, page in workspace.pages.items() if page.archived),
        escape_plain_text=escape_plain_text,
    )


def render_tree(workspace: Workspace, show_archived: bool = False) -> str:
    """The whole (non-archived) tree as one Markdown document, nested by depth.

    Which pages render is unchanged (archived subtrees are still skipped); `show_archived` only
    flows onto the inline-ref links (as `?archived=true`) so following a ref keeps the archived view.
    """
    out = [f"# {workspace.name}", ""]
    ref_context = build_ref_context(workspace, show_archived)

    def walk(page_id: str, depth: int) -> None:
        page = workspace.pages[page_id]
        if page.archived:
            return
        page_type = get_page_type(page.type)
        if page_type is not None:
            out.extend([render_page(page, page_type, level=min(depth, 6), ref_context=ref_context), ""])
        for child_id in page.child_ids:
            walk(child_id, depth + 1)

    for root_id in workspace.root_page_ids:
        walk(root_id, 2)
    return "\n".join(out).rstrip() + "\n"


# Page types whose workspace status is worth a glance in the nav tree - the pages a user
# tracks through a lifecycle, as opposed to structural/reference pages (toc, architecture, ...).
_STATUS_SUFFIX_TYPES = frozenset({"feature-brief", "simple-change", "bug-report"})


def render_workspace_links(tree: dict[str, Any], show_archived: bool = False, show_meta: bool = False,
                           escape_plain_text: bool = False) -> str:
    """A `store.tree()` result as a nested Markdown list - every page a link to /page/<id>.

    Lists all pages (each node and its descendants); an archived node, if present, is flagged with a
    bold `**(A)**` marker prefixed BEFORE its link.
    The link target is the page's full type-prefixed id (the /page/<id> route is served elsewhere).
    `show_archived=True` lists links with `?archived=true` query parameter.
    `show_meta=True` lists the page-title links, including each page's `type · status`.
    `escape_plain_text=True` (the web path) markdown-escapes the plain-text page-title labels.
    When `show_meta=False`, pages of a type in `_STATUS_SUFFIX_TYPES` get their status appended
    to the link text in parentheses, e.g. "Change (done)".
    """
    query = "?archived=true" if show_archived else ""

    def walk(pages: list[dict[str, Any]], depth: int) -> list[str]:
        lines: list[str] = []
        for page in pages:
            indent = "  " * depth
            title = escape_markdown(page['title']) if escape_plain_text else page['title']
            meta = f" *{page['type']}* · `{page['status']}`" if show_meta else ""
            if not show_meta and page['type'] in _STATUS_SUFFIX_TYPES:
                title = f"{title} ({page['status']})"
            prefix = "**(A)** " if page.get("archived") else ""
            lines.append(f"{indent}- {prefix}[{title}](/{str(tree.get("workspaceId"))}/page/{page['id']}{query}){meta}")
            lines.extend(walk(page.get("children", []), depth + 1))
        return lines

    return "\n".join(walk(tree.get("nodes", []), 0))


def _runs_text(runs: list[Any]) -> str:
    """The plain text carried by an inline-run array (text/code runs; refs are ids, skipped)."""
    words: list[str] = []
    for run in runs:
        if isinstance(run, str):
            words.append(run)
        elif isinstance(run, dict):
            if "text" in run:
                words.append(str(run["text"]))
            elif "code" in run:
                words.append(str(run["code"]))
    return " ".join(words)


def _block_inline_text(block: dict[str, Any]) -> str:
    """The text held in a block's inline runs (its bounded `text`/`source` are captured elsewhere)."""
    segments: list[str] = []
    if "inlines" in block:
        segments.append(_runs_text(block["inlines"]))
    for item in block.get("items", []) or []:
        segments.append(_runs_text(item))
    for paragraph in block.get("paragraphs", []) or []:
        segments.append(_runs_text(paragraph))
    for cell in block.get("header", []) or []:
        segments.append(_runs_text(cell))
    for row in block.get("rows", []) or []:
        for cell in row:
            segments.append(_runs_text(cell))
    return " ".join(segment for segment in segments if segment)


def page_text(page: Page, page_type: PageType) -> str:
    """A flat text projection of a page's content (title + every string value), for search."""
    parts = [page.title]
    for section in page_type.sections:
        for field_spec in section.fields:
            value = page.sections.get(section.key, {}).get(field_spec.key)
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, list):
                for entry in value:
                    if not isinstance(entry, dict):
                        continue
                    parts += [str(v) for k, v in entry.items() if k != "id" and isinstance(v, str)]
                    # inline-run blocks keep their text nested in runs, not as top-level strings
                    if field_spec.kind == BLOCKS:
                        parts.append(_block_inline_text(entry))
                    # an element's block fields carry their text one level deeper again
                    for block_field in field_spec.block_element_fields():
                        for block in entry.get(block_field) or []:
                            if not isinstance(block, dict):
                                continue
                            parts += [str(v) for k, v in block.items()
                                      if k != "id" and isinstance(v, str)]
                            parts.append(_block_inline_text(block))
    return " ".join(part for part in parts if part)

"""Offline structural validator for a pasta workspace JSON file.

Reads a workspace document directly off disk - no server, no lock, no `src`
import - and reports structural integrity problems as a human fix-list keyed to
line numbers in the file. It never edits anything; it only tells a human what to
change and where.

    uv run python scripts/validate_workspace.py [PATH]

PATH defaults to `.pasta-data/ws_mrteq0c5-238cf6.json` (relative to the repo
root). Exit code is 0 when the file is clean and 1 when any problem is found, so
the script is usable as a check.

What it checks
--------------
* **Child existence** - every id in a page's `child_ids` resolves to a page.
* **Unique filing** - no page is listed as a child by two parents, and no page is
  both a root and someone's child (the page tree renders from `root_page_ids` +
  `child_ids`, so this list structure is treated as authoritative).
* **Parent back-reference** - each page's `parent_id` agrees with where the page
  is actually filed (its listed parent, or `null` for a root).
* **Reachability** - every page is filed exactly once (a root, or some parent's
  child); pages filed nowhere are orphans. The server's hourly cleanup sweep
  treats these as prunable after a 5-day grace period - see the "Scheduled
  workspace cleanup" architecture page.
* **root_page_ids** - its entries exist and are genuinely parentless, and every
  parentless page appears in it.
* **Unique ids** - no duplicated page-id key in the `pages` object.
* **Links** - every `links[].to` on a page resolves to an existing page.
* **Empty tocs** - a `toc` page with no non-archived children and no section
  content is flagged as removable.

The line numbers are exact for a workspace file written by the store, which
serializes with `json.dumps(..., indent=2)` (one token per line).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FILE = _REPO_ROOT / ".pasta-data" / "ws_mrteq0c5-238cf6.json"

# --- line index -------------------------------------------------------------
# The store writes the workspace with `json.dumps(..., indent=2)`, so every page
# sits in a predictable, one-token-per-line block. These regexes pick the exact
# lines back out so findings can point a human at a line number.

_PAGE_KEY = re.compile(r'^    "(?P<id>[^"]+)": \{$')       # `    "<id>": {`  (4-space indent)
_PAGE_END = re.compile(r"^    \},?$")                       # `    }` / `    },`
_FIELD = re.compile(r'^      "(?P<key>[^"]+)": ')           # `      "<field>": ...` (6-space)
_ARRAY_END = re.compile(r"^      \],?$")                    # `      ]` / `      ],`
_STRING_ELEM = re.compile(r'^        "(?P<val>[^"]+)",?$')  # `        "<value>",` (8-space)
_LINK_TO = re.compile(r'^          "to": "(?P<to>[^"]+)"')  # `          "to": "<id>"` (10-space)
_LINK_ROLE = re.compile(r'^          "role": "(?P<role>[^"]*)"')


class PageIndex:
    """Where a page and its structural fields live in the file (1-based lines)."""

    def __init__(self, page_id: str, start: int) -> None:
        self.id = page_id
        self.key_line = start                       # the `    "<id>": {` line
        self.parent_id_line: int | None = None
        self.child_lines: list[tuple[str, int]] = []   # (child_id, line) in order
        self.link_lines: list[tuple[str, int]] = []    # (to_id, line) in order


def build_index(text: str) -> tuple[dict[str, PageIndex], list[tuple[str, int]]]:
    """Scan the raw file text into a per-page line index.

    Returns `(index, duplicate_keys)` where `duplicate_keys` lists every page
    id that appears more than once as a key in the `pages` object (JSON parsing
    silently keeps only the last, so this is caught here from the raw text).
    """
    lines = text.split("\n")
    index: dict[str, PageIndex] = {}
    order: list[str] = []            # ids in first-seen order, keeps duplicates
    blocks: list[tuple[str, int, int]] = []   # (id, start_line, end_line) 1-based

    # Pass 1: page blocks. A page key is at exactly 4-space indent; its block runs
    # to the matching 4-space `}` close.
    i = 0
    n = len(lines)
    while i < n:
        m = _PAGE_KEY.match(lines[i])
        if m:
            start = i + 1
            j = i + 1
            while j < n and not _PAGE_END.match(lines[j]):
                j += 1
            blocks.append((m.group("id"), start, j + 1))
            order.append(m.group("id"))
            i = j + 1
            continue
        i += 1

    duplicate_keys: list[tuple[str, int]] = []
    for page_id, start, end in blocks:
        if page_id in index:
            duplicate_keys.append((page_id, start))
            continue
        idx = PageIndex(page_id, start)
        _scan_block(lines, start, end, idx)
        index[page_id] = idx

    return index, duplicate_keys


def _scan_block(lines: list[str], start: int, end: int, idx: PageIndex) -> None:
    """Fill in parent_id / child / link line numbers from one page block.

    `start` / `end` are 1-based inclusive line numbers of the page block.
    """
    k = start  # skip the key line, scan the fields
    while k < end:
        line = lines[k - 1]
        field = _FIELD.match(line)
        if not field:
            k += 1
            continue
        key = field.group("key")
        if key == "parent_id":
            idx.parent_id_line = k
            k += 1
        elif key == "child_ids":
            k = _scan_string_array(lines, k, end, idx.child_lines)
        elif key == "links":
            k = _scan_link_array(lines, k, end, idx.link_lines)
        else:
            k += 1


def _scan_string_array(lines: list[str], open_line: int, end: int,
                       out: list[tuple[str, int]]) -> int:
    """Collect `"value"` element lines of a string array that opens on `open_line`.

    Returns the line after the array's closing `]`.
    """
    if lines[open_line - 1].rstrip().endswith("[]") or lines[open_line - 1].rstrip().endswith("[],"):
        return open_line + 1
    k = open_line + 1
    while k < end and not _ARRAY_END.match(lines[k - 1]):
        m = _STRING_ELEM.match(lines[k - 1])
        if m:
            out.append((m.group("val"), k))
        k += 1
    return k + 1


def _scan_link_array(lines: list[str], open_line: int, end: int,
                     out: list[tuple[str, int]]) -> int:
    """Collect each link object's `to` id + line from a links array.

    Returns the line after the array's closing `]`.
    """
    if lines[open_line - 1].rstrip().endswith("[]") or lines[open_line - 1].rstrip().endswith("[],"):
        return open_line + 1
    k = open_line + 1
    while k < end and not _ARRAY_END.match(lines[k - 1]):
        m = _LINK_TO.match(lines[k - 1])
        if m:
            out.append((m.group("to"), k))
        k += 1
    return k + 1


# --- content emptiness ------------------------------------------------------

def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _has_section_content(sections: dict[str, Any]) -> bool:
    """True if any field anywhere in `sections` holds a non-empty value."""
    for fields in sections.values():
        if not isinstance(fields, dict):
            if not _is_empty_value(fields):
                return True
            continue
        for value in fields.values():
            if not _is_empty_value(value):
                return True
    return False


# --- findings ---------------------------------------------------------------

class Finding:
    __slots__ = ("line", "title", "detail")

    def __init__(self, line: int | None, title: str, detail: str) -> None:
        self.line = line
        self.title = title
        self.detail = detail


def _short(page_id: str, pages: dict[str, Any]) -> str:
    page = pages.get(page_id)
    if page is None:
        return page_id
    return f'{page_id} ("{page.get("title", "?")}", {page.get("type", "?")})'


def validate(data: dict[str, Any], index: dict[str, PageIndex],
             duplicate_keys: list[tuple[str, int]]) -> list[Finding]:
    pages: dict[str, Any] = data.get("pages", {})
    root_ids: list[str] = data.get("root_page_ids", [])
    ids = set(pages)
    findings: list[Finding] = []

    def loc(page_id: str) -> int | None:
        idx = index.get(page_id)
        return idx.key_line if idx else None

    # 1. Duplicate page-id keys.
    for page_id, line in duplicate_keys:
        findings.append(Finding(
            line,
            "Duplicate page id",
            f'Page id {page_id} appears more than once as a key in "pages" '
            f"(line {line}). JSON keeps only the last; remove the stale "
            f"duplicate block so the id is unique.",
        ))

    # 2. child_ids -> existence, and build the "listed parent" map from the tree
    #    structure (root_page_ids + child_ids), which drives rendering and is
    #    treated as authoritative for where a page is filed.
    listed_parents: dict[str, list[str]] = {}   # child_id -> [parent_id, ...]
    for page_id in pages:
        idx = index.get(page_id)
        child_line = {cid: ln for cid, ln in (idx.child_lines if idx else [])}
        seen_local: set[str] = set()
        for cid in pages[page_id].get("child_ids", []):
            line = child_line.get(cid, loc(page_id))
            if cid not in ids:
                findings.append(Finding(
                    line,
                    "child_ids -> missing page",
                    f'{_short(page_id, pages)} lists child {cid} (line {line}), '
                    f"but no such page exists. Remove that child_ids entry.",
                ))
                continue
            if cid in seen_local:
                findings.append(Finding(
                    line,
                    "Duplicate child entry",
                    f"{_short(page_id, pages)} lists child {cid} more than once "
                    f"(line {line}). Remove the duplicate child_ids entry.",
                ))
                continue
            seen_local.add(cid)
            listed_parents.setdefault(cid, []).append(page_id)

    # 3. Unique filing: a page listed by >1 parent, or listed as a child while
    #    also a root.
    root_set = set(root_ids)
    for cid, parents in listed_parents.items():
        if len(parents) > 1:
            where = "; ".join(f"{p} (line {loc(p)})" for p in parents)
            findings.append(Finding(
                loc(cid),
                "Page filed under multiple parents",
                f"{_short(cid, pages)} is listed as a child by {len(parents)} "
                f"parents: {where}. Remove it from all but the correct parent's "
                f"child_ids.",
            ))
        if cid in root_set:
            findings.append(Finding(
                loc(cid),
                "Page is both a root and a child",
                f"{_short(cid, pages)} appears in root_page_ids AND in the "
                f"child_ids of {parents[0]} (line {loc(parents[0])}). Remove it "
                f"from whichever is wrong (a page is filed in exactly one place).",
            ))

    # 4. parent_id agreement with the listed parent.
    for page_id in pages:
        idx = index.get(page_id)
        parent_id = pages[page_id].get("parent_id")
        parents = listed_parents.get(page_id, [])
        expected = parents[0] if parents else (None if page_id in root_set else "<orphan>")
        pline = idx.parent_id_line if idx else loc(page_id)

        if expected == "<orphan>":
            # Reachability problem: filed nowhere. Reported in section 5.
            continue
        if len(parents) > 1:
            # Filed under multiple parents: which parent_id is "right" is
            # ambiguous until that is resolved (reported in section 3). Skip.
            continue
        if parent_id != expected:
            want = "null" if expected is None else expected
            got = "null" if parent_id is None else parent_id
            if expected is None:
                where = "it is a top-level (root) page"
            else:
                where = f"it is filed under {expected}"
            findings.append(Finding(
                pline,
                "parent_id disagrees with filing",
                f"{_short(page_id, pages)} has parent_id {got} (line {pline}), "
                f"but {where}. Set parent_id to {want}.",
            ))

    # 5. Reachability: every page filed exactly once (root or some child).
    for page_id in pages:
        if page_id in root_set:
            continue
        if not listed_parents.get(page_id):
            parent_id = pages[page_id].get("parent_id")
            hint = (f' Its parent_id points to {parent_id}, which does not list '
                    f"it as a child - add it to that parent's child_ids, or add "
                    f"this page to root_page_ids." if parent_id else
                    " Add it to a parent's child_ids or to root_page_ids.")
            findings.append(Finding(
                loc(page_id),
                "Orphan page (filed nowhere)",
                f"{_short(page_id, pages)} is not in root_page_ids and no page "
                f"lists it as a child, so it is unreachable in the tree.{hint}"
                f" The hourly cleanup sweep stamps an unreachable page with an "
                f"expiry 5 days out and deletes it once that passes, so re-file "
                f"it before then.",
            ))

    # 6. root_page_ids sanity.
    for rid in root_ids:
        if rid not in ids:
            findings.append(Finding(
                None,
                "root_page_ids -> missing page",
                f"root_page_ids contains {rid}, but no such page exists. Remove "
                f"it from root_page_ids (near the top of the file).",
            ))
        elif listed_parents.get(rid):
            findings.append(Finding(
                loc(rid),
                "root entry is also a child",
                f"root_page_ids contains {rid}, but it is also a child of "
                f"{listed_parents[rid][0]}. Remove it from root_page_ids.",
            ))

    # 7. Links -> existence.
    for page_id in pages:
        idx = index.get(page_id)
        link_line = idx.link_lines if idx else []
        li = 0
        for link in pages[page_id].get("links", []):
            to_id = link.get("to")
            role = link.get("role")
            line = link_line[li][1] if li < len(link_line) else loc(page_id)
            li += 1
            if to_id not in ids:
                findings.append(Finding(
                    line,
                    "link -> missing page",
                    f'{_short(page_id, pages)} has a link (role "{role}") to '
                    f"{to_id} (line {line}), but no such page exists. Remove that "
                    f"link entry.",
                ))

    # 8. Empty tocs.
    for page_id in pages:
        page = pages[page_id]
        if page.get("type") != "toc":
            continue
        live_children = [c for c in page.get("child_ids", [])
                         if c in ids and not pages[c].get("archived", False)]
        if live_children:
            continue
        if _has_section_content(page.get("sections", {})):
            continue
        findings.append(Finding(
            loc(page_id),
            "Empty toc (removable)",
            f"{_short(page_id, pages)} has no non-archived child pages and no "
            f"section content (line {loc(page_id)}). It is a candidate for "
            f"removal - delete the page block and its id from wherever it is "
            f"filed (root_page_ids or a parent's child_ids).",
        ))

    return findings


# --- report -----------------------------------------------------------------

def render_report(path: Path, data: dict[str, Any], findings: list[Finding]) -> str:
    pages = data.get("pages", {})
    out: list[str] = []
    out.append(f"Workspace integrity report for {path}")
    out.append(f'  workspace: {data.get("id", "?")}  "{data.get("name", "?")}"')
    out.append(f"  pages: {len(pages)}   findings: {len(findings)}")
    out.append("")

    if not findings:
        out.append("No structural problems found. Nothing to fix.")
        return "\n".join(out)

    # Group by title, and within a group sort by line number.
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        groups.setdefault(f.title, []).append(f)

    out.append("Fixes to apply (grouped by problem; line numbers refer to the file above):")
    out.append("")
    n = 0
    for title in sorted(groups, key=lambda t: min((f.line or 0) for f in groups[t])):
        group = sorted(groups[title], key=lambda f: (f.line or 0))
        out.append(f"== {title} ({len(group)}) ==")
        for f in group:
            n += 1
            where = f"line {f.line}" if f.line is not None else "top of file"
            out.append(f"  {n}. [{where}] {f.detail}")
        out.append("")

    out.append(f"{len(findings)} finding(s). Apply the edits above, then re-run to confirm a clean file.")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    # Page titles can hold non-ASCII; don't let a narrow console encoding crash
    # the report.
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    path = Path(argv[1]).expanduser() if len(argv) > 1 else _DEFAULT_FILE
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"error: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    index, duplicate_keys = build_index(text)
    findings = validate(data, index, duplicate_keys)
    print(render_report(path, data, findings))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Generate the per-page-type-state Sphinx docs and write them into the docsite.

The content is produced purely by `src.docsgen` (unit-tested); this driver only does the
I/O: it splits the emitted docs into one `docsite/page-types/<tag>-<state>.md` file each, plus a
generated `docsite/page-types/states.md` toctree index, then prints what it wrote.

    uv run python scripts/gen_page_type_docs.py

It does NOT touch the hand-authored per-type overview docs (`page-types/<tag>.md`), and it does
not delete previously generated files - remove a stale `<tag>-<state>.md` by hand if a state is
ever dropped from a page type. Re-run after changing the page-type registry or the doc templates.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))   # make `src` importable when run as a bare script

from src.docsgen import STATES_INDEX_STEM, all_state_docs, render_states_index

PAGE_TYPES_DIR = _REPO_ROOT / "docsite" / "page-types"


def main() -> None:
    PAGE_TYPES_DIR.mkdir(parents=True, exist_ok=True)

    docs = all_state_docs()
    docs[STATES_INDEX_STEM] = render_states_index()

    written, skipped = 0, 0
    for stem, markdown in docs.items():
        path = PAGE_TYPES_DIR / f"{stem}.md"
        # A missing file is the normal case on a clean checkout (and after `just docs` wipes the
        # directory), so only an existing file is read back to skip an unchanged rewrite.
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing == markdown:
            skipped += 1
            continue
        path.write_text(markdown, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(PAGE_TYPES_DIR.parent.parent)}")
        written += 1

    print(f"\n{written} files written to {PAGE_TYPES_DIR} ({skipped} unchanged)")


if __name__ == "__main__":
    main()

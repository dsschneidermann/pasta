"""Report or fix double-backtick inline code in the docstrings and comments of Python files.

The codebase writes inline code with single backticks, and a docstring renders as MyST, where the
double form (two backticks each side) is the same code span. This finds the double form in every
docstring - module, class, function and attribute - and every comment, and with --fix rewrites it
to the single form. Nothing outside docstrings and comments is read or written, so backticks in
string literals the code emits are left alone, as is a span that holds a backtick of its own.

    uv run python scripts/docstring_backticks.py          # report; exits 1 when any are found
    uv run python scripts/docstring_backticks.py --fix    # rewrite them in place

With no paths it checks every git-tracked .py file.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Two backticks each side around text holding none; a third backtick on either side is a fence.
DOUBLE_SPAN = re.compile(r"(?<!`)``([^`\n]+)``(?!`)")


def doc_ranges(src: str) -> list[tuple[int, int]]:
    """The character ranges of every docstring and comment in `src`, in source order."""
    lines = src.split("\n")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)

    def offset(lineno: int, byte_col: int) -> int:
        # ast columns count UTF-8 bytes, while slicing counts characters.
        return starts[lineno - 1] + len(lines[lineno - 1].encode("utf-8")[:byte_col].decode("utf-8"))

    ranges = [
        (offset(node.value.lineno, node.value.col_offset), offset(node.value.end_lineno, node.value.end_col_offset))
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    ]
    ranges += [
        (starts[tok.start[0] - 1] + tok.start[1], starts[tok.end[0] - 1] + tok.end[1])
        for tok in tokenize.generate_tokens(io.StringIO(src).readline)
        if tok.type == tokenize.COMMENT
    ]
    return sorted(ranges)


def double_spans(src: str) -> int:
    """How many double-backtick spans the docstrings and comments of `src` hold."""
    return sum(len(DOUBLE_SPAN.findall(src[start:end])) for start, end in doc_ranges(src))


def to_single(src: str) -> str:
    """`src` with every double-backtick span in its docstrings and comments made single."""
    for start, end in reversed(doc_ranges(src)):
        src = src[:start] + DOUBLE_SPAN.sub(r"`\1`", src[start:end]) + src[end:]
    return src


def tracked_python_files() -> list[Path]:
    names = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [_REPO_ROOT / name for name in names]


def _shown(path: Path) -> Path:
    resolved = path.resolve()
    return resolved.relative_to(_REPO_ROOT) if resolved.is_relative_to(_REPO_ROOT) else path


def main() -> None:
    parser = argparse.ArgumentParser(description="Report or fix double-backtick inline code in docstrings and comments.")
    parser.add_argument("--fix", action="store_true", help="rewrite the double spans in place")
    parser.add_argument("paths", nargs="*", type=Path, help="files to check (default: every tracked .py file)")
    args = parser.parse_args()

    found = 0
    for path in args.paths or tracked_python_files():
        # Bytes in and out, so a file's line endings survive the rewrite.
        src = path.read_bytes().decode("utf-8")
        count = double_spans(src)
        if not count:
            continue
        found += count
        if args.fix:
            path.write_bytes(to_single(src).encode("utf-8"))
        print(f"{count:4d}  {_shown(path)}")

    print(f"\n{found} double-backtick spans {'fixed' if args.fix else 'found'}")
    if found and not args.fix:
        sys.exit(1)


if __name__ == "__main__":
    main()

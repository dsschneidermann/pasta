
docs:
    rm -rf docsite/page-types
    uv run python scripts/gen_page_type_docs.py
    uv run sphinx-build -a docsite -b html docsite/_build/html

probe:
    uv run python scripts/mcp_probe.py

# Walk a real feature-brief through its whole lifecycle, printing each stage's `do` rollup,
# then archive it. Server must be running. Pass args through, e.g. `just brief-probe --keep`.
brief-probe *ARGS:
    uv run python scripts/feature_brief_probe.py {{ARGS}}

test:
    #!/bin/bash
    export PYTHONDONTWRITEBYTECODE=1
    uv run pytest

testincr:
    #!/bin/bash
    export PYTHONDONTWRITEBYTECODE=1
    uv run pytest --testmon

types:
    uv run basedpyright

validate:
    uv run python scripts/validate_workspace.py

main:
    uv run python main.py

klaus:
    klaus --host 0.0.0.0 .

sphinx:
    uv run sphinx-autobuild docsite -q -b html docsite/_build/html \
    --watch src --port 8081 \
    --pre-build 'uv run python scripts/gen_page_type_docs.py'

[parallel]
dev: main sphinx

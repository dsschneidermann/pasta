"""Unit tests for the dev server's reload-failure reporting (src.hmr_server).

Only the reporting is covered here. The reload machinery itself needs a live uvicorn process, a
file watcher and a real edit, so it is exercised by hand against the running dev server rather
than in this suite; what is pinned here is the part that regressed silently - a failed re-exec
that left no usable trace in the console.
"""

import logging

import pytest

from src.hmr_server import _LoggingErrorFilter


@pytest.fixture
def swallow(caplog):
    """Run a reload failure through the filter hmr uses, capturing what reaches the dev console."""
    def _run(exc: BaseException):
        with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
            with _LoggingErrorFilter():
                raise exc
        return caplog.text
    return _run


def test_a_swallowed_reload_error_reaches_the_console_as_an_error(swallow):
    # hmr prints a failed re-exec through sys.excepthook and swallows it, which lands a bare
    # traceback between uvicorn's INFO lines - no level, no prefix, nothing a reader can scan for.
    text = swallow(ValueError("Invalid page-type declarations:\n- bug-report: bad setter"))
    assert "ERROR" in text
    assert "[HMR]" in text


def test_the_report_names_the_error_that_failed_the_reload(swallow):
    text = swallow(ValueError("Invalid page-type declarations:\n- bug-report: bad setter"))
    assert "Invalid page-type declarations" in text
    assert "bad setter" in text


def test_the_report_states_the_consequence_not_just_the_error(swallow):
    # The traceback alone never said what it cost: the reload did not take, so the modules loaded
    # before it are still the ones serving. That is the line the developer actually needs, and its
    # absence is why a 200 OK on the very next line read as normal.
    text = swallow(ValueError("boom"))
    assert "did not take" in text.lower() or "still serving" in text.lower()


def test_the_failure_is_still_swallowed_so_the_dev_server_survives(swallow):
    # Logging must not change hmr's contract: the error is reported, not re-raised. A raise here
    # would tear down the watcher and drop every live MCP session on a half-finished save.
    assert swallow(ValueError("boom")) is not None


def test_a_clean_reload_reports_nothing(caplog):
    # No error, no noise - the filter is invisible on the ordinary path.
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        with _LoggingErrorFilter():
            pass
    assert caplog.text == ""

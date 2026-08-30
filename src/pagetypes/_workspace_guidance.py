"""Descriptions of the workspace-configurable guidance fields.

Each field's description is a single constant here, so page types that share a field give it the
same description and validation stays satisfied. The stored text is not here; it is set per
workspace at runtime, and only the field names and descriptions are fixed in code.

The module imports nothing, so it can be read early during package setup, and its leading
underscore keeps it above the page-type modules that use it.
"""


MERGE_PROCESS_FIELD = "mergeProcess"
MERGE_PROCESS_DESC = (
    "How this workspace integrates finished work when shipping - for example, rebase onto main "
    "with no merge commit, or merge through a pull request."
)

TESTING_TOOL_FIELD = "testingTool"
TESTING_TOOL_DESC = (
    "The test runner and command this workspace uses to write and run tests - for example, "
    "pytest, run with --testmon for red-green testing."
)

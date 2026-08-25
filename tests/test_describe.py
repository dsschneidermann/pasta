"""Unit tests for the pure introspection helpers (src.describe)."""

from src.commands import create_page
from src.describe import (
    command_arg_schema,
    describe_mutations,
    describe_page_type,
)
from src.pagetypes import get_page_type

# Hand-authored capability fixtures (src.testtypes), so enriching a production type never
# churns these introspection assertions.
FIELDS = get_page_type("test-fields")     # arg schema (enum / required / optional) + type shape
FLOW = get_page_type("test-flow")         # transition availability


def _counter():
    state = {"n": 0}

    def factory(prefix: str) -> str:
        state["n"] += 1
        return f"{prefix}:{state['n']}" if prefix else f"el{state['n']}"

    return factory


def test_command_arg_schema_has_required_and_enum():
    set_kind = FIELDS.command("setKind")
    schema = command_arg_schema(set_kind)
    assert schema["required"] == ["kind"]
    assert schema["properties"]["kind"]["enum"] == list(FIELDS.command("setKind").args[0].choices)
    assert schema["additionalProperties"] is False


def test_command_arg_schema_optional_not_required():
    add_item = FIELDS.command("addItem")
    schema = command_arg_schema(add_item)
    assert "text" in schema["required"]
    assert "note" not in schema["required"]         # optional


def test_describe_page_type_shape():
    described = describe_page_type(FIELDS)
    assert described["tag"] == "test-fields"
    assert described["fsm"]["initial"] == "active"
    section_keys = {section["key"] for section in described["sections"]}
    assert {"basics", "items"} <= section_keys
    command_names = {command["name"] for command in described["commands"]}
    assert {"setKind", "setBody", "addItem", "flagItem"} <= command_names


def test_describe_mutations_marks_availability():
    page = create_page(FLOW, "A change", None, _counter())
    described = {entry["name"]: entry["available"] for entry in describe_mutations(page, FLOW)}
    assert described["setSummary"] is True
    assert described["open"] is True        # legal from draft
    assert described["close"] is False      # not legal until open


def test_describe_mutations_terminal_state_locks_authoring_keeps_transitions():
    # test-flow's `closed` is a declared-terminal state that still offers a `reopen` edge. The
    # describeMutations surface must mark authoring unavailable while keeping the transition available.
    page = create_page(FLOW, "A change", None, _counter())
    page.status = "closed"
    available = {entry["name"]: entry["available"] for entry in describe_mutations(page, FLOW)}
    assert available["setSummary"] is False       # authoring locked in a terminal state
    assert available["addLink"] is False          # authoring locked
    assert available["reopen"] is True            # transition still surfaced as available


def test_describe_mutations_ignore_requirements_surfaces_gated_transition():
    lifecycle = get_page_type("test-lifecycle")
    page = create_page(lifecycle, "New feature", None, _counter())   # empty draft
    default = {entry["name"]: entry["available"] for entry in describe_mutations(page, lifecycle)}
    skipped = {entry["name"]: entry["available"]
               for entry in describe_mutations(page, lifecycle, ignore_requirements=True)}
    # beginPlanning requires summary.body: hidden by default, surfaced when requirements are skipped.
    assert default["beginPlanning"] is False
    assert skipped["beginPlanning"] is True


def test_describe_page_type_keeps_the_instruction_on_the_field_not_the_setter():
    """A field's authoring instruction is reported once, on the field; its setter carries a short
    description instead."""
    described = describe_page_type(FIELDS)
    commands = {c["name"]: c for c in described["commands"]}
    fields = {(s["key"], f["key"]): f for s in described["sections"] for f in s["fields"]}

    set_body = commands["setBody"]
    assert set_body["section"] == "basics" and set_body["field"] == "body"
    assert set_body["description"] == "set the body"
    assert fields[("basics", "body")]["description"] == "a prose body"    # the instruction stays here
    assert set_body["description"] != fields[("basics", "body")]["description"]

    flow = {c["name"]: c for c in describe_page_type(FLOW)["commands"]}
    assert flow["setSummary"]["description"] == "set the summary"   # derived, not the instruction
    assert flow["open"]["description"] == "draft → open"            # a transition is unaffected


def test_describe_mutations_is_full_catalog():
    """describeMutations stays the FULL command catalog with unchanged legality."""
    page = create_page(FIELDS, "A", None, _counter())
    mutations = describe_mutations(page, FIELDS)
    assert {m["name"] for m in mutations} == {c.name for c in FIELDS.commands}   # full catalog
    by_name = {m["name"]: m for m in mutations}
    assert by_name["setBody"]["available"] is True


def test_describe_fsm_projects_state_guidance():
    """The guidance rides on the FSM projection, which is how doc generation reads it."""
    described = describe_page_type(FLOW)["fsm"]
    assert described["stateGuidance"] == {
        "open": "open - the work is under way.\nRecord a commit with close when it is finished."
    }
    # Pre-existing keys untouched, so describePageType stays backward compatible.
    assert described["initial"] == "draft" and described["states"] == ["draft", "open", "closed"]
    # A type declaring none projects an empty mapping rather than omitting the key.
    assert describe_page_type(get_page_type("test-blocks"))["fsm"]["stateGuidance"] == {}


def test_add_reports_its_block_argument_shape():
    """A caller reads the create-with-content shape off the schema rather than learning a rule."""
    described = describe_page_type(get_page_type("test-element-blocks"))
    add = next(c for c in described["commands"] if c["name"] == "addItem")
    detail = add["args"]["properties"]["detail"]
    assert detail["type"] == "array"
    branches = detail["items"]["oneOf"]
    assert [b["properties"]["kind"]["const"] for b in branches] == ["paragraph", "code", "list"]
    code = next(b for b in branches if b["properties"]["kind"]["const"] == "code")
    assert code["required"] == ["kind", "language", "source"]
    # snippet accepts code only, and neither argument is required to create an item.
    snippet = add["args"]["properties"]["snippet"]
    assert [b["properties"]["kind"]["const"] for b in snippet["items"]["oneOf"]] == ["code"]
    assert "detail" not in add["args"]["required"] and "snippet" not in add["args"]["required"]


def test_describe_reports_block_bearing_element_fields():
    """An agent authors from this surface, so a capability missing here does not exist."""
    described = describe_page_type(get_page_type("test-element-blocks"))
    field = described["sections"][0]["fields"][0]
    assert field["elementBlocks"] == [
        {"field": "snippet", "kinds": ["code"]},
        {"field": "detail", "kinds": ["paragraph", "code", "list"]},
    ]
    # The pre-existing keys are untouched beside it.
    assert field["elementFields"] == ["text", "snippet", "detail", "status"]
    assert field["elementStates"] == ["todo", "done"]
    # A list declaring none reports None rather than an empty list.
    plain = describe_page_type(get_page_type("test-fields"))["sections"][1]["fields"][0]
    assert plain["elementBlocks"] is None


def test_a_blocks_add_describes_its_whole_vocabulary():
    """Without this the collapsed surface is unauthorable: there is no longer a command name per
    kind, so the arg schema is the only place a caller can learn what a block may be."""
    blocks = get_page_type("test-blocks")
    described = describe_page_type(blocks)
    commands = {c["name"]: c for c in described["commands"]}

    add = commands["addBody"]["args"]["properties"]["blocks"]
    assert add["type"] == "array"
    branches = add["items"]["oneOf"]
    assert [branch["properties"]["kind"]["const"] for branch in branches] == [
        "paragraph", "heading", "code", "list", "quote", "table", "divider"]
    heading = next(b for b in branches if b["properties"]["kind"]["const"] == "heading")
    assert set(heading["properties"]) == {"kind", "level", "inlines"}
    assert sorted(heading["required"]) == ["inlines", "kind", "level"]
    table = next(b for b in branches if b["properties"]["kind"]["const"] == "table")
    assert "align" in table["properties"] and "align" not in table["required"]   # optional

    # The add is the only place a vocabulary is advertised - nothing else carries blocks.
    carriers = [c["name"] for c in described["commands"]
                if any("oneOf" in prop or "oneOf" in prop.get("items", {})
                       for prop in c["args"]["properties"].values())]
    assert carriers == ["addBody"]


def test_an_overridden_kind_advertises_its_own_args():
    """test-child declares `paragraph` with a plain text arg, so the schema must show `text` -
    not the standard `inlines` it would show if describe read the global table."""
    child = describe_page_type(get_page_type("test-child"))
    add = {c["name"]: c for c in child["commands"]}["addDecisions"]
    branches = add["args"]["properties"]["blocks"]["items"]["oneOf"]
    paragraph = next(b for b in branches if b["properties"]["kind"]["const"] == "paragraph")
    assert set(paragraph["properties"]) == {"kind", "text"}
    decision = next(b for b in branches if b["properties"]["kind"]["const"] == "decision")
    assert set(decision["properties"]) == {"kind", "questionId", "text"}


def test_no_command_reports_a_block_kind():
    """CommandSpec.block_kind is deleted, so the key must not linger as a permanent null on
    every command of every type."""
    for tag in ("test-blocks", "test-element-blocks", "test-child", "test-fields"):
        for command in describe_page_type(get_page_type(tag))["commands"]:
            assert "blockKind" not in command


def _fields_of(tag):
    return {(section["key"], field["key"]): field
            for section in describe_page_type(get_page_type(tag))["sections"]
            for field in section["fields"]}


def test_a_blocks_field_reports_its_kinds():
    """The first time a page-level field's vocabulary is visible to a caller at all."""
    # An undeclared vocabulary reports every standard kind...
    body = _fields_of("test-blocks")[("body", "body")]
    assert body["blockKinds"] == ["paragraph", "heading", "code", "list", "quote", "table",
                                  "divider"]
    # ...and a declared one reports exactly what it names, custom kinds included.
    assert _fields_of("test-child")[("decisions", "body")]["blockKinds"] == [
        "decision", "paragraph"]
    element = _fields_of("test-element-blocks")
    # A non-blocks field reports none, and an element one still reports its kinds by name.
    items = element[("items", "items")]
    assert items["blockKinds"] is None
    assert items["elementBlocks"] == [
        {"field": "snippet", "kinds": ["code"]},
        {"field": "detail", "kinds": ["paragraph", "code", "list"]},
    ]

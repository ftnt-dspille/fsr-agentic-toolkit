"""A connector result's rows sit a few keys down: the cap trims whole rows there, and fields= keeps only named columns."""
import json

from fsr_agentic_toolkit.verbs import tools as T


def _faz_like(n: int) -> dict:
    """The shape of a FortiAnalyzer get_alert_event_logs result: rows at data.result.data, ~1.3 KB each."""
    rows = [{"itime": 1700000000 + i, "srcip": "192.0.2.11", "dstip": "198.51.100.10", "dstport": 1000 + i,
             "policyid": 20 if i % 2 else 905, "action": "deny" if i % 2 else "accept", "filler": "x" * 1200}
            for i in range(n)]
    return {"operation": None, "status": "Success", "message": "", "data": {"jsonrpc": "2.0", "id": 1, "result": {"data": rows}}}


def test_nested_rows_are_trimmed_whole_not_previewed():
    out = T._cap_output(_faz_like(40), tool="run_connector_operation", cap=4000)
    assert out["_truncated"]["path"] == "data.result.data"
    rows = out["data"]["result"]["data"]
    assert 1 <= len(rows) < 40 and out["_truncated"]["shown"] == len(rows) and out["_truncated"]["total"] == 40
    assert all(set(r) >= {"policyid", "action"} for r in rows)   # whole records, not a text fragment
    assert len(json.dumps(out)) <= 4000


def test_fields_keeps_only_named_columns_on_nested_rows():
    slim = T._project_fields(_faz_like(40), ["itime", "srcip", "dstport", "policyid", "action"])
    rows = slim["data"]["result"]["data"]
    assert len(rows) == 40 and set(rows[0]) == {"itime", "srcip", "dstport", "policyid", "action"}
    out = T._cap_output(slim, tool="run_connector_operation", cap=4000)
    shown = out["data"]["result"]["data"]
    assert len(shown) >= 15   # narrowed rows: many more fit under the same cap (about 2 without fields)


def test_small_result_and_top_level_lists_are_untouched():
    small = _faz_like(1)
    small["data"]["result"]["data"][0]["filler"] = ""
    assert T._cap_output(small, tool="t", cap=4000) == small
    assert T._dominant_list_path({"members": [{"a": 1}]}) is None   # top level stays on the existing path


def test_hint_names_the_order_and_time_span_of_the_shown_rows():
    res = _faz_like(40)
    res["data"]["result"]["data"].reverse()   # newest first, like a log search
    hint = T._cap_output(res, tool="run_connector_operation", cap=4000)["_truncated"]["hint"]
    assert "newest first by itime" in hint and "all 40 span 2023-11-14 22:13:20Z to 2023-11-14 22:13:59Z" in hint


def test_hint_says_the_operation_returned_everything():
    hint = T._cap_output(_faz_like(40), tool="run_connector_operation", cap=4000)["_truncated"]["hint"]
    assert "returned all 40" in hint and "will not bring them back" in hint and "fields=" in hint


def test_listing_shows_the_parameters_a_choice_opens():
    params = [{"name": "address_group_category", "type": "select", "required": True,
               "onchange": {"IPv4 Group": [{"name": "group_name", "type": "text", "required": True},
                                           {"name": "add_member", "type": "text"}]}},
              {"name": "vdom", "type": "text"}]
    out = T._flatten_operation_params(params)
    names = {p["name"]: p for p in out}
    assert set(names) == {"address_group_category", "group_name", "add_member", "vdom"}
    assert names["add_member"]["when"] == "address_group_category = IPv4 Group" and names["group_name"]["required"] is True
    assert "when" not in names["vdom"]

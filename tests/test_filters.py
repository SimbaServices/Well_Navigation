import unittest
from urllib.parse import parse_qs

from wellnav.filters import (
    apply_search_input,
    empty_filters,
    parse_column_filters,
    parse_filters,
    search_kwargs,
    subtitle,
)
from wellnav.operators import normalize_operator_name


class Params(dict):
    def getlist(self, key):
        value = self.get(key, [])
        if isinstance(value, list):
            return value
        return [value] if value else []


class FilterParseTests(unittest.TestCase):
    def test_normalize_operator_name(self) -> None:
        self.assertEqual(normalize_operator_name("BPX Operating Company"), "BPX OPERATING COMPANY")
        self.assertEqual(normalize_operator_name("  bpx   operating  company "), "BPX OPERATING COMPANY")
        self.assertEqual(normalize_operator_name(""), "")

    def test_add_operator_becomes_active(self) -> None:
        filters = parse_filters(Params(add_op_number="123", add_op_name="OXY USA"))
        self.assertEqual(filters["operators"], [{"number": "123", "name": "OXY USA", "active": True}])

    def test_uncheck_operator_keeps_chip(self) -> None:
        filters = parse_filters(Params(opn=["123|OXY", "456|PIONEER"], op=["123"]))
        by_num = {op["number"]: op for op in filters["operators"]}
        self.assertTrue(by_num["123"]["active"])
        self.assertFalse(by_num["456"]["active"])

    def test_remove_operator(self) -> None:
        filters = parse_filters(Params(opn=["123|OXY"], op=["123"], remove_op="123"))
        self.assertEqual(filters["operators"], [])

    def test_clear_all(self) -> None:
        filters = parse_filters(
            Params(opn=["123|OXY"], op=["123"], name="UNI", use_name="1", clear_filters="1")
        )
        self.assertEqual(filters, empty_filters())

    def test_stack_name_on_operators(self) -> None:
        filters = parse_filters(Params(opn=["123|OXY"], op=["123"]))
        filters = apply_search_input(filters, mode="name", q="UNIVERSITY")
        self.assertEqual(search_kwargs(filters)["operator_numbers"], ["123"])
        self.assertEqual(search_kwargs(filters)["name"], "UNIVERSITY")
        self.assertIn("Operator OXY", subtitle(filters))
        self.assertIn("UNIVERSITY", subtitle(filters))

    def test_name_checkbox_off(self) -> None:
        filters = parse_filters(Params(name="UNIVERSITY"))
        self.assertFalse(filters["name_active"])
        filters = parse_filters(Params(name="UNIVERSITY", use_name="1"))
        self.assertTrue(filters["name_active"])
        filters = parse_filters(Params(name="UNIVERSITY", use_name="0"))
        self.assertEqual(filters["name"], "UNIVERSITY")
        self.assertFalse(search_kwargs(filters)["name"])

    def test_filter_query_roundtrip(self) -> None:
        from wellnav.filters import filter_query

        filters = parse_filters(Params(add_op_number="123", add_op_name="OXY"))
        filters = apply_search_input(filters, mode="name", q="UNI")
        parsed = parse_qs(filter_query(filters, mode="name", offset=0))
        self.assertEqual(parsed["op"], ["123"])
        self.assertEqual(parsed["name"], ["UNI"])

    def test_column_filters_drop_blanks_and_cap_length(self) -> None:
        parsed = parse_column_filters(
            Params(cf_operator="  OXY  ", cf_county="", cf_name="N" * 90, cf_api="42-003")
        )
        self.assertEqual(parsed["operator"], "OXY")
        self.assertNotIn("county", parsed)
        self.assertEqual(len(parsed["name"]), 80)
        self.assertEqual(parsed["api"], "42-003")

    def test_case_and_id_variants_collapse_to_one_operator(self) -> None:
        filters = parse_filters(
            Params(
                opn=[
                    "B6983|BPX OPERATING COMPANY",
                    "B372|BPX Operating Company",
                ],
                op=["B6983", "B372"],
            )
        )
        self.assertEqual(len(filters["operators"]), 1)
        self.assertEqual(filters["operators"][0]["name"], "BPX OPERATING COMPANY")
        self.assertTrue(filters["operators"][0]["active"])
        kwargs = search_kwargs(filters)
        self.assertEqual(kwargs["operator_names"], ["BPX OPERATING COMPANY"])
        self.assertIn(filters["operators"][0]["number"], {"B6983", "B372"})

    def test_name_only_operator_chip(self) -> None:
        filters = parse_filters(Params(add_op_name="BP America Production Company"))
        self.assertEqual(len(filters["operators"]), 1)
        self.assertEqual(filters["operators"][0]["name"], "BP AMERICA PRODUCTION COMPANY")
        self.assertTrue(filters["operators"][0]["active"])
        self.assertEqual(
            search_kwargs(filters)["operator_names"],
            ["BP AMERICA PRODUCTION COMPANY"],
        )

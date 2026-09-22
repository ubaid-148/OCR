"""No OCR/model is run here: tests cover schema, routing and safety decisions."""
import os
import unittest
from unittest.mock import patch

from invoice_response import clean_invoice_response
from visual_invoice import ask_visual, merge_pages, normalize_full, parse_invoice_visual, reconcile_with_spatial


def visual_raw():
    return {
        "document_type": "Tax Invoice", "document_type_ar": "فاتورة ضريبية",
        "handwritten_notes": ["9498", "5715"],
        "supplier": {"name_ar": "شركة عبدالرحمن احمد الراجحي التجارية",
                     "name_en": "Abdulrahman Ahmed Alrajhi Trading Co.",
                     "vat_number": "310981818100003", "building_no": "6595"},
        "invoice": {"invoice_number": "2690111862", "date": "2026-03-09",
                    "date_of_supply": "2026-04-02", "time": "11:19:38",
                    "payment_method": "Span Card - Mada"},
        "customer": {"name_ar": "مؤسسة علي محمد ال ريح للمقاولات العامة",
                     "vat_number": "300402905100003", "building_no": "3518"},
        "items": [
            {"item_code": "1212", "description": "زيت شل هيلكس HELIX 15/40",
             "quantity": 2, "unit": "PCS", "unit_price": 14.79, "discount": 0,
             "amount": 14.79, "tax_rate": 15, "vat_amount": 2.22, "gross_amount": 34.02},
            {"item_code": "1218", "description": "زيت شل هيلكس HELIX 4لتر*15/40",
             "quantity": 1, "unit": "PCS", "unit_price": 50.44, "discount": 0,
             "amount": 50.44, "tax_rate": 15, "vat_amount": 7.57, "gross_amount": 58.01},
            {"item_code": "5007", "description": "سيفون تويوتا وكالة D4",
             "quantity": 1, "unit": "PCS", "unit_price": 14.79, "discount": 0,
             "amount": 14.79, "tax_rate": 15, "vat_amount": 2.22, "gross_amount": 17.01},
        ],
        "amount_in_words_ar": "مئة وتسعة ريال سعودي وثلاث هللات",
        "vat_summary": {"before_tax": 94.81, "tax_amount": 14.22,
                        "inc_tax": 109.03, "tax_code": "S"},
        "totals": {"subtotal": 94.81, "discount": 0, "other_charges": 0,
                   "taxable_amount": 94.81, "vat_rate": 15, "vat_amount": 14.22,
                   "net_amount": 109.03, "currency": "SAR"},
        "other_fields": [{"label": "CR", "value": "2050209041", "page": 99}],
    }


def fallback():
    return {"data": {"supplier": {}, "invoice": {}, "customer": {},
                     "items": [{"item_code": "1212"}], "totals": {}},
            "quality": {"needs_review": True, "missing_fields": [],
                        "parser": "spatial_fast", "local_ai_status": "disabled"}}


class VisualInvoiceTests(unittest.TestCase):
    def test_full_fields_and_printed_per_unit_values_survive_response(self):
        data = normalize_full(visual_raw(), "9498.pdf", "eng+ara", page_number=2)
        response = clean_invoice_response({"data": data, "quality": {"needs_review": True}})
        self.assertEqual(response["data"]["supplier"]["building_no"], "6595")
        self.assertEqual(response["data"]["customer"]["building_no"], "3518")
        self.assertEqual(response["data"]["invoice"]["date_of_supply"], "2026-04-02")
        self.assertEqual([row["item_code"] for row in response["data"]["items"]],
                         ["1212", "1218", "5007"])
        self.assertEqual(response["data"]["items"][0]["quantity"], 2)
        self.assertEqual(response["data"]["items"][0]["amount"], 14.79)
        self.assertEqual(response["data"]["other_fields"][0]["page"], 2)

    def test_request_sends_original_image_not_ocr_boxes(self):
        responses = [{"done": True, "message": {"content": "{}"}},
                     {"done": True, "message": {"content": '{"items":[]}'}}]
        with patch("visual_invoice.request_json", side_effect=responses) as request:
            ask_visual(["FULLPAGE", "TABLECROP"], 1, 1)
        self.assertEqual(request.call_count, 2)
        header = request.call_args_list[0].args[1]
        items = request.call_args_list[1].args[1]
        self.assertEqual(header["messages"][0]["images"], ["FULLPAGE"])
        self.assertEqual(items["messages"][0]["images"], ["FULLPAGE", "TABLECROP"])
        self.assertIn("document_type_ar", header["format"]["properties"])
        self.assertNotIn("items", header["format"]["properties"])
        self.assertNotIn("required", header["format"]["properties"]["supplier"])
        self.assertEqual(list(items["format"]["properties"]), ["items"])
        self.assertEqual(items["model"], "qwen3-vl:4b")

    def test_scope_metrics_convert_nanoseconds_and_keep_missing_values_unknown(self):
        diagnostics = []
        responses = [
            {"load_duration": 2_000_000_000, "prompt_eval_duration": 3_500_000_000,
             "eval_duration": 4_000_000_000, "total_duration": 10_000_000_000,
             "prompt_eval_count": 100, "eval_count": 20, "done_reason": "stop",
             "message": {"content": "{}"}},
            {"message": {"content": '{"items":[]}'}}]
        with patch("visual_invoice.request_json", side_effect=responses):
            ask_visual(["FULL", "CROP"], 2, 3, diagnostics=diagnostics)
        header, items = diagnostics
        self.assertEqual((header["page"], header["scope"], header["image_count"]), (2, "header", 1))
        self.assertEqual(header["model_load_seconds"], 2)
        self.assertEqual(header["prompt_eval_seconds"], 3.5)
        self.assertEqual(header["generation_seconds"], 4)
        self.assertEqual(header["server_total_seconds"], 10)
        self.assertEqual(header["eval_count"], 20)
        self.assertIsNone(header["image_processing_seconds"])
        self.assertEqual(items["image_count"], 2)
        self.assertIsNone(items["model_load_seconds"])
        self.assertGreaterEqual(items["wall_seconds"], 0)

    def test_diagnostics_survive_rejected_visual_rows_on_multiple_pages(self):
        responses = []
        for _ in range(2):
            responses.extend([{"message": {"content": "{}"}, "load_duration": 1_000_000_000},
                              {"message": {"content": '{"items":[]}'}}])
        with patch("visual_invoice.parse_invoice_hybrid", return_value=fallback()), \
             patch("visual_invoice.render_pages", return_value=[(1, 2, ["IMAGE"]), (2, 2, ["IMAGE"])]), \
             patch("visual_invoice.request_json", side_effect=responses), \
             patch("visual_invoice.audit_ai", return_value=([], {})):
            result = parse_invoice_visual("unused.pdf", [], "x.pdf", "eng")
        self.assertEqual(result["quality"]["local_ai_status"], "rejected_unsafe_vision_result")
        self.assertEqual([r["page"] for r in result["vision_diagnostics"]], [1, 1, 2, 2])
        self.assertEqual(result["vision_diagnostics"][0]["model_load_seconds"], 1)

    def test_failed_requests_retain_available_metrics_and_completed_header(self):
        for failure in (TimeoutError("timed out"),
                        {"done_reason": "length", "eval_count": 4096,
                         "eval_duration": 8_000_000_000, "message": {"content": "{}"}}):
            with self.subTest(failure=failure):
                responses = [{"message": {"content": '{"invoice":{"invoice_number":"INV-1"}}'}}, failure]
                with patch("visual_invoice.parse_invoice_hybrid", return_value=fallback()), \
                     patch("visual_invoice.render_pages", return_value=[(1, 1, ["IMAGE"])]), \
                     patch("visual_invoice.request_json", side_effect=responses):
                    result = parse_invoice_visual("unused.pdf", [], "x.pdf", "eng")
                self.assertEqual(result["data"]["invoice"]["invoice_number"], "INV-1")
                header, items = result["vision_diagnostics"]
                self.assertEqual(header["status"], "completed")
                self.assertEqual(items["status"], "failed")
                self.assertIn("visual_ai", result["stage_timings"])
                self.assertGreaterEqual(items["wall_seconds"], 0)
                self.assertEqual(items["generation_seconds"], 8 if isinstance(failure, dict) else None)

    def test_truncated_scope_is_rejected_with_token_count(self):
        with patch("visual_invoice.request_json", return_value={"done": True,
                "done_reason": "length", "eval_count": 4096, "message": {"content": "{}"}}):
            with self.assertRaisesRegex(ValueError, "header output for page 1 was truncated.*4096"):
                ask_visual("FULLPAGE", 1, 1)

    def test_focused_responses_combine_without_requiring_null_keys(self):
        responses = [
            {"done": True, "message": {"content": '{"supplier":{"vat_number":"310981818100003"},"invoice":{"invoice_number":"2690111862"},"customer":{},"totals":{}}'}},
            {"done": True, "message": {"content": '{"items":[{"item_code":"1212","quantity":2,"unit_price":14.79}]}'}}
        ]
        with patch("visual_invoice.request_json", side_effect=responses):
            raw = ask_visual("FULLPAGE", 1, 1)
        data = normalize_full(raw, "9498.pdf", "eng+ara")
        self.assertEqual(data["supplier"]["vat_number"], "310981818100003")
        self.assertEqual(data["items"][0]["quantity"], 2)
        self.assertIsNone(data["customer"]["name"])

    def test_two_pages_keep_all_rows_and_flag_conflicting_totals(self):
        first = normalize_full(visual_raw(), "x.pdf", "eng")
        second = normalize_full(visual_raw(), "x.pdf", "eng", page_number=2)
        second["totals"]["net_amount"] = 110.03
        merged, conflicts = merge_pages([first, second])
        self.assertEqual(len(merged["items"]), 6)
        self.assertEqual([item["line_no"] for item in merged["items"]], list(range(1, 7)))
        self.assertIn("Conflicting totals.net_amount on page 2", conflicts)

    def test_visual_row_order_problem_retains_fallback_for_review(self):
        with patch("visual_invoice.parse_invoice_hybrid", return_value=fallback()), \
             patch("visual_invoice.render_pages", return_value=[(1, 1, "IMAGE")]), \
             patch("visual_invoice.ask_visual", return_value=visual_raw()), \
             patch("visual_invoice.audit_ai", return_value=([{"field": "items.row_order",
                                                               "reason": "swapped"}], {})):
            result = parse_invoice_visual("unused.pdf", [], "9498.pdf", "eng+ara")
        self.assertEqual(result["quality"]["parser"], "spatial_after_visual_review")
        self.assertEqual(result["quality"]["local_ai_status"], "rejected_unsafe_vision_result")
        self.assertEqual(len(result["visual_candidate"]["data"]["items"]), 3)

    def test_visual_result_kept_when_printed_vat_rounding_differs(self):
        with patch("visual_invoice.parse_invoice_hybrid", return_value=fallback()), \
             patch("visual_invoice.render_pages", return_value=[(1, 1, "IMAGE")]), \
             patch("visual_invoice.ask_visual", return_value=visual_raw()), \
             patch("visual_invoice.audit_ai", return_value=([], {})):
            result = parse_invoice_visual("unused.pdf", [], "9498.pdf", "eng+ara")
        self.assertEqual(result["quality"]["parser"], "visual_ai")
        self.assertTrue(result["quality"]["needs_review"])
        self.assertFalse(result["data"]["validation"]["line_vat_sum_matches"])

    def test_incomplete_financial_rows_cannot_be_accepted_as_visual_ai(self):
        raw = visual_raw()
        raw["items"][0].update(quantity=None, unit_price=None, amount=None, vat_amount=14.79)
        with patch("visual_invoice.parse_invoice_hybrid", return_value=fallback()), \
             patch("visual_invoice.render_pages", return_value=[(1, 1, "IMAGE")]), \
             patch("visual_invoice.ask_visual", return_value=raw), \
             patch("visual_invoice.audit_ai", return_value=([], {})):
            result = parse_invoice_visual("unused.pdf", [], "9498.pdf", "eng+ara")
        self.assertEqual(result["quality"]["parser"], "visual_spatial_review")
        self.assertTrue(result["quality"]["needs_review"])
        self.assertIsNone(result["data"]["items"][0]["vat_amount"])

    def test_fast_mode_never_renders_or_calls_model(self):
        with patch("visual_invoice.parse_invoice_hybrid", return_value=fallback()), \
             patch("visual_invoice.render_pages") as render:
            result = parse_invoice_visual("unused.pdf", [], "x.pdf", "eng", mode="fast")
        self.assertEqual(result["quality"]["parser"], "spatial_fast")
        render.assert_not_called()

    def test_failed_vision_is_not_reported_as_extracted(self):
        with patch("visual_invoice.parse_invoice_hybrid", return_value=fallback()), \
             patch("visual_invoice.render_pages", side_effect=OSError("render failed")):
            result = parse_invoice_visual("unused.pdf", [], "x.pdf", "eng")
        self.assertEqual(result["quality"]["local_ai_status"], "failed")
        self.assertTrue(result["quality"]["needs_review"])

    def test_item_failure_keeps_completed_header_with_review(self):
        header = {"supplier": {"name_en": "Example Trading"},
                  "invoice": {"invoice_number": "INV-123"},
                  "customer": {}, "totals": {"subtotal": 94.81}}
        with patch("visual_invoice.parse_invoice_hybrid", return_value=fallback()), \
             patch("visual_invoice.render_pages", return_value=[(1, 1, ["IMAGE"])]), \
             patch("visual_invoice._ask_scope", side_effect=[header, ValueError("items truncated")]):
            result = parse_invoice_visual("unused.pdf", [], "x.pdf", "eng")
        self.assertEqual(result["quality"]["parser"], "spatial_fallback")
        self.assertEqual(result["data"]["invoice"]["invoice_number"], "INV-123")
        self.assertEqual(result["data"]["totals"]["subtotal"], 94.81)
        self.assertTrue(result["quality"]["needs_review"])

    def test_incomplete_visual_rows_are_reconciled_without_inventing_first_row(self):
        raw = visual_raw()
        raw["supplier"]["vat_number"] = None
        raw["invoice"]["invoice_number"] = None
        raw["invoice"]["date"] = "2026-03-09T11:19:38"
        raw["customer"]["vat_number"] = None
        raw["totals"]["subtotal"] = None
        raw["totals"]["vat_rate"] = None
        raw["totals"]["currency"] = None
        raw["vat_summary"]["inc_tax"] = 14.22
        raw["other_fields"] = [{"label": "Invoice Serial", "value": "2690111862"}]
        for item, wrong_vat in zip(raw["items"], (14.79, 50.44, 14.79)):
            item.update(description=None, quantity=None, unit_price=None, amount=None,
                        vat_amount=wrong_vat)
        data = normalize_full(raw, "9498.pdf", "eng+ara")
        spatial = {"supplier": {"vat_number": "310981818100003"},
                   "invoice": {}, "customer": {"vat_number": "300402905100003"},
                   "items": [
                       {"item_code": "1218", "description": "Oil HELIX 4L", "quantity": 1,
                        "unit_price": 50.44, "amount": 50.44, "vat_amount": 7.57,
                        "gross_amount": 58.01},
                       {"item_code": "5007", "description": "Toyota filter", "quantity": 1,
                        "unit_price": 14.79, "amount": 14.79, "vat_amount": 2.22,
                        "gross_amount": 17.01}],
                   "totals": {"vat_rate": 15, "vat_amount": 14.22, "currency": "SAR"}}
        notes = reconcile_with_spatial(data, spatial)
        self.assertTrue(notes)
        self.assertEqual(data["invoice"]["invoice_number"], "2690111862")
        self.assertEqual(data["invoice"]["date"], "2026-03-09")
        self.assertEqual(data["invoice"]["time"], "11:19:38")
        self.assertEqual(data["supplier"]["vat_number"], "310981818100003")
        self.assertEqual(data["customer"]["vat_number"], "300402905100003")
        self.assertEqual(data["totals"]["subtotal"], 94.81)
        self.assertEqual(data["items"][0]["item_code"], "1212")
        self.assertIsNone(data["items"][0]["vat_amount"])
        self.assertIsNone(data["items"][0]["quantity"])
        self.assertEqual(data["items"][1]["vat_amount"], 7.57)
        self.assertEqual(data["items"][2]["vat_amount"], 2.22)


if __name__ == "__main__":
    unittest.main()

"""Pure Stage 1 sweep tests; no Paddle model or PDF OCR is run."""
import unittest

from tools.ocr_param_sweep import (
    RENDER_DPI, SIDE_LIMITS, is_decimal_token, prediction_metrics,
    resolve_side_parameter,
)


class ModernOCR:
    def __init__(self, text_det_limit_side_len=None, **kwargs):
        pass

    def predict(self, image, *, text_det_limit_side_len=None):
        pass


class LegacyOCR:
    def __init__(self, **kwargs):
        pass


class SweepTests(unittest.TestCase):
    def test_grid_is_twelve_runs(self):
        self.assertEqual(len(RENDER_DPI) * len(SIDE_LIMITS), 12)

    def test_modern_parameter_is_explicitly_supported(self):
        self.assertEqual(resolve_side_parameter("3.7.0", ModernOCR, ModernOCR.predict),
                         ("text_det_limit_side_len", "predict"))

    def test_legacy_kwargs_need_package_option_registry(self):
        with self.assertRaisesRegex(RuntimeError, "does not expose"):
            resolve_side_parameter("2.7.0", LegacyOCR, None)
        self.assertEqual(resolve_side_parameter("2.7.0", LegacyOCR, None,
                                                {"det_limit_side_len"}),
                         ("det_limit_side_len", "constructor"))

    def test_decimal_count_accepts_arabic_digits_not_identifiers(self):
        self.assertTrue(is_decimal_token("١٤٫٧٩"))
        self.assertTrue(is_decimal_token("50.44"))
        self.assertFalse(is_decimal_token("1218"))
        self.assertFalse(is_decimal_token("50.44SAR"))

    def test_metrics_preserve_actual_detector_configuration(self):
        raw = [{"res": {"rec_texts": ["١٤٫٧٩", "PCS", "1218"],
                        "rec_scores": [0.9, 0.8, 0.7],
                        "dt_polys": [[[0, 0], [10, 0], [10, 8], [0, 8]],
                                     [[0, 20], [10, 20], [10, 32], [0, 32]]],
                        "text_det_params": {"limit_side_len": 1600, "limit_type": "max"}}}]
        metrics = prediction_metrics(raw, 3)
        self.assertEqual(metrics["detected_boxes"], 2)
        self.assertEqual(metrics["decimal_tokens"], 1)
        self.assertEqual(metrics["mean_confidence"], 80.0)
        self.assertEqual(metrics["median_output_box_height_px"], 10.0)
        self.assertEqual(metrics["reported_text_det_params"]["limit_type"], "max")


if __name__ == "__main__":
    unittest.main()

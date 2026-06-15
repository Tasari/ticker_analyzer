import unittest

import pandas as pd

from ticker_analyzer.data_provider import safe_dict, safe_frame


class DataProviderTest(unittest.TestCase):
    def test_safe_frame_records_provider_failure(self):
        diagnostics = []

        def fail():
            raise RuntimeError("endpoint unavailable")

        result = safe_frame(fail, label="annual income statement", diagnostics=diagnostics)

        self.assertTrue(result.empty)
        self.assertEqual(diagnostics[0]["source"], "annual income statement")
        self.assertEqual(diagnostics[0]["kind"], "provider_error")
        self.assertIn("endpoint unavailable", diagnostics[0]["message"])

    def test_safe_dict_records_network_failure(self):
        diagnostics = []

        def fail():
            raise TimeoutError("request timed out")

        result = safe_dict(fail, label="company info", diagnostics=diagnostics)

        self.assertEqual(result, {})
        self.assertEqual(diagnostics[0]["kind"], "network_error")

    def test_safe_frame_does_not_report_valid_empty_data_as_failure(self):
        diagnostics = []

        result = safe_frame(lambda: pd.DataFrame(), label="earnings estimates", diagnostics=diagnostics)

        self.assertTrue(result.empty)
        self.assertEqual(diagnostics, [])


if __name__ == "__main__":
    unittest.main()

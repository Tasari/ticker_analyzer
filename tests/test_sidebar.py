import unittest

from ticker_analyzer.ui.sidebar import AUTO_ANALYSIS_DELAY_SECONDS, seconds_until_auto_analysis


class SidebarTest(unittest.TestCase):
    def test_auto_analysis_countdown_uses_ten_seconds_since_last_addition(self):
        self.assertEqual(AUTO_ANALYSIS_DELAY_SECONDS, 10)
        self.assertEqual(seconds_until_auto_analysis(None, now=100), 10)
        self.assertEqual(seconds_until_auto_analysis(100, now=100), 10)
        self.assertEqual(seconds_until_auto_analysis(100, now=104.2), 6)
        self.assertEqual(seconds_until_auto_analysis(100, now=110), 0)
        self.assertEqual(seconds_until_auto_analysis(100, now=999), 0)


if __name__ == "__main__":
    unittest.main()

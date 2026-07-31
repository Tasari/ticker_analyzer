import unittest

from ticker_analyzer.cache_keys import analysis_cache_key


class CacheKeyTest(unittest.TestCase):
    def test_calibration_or_config_change_invalidates_cache_key(self):
        base = {"version": 4, "calibration_version": "a", "metrics": {}}
        changed = {**base, "calibration_version": "b"}
        self.assertNotEqual(analysis_cache_key("aapl", "3Y", base), analysis_cache_key("AAPL", "3Y", changed))


if __name__ == "__main__":
    unittest.main()

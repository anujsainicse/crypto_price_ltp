"""Tests for dashboard UI overhaul."""
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def read_file(relative_path):
    with open(os.path.join(PROJECT_ROOT, relative_path), 'r') as f:
        return f.read()

class TestDataCountsFix(unittest.TestCase):
    def test_all_13_prefixes_present(self):
        source = read_file('core/control_interface.py')
        required = [
            'bybit_spot', 'bybit_futures_ob', 'bybit_options',
            'bybit_spot_testnet', 'bybit_futures_testnet',
            'coindcx_spot', 'coindcx_futures',
            'delta_spot', 'delta_futures', 'delta_options',
            'hyperliquid_spot', 'hyperliquid_futures',
            'binance_spot',
        ]
        for prefix in required:
            self.assertIn(f"'{prefix}'", source,
                f"Missing prefix '{prefix}' in get_all_data_counts()")

if __name__ == '__main__':
    unittest.main()

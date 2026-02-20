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

class TestBulkAPIEndpoints(unittest.TestCase):
    def test_services_info_helper_exists(self):
        source = read_file('web_dashboard.py')
        self.assertIn('_get_services_info', source)

    def test_start_all_endpoint(self):
        source = read_file('web_dashboard.py')
        self.assertIn('/api/services/start-all', source)

    def test_stop_all_endpoint(self):
        source = read_file('web_dashboard.py')
        self.assertIn('/api/services/stop-all', source)

    def test_exchange_start_endpoint(self):
        source = read_file('web_dashboard.py')
        self.assertIn('/api/exchange/{exchange_id}/start', source)

    def test_exchange_stop_endpoint(self):
        source = read_file('web_dashboard.py')
        self.assertIn('/api/exchange/{exchange_id}/stop', source)

    def test_web_dashboard_compiles(self):
        import py_compile
        try:
            py_compile.compile(os.path.join(PROJECT_ROOT, 'web_dashboard.py'), doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"Syntax error: {e}")

if __name__ == '__main__':
    unittest.main()

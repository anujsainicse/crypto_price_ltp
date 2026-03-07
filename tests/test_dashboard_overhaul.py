"""Tests for dashboard UI overhaul."""
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def read_file(relative_path):
    with open(os.path.join(PROJECT_ROOT, relative_path), 'r') as f:
        return f.read()

class TestDataCountsFix(unittest.TestCase):
    def test_all_12_prefixes_present(self):
        source = read_file('core/control_interface.py')
        required = [
            'bybit_spot', 'bybit_futures_ob', 'bybit_options',
            'bybit_spot_testnet',
            'coindcx_spot', 'coindcx_futures',
            'delta_spot', 'delta_futures', 'delta_options',
            'hyperliquid_spot', 'hyperliquid_futures',
            'binance_spot',
        ]
        for prefix in required:
            self.assertIn(f"'{prefix}'", source,
                f"Missing prefix '{prefix}' in get_all_data_counts()")

    def test_counts_include_ob_and_trades(self):
        source = read_file('core/control_interface.py')
        self.assertIn('_ob', source, "Should count orderbook keys (_ob)")
        self.assertIn('_trades', source, "Should count trades keys (_trades)")

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

class TestHTMLStructure(unittest.TestCase):
    def test_global_actions_bar(self):
        source = read_file('web/static/index.html')
        self.assertIn('global-actions', source)

    def test_start_all_button(self):
        source = read_file('web/static/index.html')
        self.assertIn('start-all-btn', source)

    def test_stop_all_button(self):
        source = read_file('web/static/index.html')
        self.assertIn('stop-all-btn', source)

    def test_search_input(self):
        source = read_file('web/static/index.html')
        self.assertIn('search-input', source)

    def test_glass_card_class(self):
        source = read_file('web/static/index.html')
        self.assertIn('glass-card', source)

class TestGlassmorphismCSS(unittest.TestCase):
    def test_backdrop_filter(self):
        source = read_file('web/static/style.css')
        self.assertIn('backdrop-filter', source)

    def test_old_gradient_removed(self):
        source = read_file('web/static/style.css')
        self.assertNotIn('#667eea', source, "Old purple gradient should be removed")

    def test_glass_card_style(self):
        source = read_file('web/static/style.css')
        self.assertIn('.glass-card', source)

    def test_pulse_animation(self):
        source = read_file('web/static/style.css')
        self.assertIn('@keyframes pulse', source)

    def test_status_dot(self):
        source = read_file('web/static/style.css')
        self.assertIn('.status-dot', source)

    def test_toast_styles(self):
        source = read_file('web/static/style.css')
        self.assertIn('.toast-container', source)

class TestFrontendJS(unittest.TestCase):
    def test_start_all_api(self):
        source = read_file('web/static/app.js')
        self.assertIn('/api/services/start-all', source)

    def test_stop_all_api(self):
        source = read_file('web/static/app.js')
        self.assertIn('/api/services/stop-all', source)

    def test_exchange_control_api(self):
        source = read_file('web/static/app.js')
        self.assertIn('/api/exchange/', source)

    def test_filter_services(self):
        source = read_file('web/static/app.js')
        self.assertIn('filterServices', source)

    def test_no_alert_calls(self):
        source = read_file('web/static/app.js')
        lines = source.split('\n')
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('//') or stripped.startswith('*'):
                continue
            self.assertNotRegex(stripped, r'\balert\s*\(',
                f"Line {i} uses alert() - should use toast: {stripped}")

    def test_toast_function(self):
        source = read_file('web/static/app.js')
        self.assertIn('showToast', source)

if __name__ == '__main__':
    unittest.main()

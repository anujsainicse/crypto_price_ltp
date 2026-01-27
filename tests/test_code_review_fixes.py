"""Tests for code review fixes - Source code verification.

This test suite verifies all 11 fixes from the code review by inspecting
the source code directly, without requiring runtime dependencies.
"""

import os
import re
import sys
import unittest

# Project root
PROJECT_ROOT = os.getcwd()


def read_file(relative_path):
    """Read a file from the project."""
    full_path = os.path.join(PROJECT_ROOT, relative_path)
    with open(full_path, 'r') as f:
        return f.read()


class TestTimestampFormat(unittest.TestCase):
    """Test Issue #1: Timestamp format should be Unix timestamp string."""

    def test_timestamp_is_unix_format(self):
        """Verify timestamp uses int(time.time()) or datetime.utcnow().timestamp()."""
        source = read_file('core/redis_client.py')

        # Should use int timestamp, not isoformat
        # Allow either legacy or timezone-correct implementation
        has_legacy = "str(int(datetime.utcnow().timestamp()))" in source
        has_modern = "str(int(time.time()))" in source

        self.assertTrue(has_legacy or has_modern, "Should use Unix timestamp (time.time() or datetime)")
        self.assertNotIn("isoformat()", source)

    def test_timestamp_not_iso_format(self):
        """Ensure isoformat() is not used for timestamp."""
        source = read_file('core/redis_client.py')

        # The timestamp line should not contain isoformat
        lines = source.split('\n')
        for line in lines:
            if "'timestamp'" in line and ':' in line:
                self.assertNotIn('isoformat', line, f"Found isoformat in timestamp line: {line}")


class TestRedisScan(unittest.TestCase):
    """Test Issue #3: Redis KEYS replaced with SCAN."""

    def test_get_all_keys_uses_scan(self):
        """Verify get_all_keys uses SCAN instead of KEYS."""
        source = read_file('core/redis_client.py')

        # Find the get_all_keys method
        self.assertIn('def get_all_keys', source)
        self.assertIn('.scan(', source)

        # The method should use cursor-based iteration
        self.assertIn('cursor', source)

    def test_control_interface_uses_get_all_keys(self):
        """Verify control_interface uses get_all_keys instead of keys()."""
        source = read_file('core/control_interface.py')

        # Should use get_all_keys method instead of _client.keys
        self.assertIn('get_all_keys', source)

        # Count occurrences - should have at least 2 uses
        count = source.count('get_all_keys')
        self.assertGreaterEqual(count, 2)


class TestExponentialBackoff(unittest.TestCase):
    """Test Issue #2: Exponential backoff in WebSocket services."""

    def test_backoff_delays_defined(self):
        """Verify backoff_delays are defined in all services."""
        services = [
            'services/bybit_s/spot_service.py',
            'services/delta_f/futures_ltp_service.py',
            'services/delta_o/options_service.py',
            'services/hyperliquid_s/spot_service.py',
            'services/hyperliquid_p/perpetual_service.py',
            'services/coindcx_f/futures_ltp_service.py',
            'services/bybit_spot_testnet/spot_testnet_service.py',
        ]

        expected_delays = "backoff_delays = [5, 10, 20, 40, 60]"

        for service_path in services:
            source = read_file(service_path)
            self.assertIn(expected_delays, source, f"{service_path} should have backoff_delays")

    def test_backoff_used_in_reconnect(self):
        """Verify backoff is used in reconnection logic."""
        services = [
            'services/bybit_s/spot_service.py',
            'services/delta_f/futures_ltp_service.py',
            'services/hyperliquid_s/spot_service.py',
        ]

        for service_path in services:
            source = read_file(service_path)
            # Should calculate delay from backoff_delays
            self.assertIn('self.backoff_delays', source, f"{service_path} should use backoff_delays")
            self.assertIn('min(reconnect_attempts', source, f"{service_path} should cap delay index")


class TestSignalHandler(unittest.TestCase):
    """Test Issue #4: Signal handler race condition fix."""

    def test_base_service_uses_call_soon_threadsafe(self):
        """Verify base_service signal handler is thread-safe."""
        source = read_file('core/base_service.py')

        self.assertIn('call_soon_threadsafe', source)
        self.assertIn('get_running_loop', source)

    def test_manager_uses_call_soon_threadsafe(self):
        """Verify manager signal handler is thread-safe."""
        source = read_file('manager.py')

        self.assertIn('call_soon_threadsafe', source)
        self.assertIn('get_running_loop', source)


class TestManagerDuplicateFix(unittest.TestCase):
    """Test Issue #5: Manager service restart duplicate instances."""

    def test_service_replacement_uses_identity(self):
        """Verify service replacement uses 'is not' for identity comparison."""
        source = read_file('manager.py')

        # Should use 'is not old_service' for identity comparison
        self.assertIn('is not old_service', source)
        self.assertIn('old_service = self.service_registry', source)


class TestWebSocketCleanup(unittest.TestCase):
    """Test Issue #6: WebSocket cleanup on exception."""

    def test_websocket_cleared_after_exception(self):
        """Verify self.websocket = None is in exception handling."""
        services = [
            'services/bybit_s/spot_service.py',
            'services/delta_f/futures_ltp_service.py',
            'services/delta_o/options_service.py',
            'services/hyperliquid_s/spot_service.py',
            'services/hyperliquid_p/perpetual_service.py',
            'services/bybit_spot_testnet/spot_testnet_service.py',
        ]

        for service_path in services:
            source = read_file(service_path)
            # Should clear websocket in exception handler
            self.assertIn('self.websocket = None', source, f"{service_path} should clear websocket")
            # Should be near "Clear stale WebSocket reference" comment
            self.assertIn('Clear stale WebSocket reference', source, f"{service_path} should have cleanup comment")


class TestOptionsSymbolLimit(unittest.TestCase):
    """Test Issue #7: Maximum symbol limit for options service."""

    def test_max_active_symbols_defined(self):
        """Verify max_active_symbols is defined."""
        source = read_file('services/delta_o/options_service.py')

        self.assertIn('max_active_symbols', source)
        self.assertIn("config.get('max_active_symbols'", source)

    def test_symbol_limit_enforced(self):
        """Verify symbol limit is enforced in _filter_symbols."""
        source = read_file('services/delta_o/options_service.py')

        self.assertIn('max_active_symbols', source)
        self.assertIn('selected[:self.max_active_symbols]', source)


class TestInputValidation(unittest.TestCase):
    """Test Issue #8: Input validation for WebSocket messages."""

    def test_math_imported(self):
        """Verify math module is imported for validation."""
        services = [
            'services/bybit_s/spot_service.py',
            'services/delta_s/spot_service.py',
            'services/coindcx_s/spot_service.py',
            'services/delta_f/futures_ltp_service.py',
            'services/delta_o/options_service.py',
            'services/hyperliquid_s/spot_service.py',
            'services/hyperliquid_p/perpetual_service.py',
            'services/coindcx_f/futures_ltp_service.py',
            'services/bybit_spot_testnet/spot_testnet_service.py',
        ]

        for service_path in services:
            source = read_file(service_path)
            self.assertIn('import math', source, f"{service_path} should import math")

    def test_isfinite_validation_used(self):
        """Verify math.isfinite is used for price validation."""
        services = [
            'services/bybit_s/spot_service.py',
            'services/delta_s/spot_service.py',
            'services/coindcx_s/spot_service.py',
            'services/delta_f/futures_ltp_service.py',
            'services/delta_o/options_service.py',
            'services/hyperliquid_s/spot_service.py',
            'services/hyperliquid_p/perpetual_service.py',
            'services/coindcx_f/futures_ltp_service.py',
            'services/bybit_spot_testnet/spot_testnet_service.py',
        ]

        for service_path in services:
            source = read_file(service_path)
            self.assertIn('math.isfinite', source, f"{service_path} should use math.isfinite")


class TestSocketIOPingCancellation(unittest.TestCase):
    """Test Issue #9: Socket.IO ping task cancellation."""

    def test_disconnect_cancels_ping_task(self):
        """Verify disconnect handler cancels ping task."""
        source = read_file('services/coindcx_f/futures_ltp_service.py')

        # Should have ping_task.cancel() in disconnect handler
        self.assertIn('ping_task', source)

        # Find the disconnect handler section
        self.assertIn('async def disconnect():', source)

        # Should cancel ping task in disconnect
        lines = source.split('\n')
        in_disconnect = False
        found_cancel = False
        for line in lines:
            if 'async def disconnect():' in line:
                in_disconnect = True
            if in_disconnect:
                if 'ping_task' in line and 'cancel' in line:
                    found_cancel = True
                    break
                if 'async def ' in line and 'disconnect' not in line:
                    break  # Left disconnect handler

        self.assertTrue(found_cancel, "ping_task.cancel() should be in disconnect handler")


class TestPingTimeout(unittest.TestCase):
    """Test Issue #10: Ping timeout should be 30 seconds."""

    def test_ping_timeout_is_30(self):
        """Verify ping_timeout=30 is used."""
        services = [
            'services/bybit_s/spot_service.py',
            'services/delta_f/futures_ltp_service.py',
            'services/hyperliquid_s/spot_service.py',
            'services/hyperliquid_p/perpetual_service.py',
            'services/bybit_spot_testnet/spot_testnet_service.py',
        ]

        for service_path in services:
            source = read_file(service_path)
            self.assertIn('ping_timeout=30', source, f"{service_path} should have ping_timeout=30")

    def test_ping_timeout_not_10(self):
        """Verify ping_timeout is not 10."""
        services = [
            'services/bybit_s/spot_service.py',
            'services/delta_f/futures_ltp_service.py',
            'services/hyperliquid_s/spot_service.py',
            'services/hyperliquid_p/perpetual_service.py',
            'services/bybit_spot_testnet/spot_testnet_service.py',
        ]

        for service_path in services:
            source = read_file(service_path)
            self.assertNotIn('ping_timeout=10', source, f"{service_path} should not have ping_timeout=10")


class TestPortKillingSafety(unittest.TestCase):
    """Test Issue #11: Process verification before port killing."""

    def test_process_name_verification(self):
        """Verify process name is checked before killing."""
        source = read_file('web_dashboard.py')

        # Should check process name using ps
        self.assertIn("'ps'", source)
        self.assertIn('process_name', source)

        # Should check for python/uvicorn
        self.assertIn('python', source.lower())
        self.assertIn('uvicorn', source.lower())


class TestGitignore(unittest.TestCase):
    """Test Issue #12: .gitignore updates."""

    def test_gitignore_contains_new_entries(self):
        """Verify .gitignore contains recommended additions."""
        content = read_file('.gitignore')

        expected_entries = [
            '.claude/settings.local.json',
            'coindcx_options_discovery/',
            'scripts/',
        ]

        for entry in expected_entries:
            self.assertIn(entry, content, f".gitignore should contain {entry}")


class TestIntegration(unittest.TestCase):
    """Integration tests to verify files compile correctly."""

    def test_all_python_files_compile(self):
        """Verify all modified Python files compile without syntax errors."""
        import py_compile

        files = [
            'core/redis_client.py',
            'core/base_service.py',
            'core/control_interface.py',
            'manager.py',
            'web_dashboard.py',
            'services/bybit_s/spot_service.py',
            'services/delta_f/futures_ltp_service.py',
            'services/delta_o/options_service.py',
            'services/coindcx_f/futures_ltp_service.py',
            'services/hyperliquid_s/spot_service.py',
            'services/hyperliquid_p/perpetual_service.py',
            'services/bybit_spot_testnet/spot_testnet_service.py',
        ]

        for file_path in files:
            full_path = os.path.join(PROJECT_ROOT, file_path)
            try:
                py_compile.compile(full_path, doraise=True)
            except py_compile.PyCompileError as e:
                self.fail(f"Syntax error in {file_path}: {e}")


if __name__ == '__main__':
    # Run tests
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)

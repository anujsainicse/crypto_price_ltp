
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from services.coindcx_s.spot_service import CoinDCXSpotService
import time

class TestCoinDCXReconnection:
    @pytest.fixture
    def service(self):
        config = {
            'enabled': True,
            'symbols': ['BTCUSDT'],
            'websocket_url': 'wss://fake.url',
            'orderbook_enabled': False,
            'trades_enabled': False
        }
        return CoinDCXSpotService(config)

    @pytest.mark.asyncio
    async def test_infinite_reconnection_backoff(self, service):
        """Test that reconnection attempts follow exponential backoff and never stop."""

        # Mock dependencies
        service._connect_and_stream = AsyncMock(side_effect=Exception("Connection failed"))
        service.running = True

        # We need to stop the infinite loop eventually.
        # We'll use a side effect on asyncio.sleep to count calls and stop the service after N retries
        sleep_delays = []

        async def mock_sleep(delay):
            sleep_delays.append(delay)
            if len(sleep_delays) >= 6:
                service.running = False
            return None

        with patch('asyncio.sleep', side_effect=mock_sleep):
            await service.start()

        # Verify backoff pattern: 5, 10, 20, 40, 60, 60...
        expected_delays = [5, 10, 20, 40, 60, 60]
        assert sleep_delays == expected_delays
        # We stop AFTER the 6th sleep, so start() loop terminates before 7th call
        assert service._connect_and_stream.call_count == 6

    @pytest.mark.asyncio
    async def test_reconnection_reset_after_stable_connection(self, service):
        """Test that backoff resets if connection was stable for > 30s."""

        # Use a mutable container for current time to simulate passage of time
        # Start at a high number to avoid any potential 0 issues
        time_state = {'current': 10000.0}

        def mock_time():
            # Every call to time() advances it slightly to handle logging calls
            time_state['current'] += 0.01
            return time_state['current']

        call_count = 0

        async def mock_connect_and_stream():
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                # First call fails immediately
                # Advance time by 1s (less than 30s threshold)
                time_state['current'] += 1.0
                raise Exception("Immediate failure")
            elif call_count == 2:
                # Second call succeeds for 40s (simulated) then fails
                # Advance time by 40s (greater than 30s threshold)
                time_state['current'] += 40.0
                raise Exception("Failure after stable connection")
            else:
                service.running = False
                return

        service._connect_and_stream = mock_connect_and_stream

        # CoinDCX service has a _cleanup_connection method we need to mock or ensure runs safely
        service._cleanup_connection = AsyncMock()

        sleep_delays = []
        async def mock_sleep(delay):
            sleep_delays.append(delay)
            return None

        with patch('asyncio.sleep', side_effect=mock_sleep):
            with patch('time.time', side_effect=mock_time):
                await service.start()

        # Expected:
        # Loop 1: duration ~1s. Attempts -> 1. Delay -> 5s.
        # Loop 2: duration ~40s. Attempts reset to 0, then increment to 1. Delay -> 5s.
        assert sleep_delays == [5, 5]

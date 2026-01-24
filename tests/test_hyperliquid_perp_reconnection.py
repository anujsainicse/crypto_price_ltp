
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from services.hyperliquid_p.perpetual_service import HyperLiquidPerpetualService
import time

class TestHyperLiquidPerpReconnection:
    @pytest.fixture
    def service(self):
        config = {
            'enabled': True,
            'symbols': ['BTC'],
            'websocket_url': 'wss://fake.url'
        }
        return HyperLiquidPerpetualService(config)

    @pytest.mark.asyncio
    async def test_infinite_reconnection_backoff(self, service):
        """Test that reconnection attempts follow exponential backoff and never stop."""
        service._connect_and_stream = AsyncMock(side_effect=Exception("Connection failed"))
        service.running = True

        sleep_delays = []
        async def mock_sleep(delay):
            sleep_delays.append(delay)
            if len(sleep_delays) >= 6:
                service.running = False
            return None

        with patch('asyncio.sleep', side_effect=mock_sleep):
            await service.start()

        expected_delays = [5, 10, 20, 40, 60, 60]
        assert sleep_delays == expected_delays
        assert service._connect_and_stream.call_count == 6

    @pytest.mark.asyncio
    async def test_reconnection_reset_after_stable_connection(self, service):
        """Test that backoff resets if connection was stable for > 30s."""
        time_state = {'current': 10000.0}
        def mock_time():
            time_state['current'] += 0.01
            return time_state['current']

        call_count = 0
        async def mock_connect_and_stream():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                time_state['current'] += 1.0
                raise Exception("Immediate failure")
            elif call_count == 2:
                time_state['current'] += 40.0
                raise Exception("Failure after stable connection")
            else:
                service.running = False

        service._connect_and_stream = mock_connect_and_stream
        sleep_delays = []
        async def mock_sleep(delay):
            sleep_delays.append(delay)
            return None

        with patch('asyncio.sleep', side_effect=mock_sleep):
            with patch('time.time', side_effect=mock_time):
                await service.start()

        # Attempt 1 (fail immediately) -> 5s delay
        # Attempt 2 (stable >30s, reset attempts to 0, then fail) -> 1st attempt again -> 5s delay
        assert sleep_delays == [5, 5]

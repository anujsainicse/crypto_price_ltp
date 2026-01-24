
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from services.delta_o.options_service import DeltaOptionsService
import time

class TestDeltaOptionsReconnection:
    @pytest.fixture
    def service(self):
        config = {
            'enabled': True,
            'symbols': ['C-BTC-100000-241227'],
            'websocket_url': 'wss://fake.url',
            'underlying_assets': ['BTC'],
            'use_dynamic_discovery': False
        }
        return DeltaOptionsService(config)

    @pytest.mark.asyncio
    async def test_infinite_reconnection_backoff(self, service):
        """Test that reconnection attempts follow exponential backoff and never stop."""
        service._connect_and_stream = AsyncMock(side_effect=Exception("Connection failed"))
        # options service calls _discover_symbols in start(), mock it
        service._discover_symbols = AsyncMock(return_value=['C-BTC-100000-241227'])
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
        service._discover_symbols = AsyncMock(return_value=['C-BTC-100000-241227'])

        sleep_delays = []
        async def mock_sleep(delay):
            sleep_delays.append(delay)
            return None

        with patch('asyncio.sleep', side_effect=mock_sleep):
            with patch('time.time', side_effect=mock_time):
                await service.start()

        assert sleep_delays == [5, 5]

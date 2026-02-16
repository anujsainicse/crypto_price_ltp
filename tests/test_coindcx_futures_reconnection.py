
import pytest
import asyncio
from unittest.mock import patch, AsyncMock
from services.coindcx_f.futures_rest_service import CoinDCXFuturesRESTService


class TestCoinDCXFuturesReconnection:
    @pytest.fixture
    def service(self):
        config = {
            'enabled': True,
            'symbols': ['BTCUSDT'],
            'redis_prefix': 'coindcx_futures',
            'redis_ttl': 60,
        }
        return CoinDCXFuturesRESTService(config)

    @pytest.mark.asyncio
    async def test_backoff_follows_exponential_delays(self, service):
        """Test that _handle_backoff produces correct exponential delay sequence."""
        sleep_delays = []
        original_sleep = asyncio.sleep

        async def mock_sleep(delay):
            sleep_delays.append(delay)

        with patch('asyncio.sleep', side_effect=mock_sleep):
            for _ in range(8):
                await service._handle_backoff('ltp', Exception("poll failed"))

        expected_delays = [1, 2, 4, 8, 16, 32, 60, 60]
        assert sleep_delays == expected_delays

    @pytest.mark.asyncio
    async def test_backoff_resets_after_successful_poll(self, service):
        """Test that failures reset to 0 after a successful poll."""
        async def mock_sleep(delay):
            pass

        with patch('asyncio.sleep', side_effect=mock_sleep):
            # Simulate 3 failures
            for _ in range(3):
                await service._handle_backoff('ltp', Exception("poll failed"))

        assert service._backoff_state['ltp']['failures'] == 3

        # Simulate what a successful poll does: reset failures
        service._backoff_state['ltp']['failures'] = 0

        sleep_delays = []

        async def capture_sleep(delay):
            sleep_delays.append(delay)

        with patch('asyncio.sleep', side_effect=capture_sleep):
            await service._handle_backoff('ltp', Exception("poll failed again"))

        # After reset, first failure should use delay index 0 (1s)
        assert sleep_delays == [1]
        assert service._backoff_state['ltp']['failures'] == 1

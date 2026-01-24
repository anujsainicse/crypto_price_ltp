
import pytest
import time
from unittest.mock import MagicMock, patch
from services.hyperliquid_s.spot_service import HyperLiquidSpotService

class TestHyperLiquidTradeID:
    @pytest.fixture
    def service(self):
        config = {
            'enabled': True,
            'symbols': ['BTC'],
            'websocket_url': 'wss://fake.url',
            'trades_enabled': True
        }
        service = HyperLiquidSpotService(config)
        service.redis_client = MagicMock()
        service.redis_client.set_trades_data.return_value = True
        service.logger = MagicMock()
        return service

    @pytest.mark.asyncio
    async def test_trade_id_fallback_logic(self, service):
        """Test that missing hash and time results in a fallback ID, not 'None' string."""
        # Data with missing hash and time
        data = {
            "channel": "trades",
            "data": [
                {"coin": "BTC", "side": "B", "px": "100", "sz": "1"} # No hash, no time
            ]
        }

        # Mock time to ensure deterministic ID
        with patch('time.time', return_value=1700000000.0):
            await service._process_trade_update(data)

        # Check what was sent to Redis
        args, kwargs = service.redis_client.set_trades_data.call_args
        trades = kwargs['trades']

        # Verify ID generation
        assert trades[0]['id'] == "unknown_1700000000000"
        assert trades[0]['id'] != "None"

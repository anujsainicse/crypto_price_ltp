import pytest
from unittest.mock import MagicMock
from services.hyperliquid_s.spot_service import HyperLiquidSpotService

class TestHyperLiquidRedisFailure:
    @pytest.fixture
    def service(self):
        config = {
            'enabled': True,
            'symbols': ['BTC'],
            'websocket_url': 'wss://fake.url',
            'orderbook_enabled': True,
            'trades_enabled': True
        }
        service = HyperLiquidSpotService(config)
        service.redis_client = MagicMock()
        service.logger = MagicMock()
        return service

    @pytest.mark.asyncio
    async def test_redis_failure_logging_mids(self, service):
        service.redis_client.set_price_data.return_value = False
        data = {"channel": "allMids", "data": {"mids": {"BTC": "50000"}}}

        await service._process_mids_update(data)

        # Verify warning logged
        service.logger.warning.assert_called_with("Failed to update price in Redis for BTC")

    @pytest.mark.asyncio
    async def test_redis_failure_logging_orderbook(self, service):
        service.redis_client.set_orderbook_data.return_value = False
        data = {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": 1234567890,
                "levels": [[{"px": "100", "sz": "1"}], [{"px": "101", "sz": "1"}]]
            }
        }

        await service._process_l2book_update(data)

        # Verify warning logged
        service.logger.warning.assert_called_with("Failed to update orderbook in Redis for BTC")

    @pytest.mark.asyncio
    async def test_redis_failure_logging_trades(self, service):
        service.redis_client.set_trades_data.return_value = False
        data = {
            "channel": "trades",
            "data": [{"coin": "BTC", "side": "B", "px": "100", "sz": "1", "time": 1}]
        }

        await service._process_trade_update(data)

        # Verify warning logged
        service.logger.warning.assert_called_with("Failed to update trades in Redis for BTC")

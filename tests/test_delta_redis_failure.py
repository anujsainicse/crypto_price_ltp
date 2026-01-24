import pytest
from unittest.mock import MagicMock
from services.delta_s.spot_service import DeltaSpotService

class TestDeltaSpotRedisFailure:
    @pytest.fixture
    def service(self):
        config = {
            'enabled': True,
            'symbols': ['BTCUSD'],
            'websocket_url': 'wss://fake.url',
            'orderbook_enabled': True,
            'trades_enabled': True
        }
        service = DeltaSpotService(config)
        service.redis_client = MagicMock()
        service.logger = MagicMock()
        return service

    @pytest.mark.asyncio
    async def test_redis_failure_logging_orderbook(self, service):
        service.redis_client.set_orderbook_data.return_value = False
        data = {
            "type": "l2_orderbook",
            "symbol": "BTCUSD",
            "buy": [{"limit_price": "100", "size": "1"}],
            "sell": [{"limit_price": "101", "size": "1"}],
            "last_sequence_no": 123
        }

        await service._process_orderbook_update(data)

        # Verify warning logged
        service.logger.warning.assert_called_with("Failed to update orderbook in Redis for BTC")

    @pytest.mark.asyncio
    async def test_redis_failure_logging_trades(self, service):
        service.redis_client.set_trades_data.return_value = False

        # Test via _store_trades directly as it's called by both snapshot and update
        service._trades["BTCUSD"] = [{"p": 100, "q": 1}]

        await service._store_trades("BTCUSD", "BTC")

        # Verify warning logged
        service.logger.warning.assert_called_with("Failed to update trades in Redis for BTC")

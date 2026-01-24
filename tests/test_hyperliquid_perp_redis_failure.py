import pytest
from unittest.mock import MagicMock
from services.hyperliquid_p.perpetual_service import HyperLiquidPerpetualService

class TestHyperLiquidPerpRedisFailure:
    @pytest.fixture
    def service(self):
        config = {
            'enabled': True,
            'symbols': ['BTC'],
            'websocket_url': 'wss://fake.url'
        }
        service = HyperLiquidPerpetualService(config)
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

import pytest
from unittest.mock import MagicMock, patch
from services.hyperliquid_s.spot_service import HyperLiquidSpotService

@pytest.fixture
def service():
    config = {
        'enabled': True,
        'symbols': ['BTC'],
        'websocket_url': 'wss://fake.url',
        'trades_enabled': True
    }
    service = HyperLiquidSpotService(config)
    service.redis_client = MagicMock()
    service.redis_client.set_trades_data = MagicMock(return_value=True)
    service.logger = MagicMock()
    return service

@pytest.mark.asyncio
async def test_duplicate_ids_in_same_millisecond(service):
    """Test that multiple trades in the same millisecond get unique IDs."""

    # Mock time.time to return a fixed value
    fixed_time = 1700000000.0

    with patch('time.time', return_value=fixed_time):
        # Create a batch of trades without hash or time
        # All these will hit the fallback logic in the same execution context
        data = {
            "channel": "trades",
            "data": [
                {"coin": "BTC", "side": "B", "px": "100", "sz": "1"},
                {"coin": "BTC", "side": "S", "px": "101", "sz": "1"},
                {"coin": "BTC", "side": "B", "px": "102", "sz": "1"}
            ]
        }

        await service._process_trade_update(data)

        # Verify Redis call
        args, kwargs = service.redis_client.set_trades_data.call_args
        trades = kwargs['trades']

        assert len(trades) == 3

        # Collect IDs
        ids = [t['id'] for t in trades]
        print(f"Generated IDs: {ids}")

        # Check for duplicates
        unique_ids = set(ids)
        assert len(unique_ids) == len(ids), f"Duplicate IDs found: {ids}"

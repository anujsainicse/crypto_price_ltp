
import pytest
import asyncio
import time
from unittest.mock import MagicMock, patch
from services.hyperliquid_s.spot_service import HyperLiquidSpotService

@pytest.fixture
def service():
    config = {
        'enabled': True,
        'symbols': ['BTC'],
        'websocket_url': 'wss://fake.url',
        'orderbook_enabled': True,
        'trades_enabled': True
    }
    service = HyperLiquidSpotService(config)
    service.redis_client = MagicMock()
    service.redis_client.set_orderbook_data = MagicMock(return_value=True)
    service.redis_client.set_trades_data = MagicMock(return_value=True)
    service.logger = MagicMock()
    return service

@pytest.mark.asyncio
async def test_orderbook_timestamp_fallback(service):
    """Test that missing timestamp in orderbook update uses fallback."""
    # Mock time.time to return a fixed value
    fixed_time = 1700000000.0
    expected_ts = int(fixed_time * 1000)

    with patch('time.time', return_value=fixed_time):
        # Data without 'time' field
        data = {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                # "time": missing
                "levels": [
                    [{"px": "100", "sz": "1", "n": 1}], # Bids
                    [{"px": "101", "sz": "1", "n": 1}]  # Asks
                ]
            }
        }

        await service._process_l2book_update(data)

        # Verify internal state
        assert service._orderbooks['BTC']['timestamp'] == expected_ts

        # Verify Redis call
        args, kwargs = service.redis_client.set_orderbook_data.call_args

        # update_id should be string of timestamp
        assert kwargs['update_id'] == str(expected_ts)
        assert kwargs['update_id'] != "None"

@pytest.mark.asyncio
async def test_trade_timestamp_fallback(service):
    """Test that missing timestamp in trade update uses fallback."""
    fixed_time = 1700000000.0
    expected_ts = int(fixed_time * 1000)

    with patch('time.time', return_value=fixed_time):
        # Data without 'time' field in trade
        data = {
            "channel": "trades",
            "data": [
                {
                    "coin": "BTC",
                    "side": "B",
                    "px": "100",
                    "sz": "1",
                    # "time": missing
                }
            ]
        }

        await service._process_trade_update(data)

        # Verify Redis call
        args, kwargs = service.redis_client.set_trades_data.call_args
        trades = kwargs['trades']

        assert len(trades) == 1
        assert trades[0]['t'] == expected_ts
        # ID should also use fallback if hash is missing
        assert str(expected_ts) in trades[0]['id']

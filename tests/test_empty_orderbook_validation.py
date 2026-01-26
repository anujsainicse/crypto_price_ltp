
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from services.hyperliquid_s.spot_service import HyperLiquidSpotService
from services.coindcx_s.spot_service import CoinDCXSpotService
from services.delta_s.spot_service import DeltaSpotService

@pytest.fixture
def mock_config():
    return {
        'websocket_url': 'wss://test.url',
        'symbols': ['BTC', 'ETH'],
        'redis_prefix': 'test',
        'redis_ttl': 60,
        'orderbook_enabled': True,
        'orderbook_depth': 50,
        'orderbook_redis_prefix': 'test_ob',
        'trades_enabled': True,
        'trades_limit': 50,
        'trades_redis_prefix': 'test_trades'
    }

@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.set_orderbook_data = MagicMock(return_value=True)
    return redis

@pytest.mark.asyncio
async def test_hyperliquid_empty_orderbook(mock_config, mock_redis):
    """Test that HyperLiquid service ignores empty orderbooks (all invalid prices)."""
    service = HyperLiquidSpotService(mock_config)
    service.redis_client = mock_redis

    # Simulate L2 update with invalid prices (0 or negative)
    # The parser should filter these out, resulting in empty bids/asks
    invalid_data = {
        "channel": "l2Book",
        "data": {
            "coin": "BTC",
            "time": 1234567890,
            "levels": [
                [{"px": "0", "sz": "1.0", "n": 1}, {"px": "-100", "sz": "1.0", "n": 1}], # Bids
                [{"px": "0", "sz": "1.0", "n": 1}, {"px": "-100", "sz": "1.0", "n": 1}]  # Asks
            ]
        }
    }

    await service._process_l2book_update(invalid_data)

    # Redis should NOT be called because parsed bids/asks are empty
    service.redis_client.set_orderbook_data.assert_not_called()

    # Verify state was NOT updated (or is empty/not persisted)
    if "BTC" in service._orderbooks:
        assert service._orderbooks["BTC"]["bids"] == []
        assert service._orderbooks["BTC"]["asks"] == []

@pytest.mark.asyncio
async def test_coindcx_empty_orderbook(mock_config, mock_redis):
    """Test that CoinDCX service ignores empty orderbook snapshots."""
    # Adjust config for CoinDCX format
    config = mock_config.copy()
    config['symbols'] = ['BTCUSDT']

    service = CoinDCXSpotService(config)
    service.redis_client = mock_redis

    # Simulate empty snapshot
    empty_snapshot = {
        "channel": "depth-snapshot",
        "data": {
            "s": "BTCUSDT",
            "bids": {}, # Empty
            "asks": {}, # Empty
            "vs": 123
        }
    }

    await service._process_orderbook_update(empty_snapshot, is_snapshot=True)

    # Redis should NOT be called
    service.redis_client.set_orderbook_data.assert_not_called()

@pytest.mark.asyncio
async def test_delta_empty_orderbook(mock_config, mock_redis):
    """Test that Delta service ignores empty orderbooks."""
    # Adjust config for Delta format
    config = mock_config.copy()
    config['symbols'] = ['BTCUSD']

    service = DeltaSpotService(config)
    service.redis_client = mock_redis

    # Simulate snapshot with invalid orders (will be parsed to empty)
    invalid_data = {
        "type": "l2_orderbook",
        "symbol": "BTCUSD",
        "buy": [{"limit_price": "0", "size": "10"}],
        "sell": [{"limit_price": "0", "size": "10"}],
        "last_sequence_no": 123
    }

    await service._process_orderbook_update(invalid_data)

    # Redis should NOT be called
    service.redis_client.set_orderbook_data.assert_not_called()

@pytest.mark.asyncio
async def test_valid_update_still_works(mock_config, mock_redis):
    """Verify that a VALID HyperLiquid update still works correctly."""
    service = HyperLiquidSpotService(mock_config)
    service.redis_client = mock_redis

    valid_data = {
        "channel": "l2Book",
        "data": {
            "coin": "BTC",
            "time": 1234567890,
            "levels": [
                [{"px": "50000", "sz": "1.0", "n": 1}], # Valid Bid
                [{"px": "50010", "sz": "1.0", "n": 1}]  # Valid Ask
            ]
        }
    }

    await service._process_l2book_update(valid_data)

    # Redis SHOULD be called
    service.redis_client.set_orderbook_data.assert_called_once()

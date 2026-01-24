
import pytest
import asyncio
import json
from unittest.mock import MagicMock, patch, AsyncMock
from services.hyperliquid_s.spot_service import HyperLiquidSpotService

class TestHyperLiquidBugRepro:
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
    async def test_process_l2book_update_with_malformed_items(self, service):
        """Test that _process_l2book_update handles non-dict items in levels without crashing."""
        # Malformed data: levels contain strings instead of dicts
        data = {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": 1234567890,
                "levels": [
                    [{"px": "100", "sz": "1"}, "invalid_item", {"px": "99", "sz": "2"}], # Bids
                    [{"px": "101", "sz": "1"}, 12345, {"px": "102", "sz": "2"}]  # Asks
                ]
            }
        }

        try:
            await service._process_l2book_update(data)
        except AttributeError as e:
            pytest.fail(f"raised AttributeError unexpectedly: {e}")
        except Exception as e:
            pytest.fail(f"raised Exception unexpectedly: {e}")

        # specific verification logic if needed, e.g. check if valid items were processed
        # The mocked redis client should have been called with valid data
        # We expect parsed bids: [[100.0, 1.0], [99.0, 2.0]] (sorted desc)
        # We expect parsed asks: [[101.0, 1.0], [102.0, 2.0]] (sorted asc)

        args, kwargs = service.redis_client.set_orderbook_data.call_args
        assert kwargs['bids'] == [[100.0, 1.0], [99.0, 2.0]]
        assert kwargs['asks'] == [[101.0, 1.0], [102.0, 2.0]]

    @pytest.mark.asyncio
    async def test_process_trade_update_with_malformed_items(self, service):
        """Test that _process_trade_update handles non-dict items in trades list without crashing."""
        data = {
            "channel": "trades",
            "data": [
                {"coin": "BTC", "side": "B", "px": "100", "sz": "1", "time": 1},
                "invalid_string_trade",
                12345,
                {"coin": "BTC", "side": "A", "px": "101", "sz": "1", "time": 2}
            ]
        }

        try:
            await service._process_trade_update(data)
        except AttributeError as e:
            pytest.fail(f"raised AttributeError unexpectedly: {e}")
        except Exception as e:
            pytest.fail(f"raised Exception unexpectedly: {e}")

        # Verify that valid trades were processed
        # We expect 2 valid trades
        redis_call_args = service.redis_client.set_trades_data.call_args
        assert redis_call_args is not None
        trades_arg = redis_call_args[1]['trades']
        assert len(trades_arg) == 2
        assert trades_arg[0]['p'] == 100.0
        assert trades_arg[1]['p'] == 101.0


import pytest
from unittest.mock import MagicMock
from services.hyperliquid_s.spot_service import HyperLiquidSpotService

class TestHyperLiquidBatchValidation:
    @pytest.fixture
    def service(self):
        config = {
            'enabled': True,
            'symbols': ['BTC'],
            'websocket_url': 'wss://fake.url',
            'trades_enabled': True,
            'trades_limit': 50
        }
        service = HyperLiquidSpotService(config)
        service.redis_client = MagicMock()
        service.redis_client.set_trades_data.return_value = True
        service.logger = MagicMock()
        return service

    @pytest.mark.asyncio
    async def test_batch_validation_mixed_symbols(self, service):
        """Test that a batch with mixed valid/invalid symbols processes the valid ones."""
        # 'UNKNOWN' is not in service.symbols, 'BTC' is.
        # Current implementation picks trades_list[0] ('UNKNOWN'), checks if it's in symbols (False), and returns.
        # So 'BTC' trade is lost.
        data = {
            "channel": "trades",
            "data": [
                {"coin": "UNKNOWN", "side": "B", "px": "100", "sz": "1", "time": 1000},
                {"coin": "BTC", "side": "B", "px": "50000", "sz": "0.1", "time": 1001}
            ]
        }

        await service._process_trade_update(data)

        # Check if BTC trades were updated in Redis
        # We expect set_trades_data to be called for BTC
        # In current buggy code, this will fail (call_count == 0)

        # We check calls to set_trades_data
        btc_calls = [
            call for call in service.redis_client.set_trades_data.mock_calls
            if "BTC" in str(call) or (call.kwargs.get('original_symbol') == 'BTC')
        ]

        # If fixed, we should have 1 call for BTC.
        # If buggy, 0 calls.
        assert len(btc_calls) == 1, "Valid BTC trade was dropped because first trade in batch was invalid"

    @pytest.mark.asyncio
    async def test_batch_validation_first_item_malformed(self, service):
        """Test batch where first item is malformed (no coin field)."""
        data = {
            "channel": "trades",
            "data": [
                {"bad_field": "no_coin"},
                {"coin": "BTC", "side": "A", "px": "50100", "sz": "0.2", "time": 1002}
            ]
        }

        await service._process_trade_update(data)

        btc_calls = [
            call for call in service.redis_client.set_trades_data.mock_calls
            if call.kwargs.get('original_symbol') == 'BTC'
        ]
        assert len(btc_calls) == 1, "Valid BTC trade dropped due to malformed first item"

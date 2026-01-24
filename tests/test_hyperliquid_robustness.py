
import unittest
import json
import math
from unittest.mock import MagicMock, AsyncMock
from services.hyperliquid_s.spot_service import HyperLiquidSpotService

class TestHyperLiquidFixes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = {
            'websocket_url': 'wss://api.hyperliquid.xyz/ws',
            'symbols': ['BTC', 'ETH'],
            'redis_prefix': 'hl_spot',
            'orderbook_enabled': True,
            'trades_enabled': True
        }
        self.service = HyperLiquidSpotService(self.config)
        self.service.redis_client = MagicMock()
        self.service.logger = MagicMock()

    async def test_crossed_orderbook_handling(self):
        """Test that crossed orderbooks (Bid >= Ask) are dropped."""
        # Bid 90000 >= Ask 89000 (Crossed)
        data = {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": 1234567890,
                "levels": [
                    [{"px": "90000.0", "sz": "1.0", "n": 1}], # Bids
                    [{"px": "89000.0", "sz": "1.0", "n": 1}]  # Asks
                ]
            }
        }

        await self.service._process_l2book_update(data)

        # Should log warning
        self.service.logger.warning.assert_called()
        args = self.service.logger.warning.call_args[0][0]
        self.assertIn("Crossed book", args)

        # Redis should NOT be called
        self.service.redis_client.set_orderbook_data.assert_not_called()

        # In-memory state should NOT be updated
        self.assertNotIn("BTC", self.service._orderbooks)

    async def test_valid_orderbook_handling(self):
        """Test that valid orderbooks are processed."""
        data = {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": 1234567890,
                "levels": [
                    [{"px": "89000.0", "sz": "1.0", "n": 1}], # Bids
                    [{"px": "90000.0", "sz": "1.0", "n": 1}]  # Asks
                ]
            }
        }

        await self.service._process_l2book_update(data)

        # Redis SHOULD be called
        self.service.redis_client.set_orderbook_data.assert_called_once()

        # Verify in-memory state
        self.assertIn("BTC", self.service._orderbooks)
        self.assertEqual(self.service._orderbooks["BTC"]["bids"][0][0], 89000.0)

    async def test_nan_inf_trade_handling(self):
        """Test that NaN/Inf values in trades are ignored."""
        data = {
            "channel": "trades",
            "data": [
                {"coin": "BTC", "side": "B", "px": "89000.0", "sz": "1.0", "time": 123}, # Valid
                {"coin": "BTC", "side": "B", "px": "Infinity", "sz": "1.0", "time": 124}, # Inf Price
                {"coin": "BTC", "side": "B", "px": "89000.0", "sz": "NaN", "time": 125}, # NaN Size
            ]
        }

        await self.service._process_trade_update(data)

        # Should only have 1 trade in buffer
        self.assertEqual(len(self.service._trades["BTC"]), 1)
        self.assertEqual(self.service._trades["BTC"][0]["t"], 123)

    async def test_nan_inf_orderbook_handling(self):
        """Test that NaN/Inf values in orderbook levels are ignored."""
        data = {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": 1234567890,
                "levels": [
                    # Bids: 1 Valid, 1 Inf, 1 NaN
                    [
                        {"px": "89000.0", "sz": "1.0", "n": 1},
                        {"px": "Infinity", "sz": "1.0", "n": 1},
                        {"px": "88000.0", "sz": "NaN", "n": 1}
                    ],
                    # Asks: 1 Valid
                    [
                        {"px": "90000.0", "sz": "1.0", "n": 1}
                    ]
                ]
            }
        }

        await self.service._process_l2book_update(data)

        # Redis called
        self.service.redis_client.set_orderbook_data.assert_called()

        # Verify call args - Bids should only have 1 entry
        call_args = self.service.redis_client.set_orderbook_data.call_args[1]
        bids = call_args['bids']
        self.assertEqual(len(bids), 1)
        self.assertEqual(bids[0], [89000.0, 1.0])

if __name__ == '__main__':
    unittest.main()

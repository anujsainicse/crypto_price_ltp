import unittest
from unittest.mock import MagicMock, patch
import json
import asyncio
from services.bybit_f.futures_orderbook_service import BybitFuturesOrderbookService

class TestBybitFuturesOrderbookService(unittest.TestCase):
    def setUp(self):
        self.config = {
            'websocket_url': 'wss://stream.bybit.com/v5/public/linear',
            'symbols': ['BTCUSDT'],
            'redis_prefix': 'bybit_futures_ob',
            'orderbook_depth': 50,
            'quote_currencies': ['USDT', 'USDC']
        }
        self.service = BybitFuturesOrderbookService(self.config)
        self.service.redis_client = MagicMock()
        self.service.logger = MagicMock()

    def test_initialization(self):
        self.assertEqual(self.service.service_name, "Bybit-Futures-Orderbook")
        self.assertEqual(self.service.ws_url, 'wss://stream.bybit.com/v5/public/linear')
        self.assertEqual(self.service.symbols, ['BTCUSDT'])
        self.assertEqual(self.service.orderbook_depth, 50)

    def test_extract_base_coin(self):
        self.assertEqual(self.service._extract_base_coin('BTCUSDT'), 'BTC')
        self.assertEqual(self.service._extract_base_coin('ETHUSDC'), 'ETH')
        self.assertEqual(self.service._extract_base_coin('UNKNOWN'), 'UNKNOWN')

    def test_process_orderbook_snapshot(self):
        snapshot_data = {
            'type': 'snapshot',
            'data': {
                's': 'BTCUSDT',
                'b': [['50000', '1.0'], ['49900', '2.0']],
                'a': [['50100', '1.0'], ['50200', '2.0']],
                'u': 12345
            }
        }

        # Run async method synchronously for testing logic
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.service._process_orderbook_update(snapshot_data))

        # Verify Redis call
        self.service.redis_client.set_orderbook_data.assert_called_once()
        call_args = self.service.redis_client.set_orderbook_data.call_args[1]
        self.assertEqual(call_args['key'], 'bybit_futures_ob:BTC')
        self.assertEqual(len(call_args['bids']), 2)
        self.assertEqual(len(call_args['asks']), 2)
        self.assertEqual(call_args['original_symbol'], 'BTCUSDT')
        loop.close()

if __name__ == '__main__':
    unittest.main()

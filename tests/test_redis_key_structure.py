"""Regression tests for Redis key structure.

Verifies that:
1. No service uses the old _extract_base_coin method
2. All 9 modified services use _get_redis_symbol
3. Passthrough services return the full symbol unchanged
4. CoinDCX services strip prefix but preserve quote currency
"""

import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch


def _make_service(cls, config):
    """Create a service instance with mocked Redis.

    Ensures an event loop exists (Python 3.8 asyncio.Event needs one)
    and mocks RedisClient to avoid real connections.
    """
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    with patch('core.base_service.RedisClient') as mock_redis:
        mock_redis.return_value = MagicMock()
        svc = cls(config)
    return svc


class TestNoExtractBaseCoin(unittest.TestCase):
    """Verify no service file contains _extract_base_coin."""

    def test_no_extract_base_coin_in_services(self):
        services_dir = os.path.join(os.path.dirname(__file__), '..', 'services')
        services_dir = os.path.normpath(services_dir)

        for root, _dirs, files in os.walk(services_dir):
            for fname in files:
                if not fname.endswith('.py'):
                    continue
                filepath = os.path.join(root, fname)
                with open(filepath) as f:
                    content = f.read()
                self.assertNotIn(
                    '_extract_base_coin',
                    content,
                    f"Found _extract_base_coin in {filepath}"
                )


class TestAllServicesHaveGetRedisSymbol(unittest.TestCase):
    """Verify all 9 modified services contain _get_redis_symbol."""

    SERVICE_FILES = [
        'bybit_s/spot_service.py',
        'bybit_spot_testnet/spot_testnet_service.py',
        'bybit_f/futures_orderbook_service.py',
        'binance_s/spot_service.py',
        'delta_s/spot_service.py',
        'delta_f/futures_ltp_service.py',
        'coindcx_s/spot_service.py',
        'coindcx_f/futures_rest_service.py',
    ]

    def test_get_redis_symbol_exists(self):
        services_dir = os.path.join(os.path.dirname(__file__), '..', 'services')
        services_dir = os.path.normpath(services_dir)

        for rel_path in self.SERVICE_FILES:
            filepath = os.path.join(services_dir, rel_path)
            with open(filepath) as f:
                content = f.read()
            self.assertIn(
                '_get_redis_symbol',
                content,
                f"Missing _get_redis_symbol in {rel_path}"
            )


class TestPassthroughServices(unittest.TestCase):
    """Verify passthrough services return full symbol unchanged."""

    def _make_config(self, **overrides):
        config = {
            'symbols': ['BTCUSDT'],
            'quote_currencies': ['USDT', 'USDC'],
        }
        config.update(overrides)
        return config

    def test_bybit_spot(self):
        from services.bybit_s.spot_service import BybitSpotService
        svc = _make_service(BybitSpotService, self._make_config())
        self.assertEqual(svc._get_redis_symbol('BTCUSDT'), 'BTCUSDT')
        self.assertEqual(svc._get_redis_symbol('ETHUSDC'), 'ETHUSDC')

    def test_bybit_spot_testnet(self):
        from services.bybit_spot_testnet.spot_testnet_service import BybitSpotTestnetService
        svc = _make_service(BybitSpotTestnetService, self._make_config())
        self.assertEqual(svc._get_redis_symbol('BTCUSDT'), 'BTCUSDT')
        self.assertEqual(svc._get_redis_symbol('ETHUSDC'), 'ETHUSDC')

    def test_bybit_futures_orderbook(self):
        from services.bybit_f.futures_orderbook_service import BybitFuturesOrderbookService
        svc = _make_service(BybitFuturesOrderbookService, self._make_config())
        self.assertEqual(svc._get_redis_symbol('BTCUSDT'), 'BTCUSDT')

    def test_binance_spot(self):
        from services.binance_s.spot_service import BinanceSpotService
        svc = _make_service(BinanceSpotService, self._make_config())
        self.assertEqual(svc._get_redis_symbol('BTCUSDT'), 'BTCUSDT')
        self.assertEqual(svc._get_redis_symbol('ETHBTC'), 'ETHBTC')

    def test_delta_spot(self):
        from services.delta_s.spot_service import DeltaSpotService
        svc = _make_service(DeltaSpotService, self._make_config())
        self.assertEqual(svc._get_redis_symbol('BTCUSD'), 'BTCUSD')

    def test_delta_futures(self):
        from services.delta_f.futures_ltp_service import DeltaFuturesLTPService
        svc = _make_service(DeltaFuturesLTPService, self._make_config())
        self.assertEqual(svc._get_redis_symbol('BTCUSD'), 'BTCUSD')


class TestCoinDCXServices(unittest.TestCase):
    """Verify CoinDCX services strip prefix but preserve quote."""

    def test_coindcx_spot_strip_prefix(self):
        from services.coindcx_s.spot_service import CoinDCXSpotService
        config = {
            'symbols': ['KC-BTC_USDT'],
            'symbol_prefixes': ['KC-', 'B-'],
        }
        svc = _make_service(CoinDCXSpotService, config)
        self.assertEqual(svc._get_redis_symbol('KC-BTC_USDT'), 'BTC_USDT')
        self.assertEqual(svc._get_redis_symbol('KC-ETH_USDT'), 'ETH_USDT')
        self.assertEqual(svc._get_redis_symbol('B-SOL_USDC'), 'SOL_USDC')

    def test_coindcx_spot_no_prefix(self):
        from services.coindcx_s.spot_service import CoinDCXSpotService
        config = {
            'symbols': ['BTC_USDT'],
            'symbol_prefixes': ['KC-', 'B-'],
        }
        svc = _make_service(CoinDCXSpotService, config)
        self.assertEqual(svc._get_redis_symbol('BTC_USDT'), 'BTC_USDT')

    def test_coindcx_futures_strip_prefix(self):
        from services.coindcx_f.futures_rest_service import CoinDCXFuturesRESTService
        config = {
            'symbols': ['B-BTC_USDT'],
            'symbol_prefix': 'B-',
        }
        svc = _make_service(CoinDCXFuturesRESTService, config)
        self.assertEqual(svc._get_redis_symbol('B-BTC_USDT'), 'BTC_USDT')
        self.assertEqual(svc._get_redis_symbol('B-ETH_USDT'), 'ETH_USDT')

    def test_coindcx_futures_no_prefix(self):
        from services.coindcx_f.futures_rest_service import CoinDCXFuturesRESTService
        config = {
            'symbols': ['BTC_USDT'],
            'symbol_prefix': 'B-',
        }
        svc = _make_service(CoinDCXFuturesRESTService, config)
        self.assertEqual(svc._get_redis_symbol('BTC_USDT'), 'BTC_USDT')


if __name__ == '__main__':
    unittest.main()

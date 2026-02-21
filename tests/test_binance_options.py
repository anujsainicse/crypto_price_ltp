"""Tests for BinanceOptionsService.

Covers:
- Symbol discovery and REST API handling
- Symbol filtering logic
- Options symbol parsing
- Ticker / Greeks / IV parsing
- Incremental subscription logic
- Multi-connection batching
- 24-hour reconnect and backoff reset
- asyncio.Lock safety on active_symbols
- Guard against zero max_symbols_per_conn
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.binance_o.options_service import BinanceOptionsService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_service(**overrides):
    config = {
        'enabled': True,
        'websocket_url': 'wss://fake.binance.com',
        'underlying_assets': ['BTC', 'ETH'],
        'max_symbols_per_asset': 5,
        'max_active_symbols': 20,
        'max_streams_per_connection': 9,   # 3 symbols × 3 streams each → 1 connection
        'symbol_refresh_interval': 9999,
        'subscription_batch_size': 50,
        'subscription_batch_delay': 0,
        'orderbook_enabled': False,
        'trades_enabled': False,
    }
    config.update(overrides)
    svc = BinanceOptionsService(config)
    svc.redis_client = MagicMock()
    svc.redis_client.set_price_data.return_value = True
    svc.redis_client.store_orderbook.return_value = True
    svc.redis_client.store_trades.return_value = True
    return svc


SAMPLE_INSTRUMENTS = [
    {'symbol': 'BTC-250328-80000-C', 'underlying': 'BTCUSDT', 'expiryDate': 1743120000000},
    {'symbol': 'BTC-250328-70000-P', 'underlying': 'BTCUSDT', 'expiryDate': 1743120000000},
    {'symbol': 'BTC-250425-90000-C', 'underlying': 'BTCUSDT', 'expiryDate': 1745020800000},
    {'symbol': 'ETH-250328-3000-C',  'underlying': 'ETHUSDT', 'expiryDate': 1743120000000},
    {'symbol': 'SOL-250328-200-C',   'underlying': 'SOLUSDT', 'expiryDate': 1743120000000},
]


# ---------------------------------------------------------------------------
# Symbol discovery
# ---------------------------------------------------------------------------

class TestSymbolDiscovery:
    @pytest.mark.asyncio
    async def test_fetch_symbols_success(self):
        """_fetch_valid_options_symbols returns optionSymbols list on 200."""
        svc = make_service()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'optionSymbols': SAMPLE_INSTRUMENTS})

        # async with session.get(...) as response: needs a MagicMock context manager
        get_cm = MagicMock()
        get_cm.__aenter__ = AsyncMock(return_value=mock_response)
        get_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=get_cm)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await svc._fetch_valid_options_symbols()

        assert result == SAMPLE_INSTRUMENTS

    @pytest.mark.asyncio
    async def test_fetch_symbols_non_200_returns_empty(self):
        """_fetch_valid_options_symbols returns [] on non-200 status."""
        svc = make_service()
        mock_response = AsyncMock()
        mock_response.status = 503

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await svc._fetch_valid_options_symbols()

        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_symbols_timeout_returns_empty(self):
        """_fetch_valid_options_symbols returns [] on timeout."""
        svc = make_service()
        with patch('aiohttp.ClientSession') as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await svc._fetch_valid_options_symbols()
        assert result == []

    @pytest.mark.asyncio
    async def test_discover_symbols_returns_filtered_list(self):
        """_discover_symbols returns a non-empty filtered list."""
        svc = make_service()
        svc._fetch_valid_options_symbols = AsyncMock(return_value=SAMPLE_INSTRUMENTS)

        symbols = await svc._discover_symbols()

        # BTC and ETH are in underlying_assets; SOL is not
        assert any('BTC' in s for s in symbols)
        assert any('ETH' in s for s in symbols)
        assert not any('SOL' in s for s in symbols)

    @pytest.mark.asyncio
    async def test_discover_symbols_empty_rest_returns_empty(self):
        """_discover_symbols returns [] when REST returns nothing."""
        svc = make_service()
        svc._fetch_valid_options_symbols = AsyncMock(return_value=[])
        result = await svc._discover_symbols()
        assert result == []


# ---------------------------------------------------------------------------
# Symbol filtering
# ---------------------------------------------------------------------------

class TestFilterSymbols:
    def test_filters_to_configured_underlyings(self):
        svc = make_service(underlying_assets=['BTC'])
        result = svc._filter_symbols(SAMPLE_INSTRUMENTS)
        assert all('BTC' in s for s in result)
        assert not any('ETH' in s or 'SOL' in s for s in result)

    def test_respects_max_symbols_per_asset(self):
        svc = make_service(underlying_assets=['BTC'], max_symbols_per_asset=2)
        result = svc._filter_symbols(SAMPLE_INSTRUMENTS)
        assert len(result) <= 2

    def test_respects_global_max_active_symbols(self):
        svc = make_service(underlying_assets=['BTC', 'ETH'], max_active_symbols=2)
        result = svc._filter_symbols(SAMPLE_INSTRUMENTS)
        assert len(result) <= 2

    def test_sorts_by_nearest_expiry(self):
        instruments = [
            {'symbol': 'BTC-250425-90000-C', 'underlying': 'BTCUSDT', 'expiryDate': 1745020800000},
            {'symbol': 'BTC-250328-80000-C', 'underlying': 'BTCUSDT', 'expiryDate': 1743120000000},
        ]
        svc = make_service(underlying_assets=['BTC'], max_symbols_per_asset=1)
        result = svc._filter_symbols(instruments)
        # Nearest expiry (smaller timestamp) should be selected
        assert result == ['BTC-250328-80000-C']

    def test_subscribe_all_overrides_max(self):
        svc = make_service(underlying_assets=['BTC'], max_symbols_per_asset=1, subscribe_all=True)
        btc_instruments = [i for i in SAMPLE_INSTRUMENTS if 'BTC' in i['symbol']]
        result = svc._filter_symbols(btc_instruments)
        assert len(result) == len(btc_instruments)

    def test_empty_instruments_returns_empty(self):
        svc = make_service()
        assert svc._filter_symbols([]) == []


# ---------------------------------------------------------------------------
# Symbol parsing
# ---------------------------------------------------------------------------

class TestParseOptionSymbol:
    def test_call_option(self):
        svc = make_service()
        result = svc._parse_option_symbol('BTC-250328-80000-C')
        assert result == {
            'underlying': 'BTC',
            'expiry': '250328',
            'strike': '80000',
            'type': 'CALL',
        }

    def test_put_option(self):
        svc = make_service()
        result = svc._parse_option_symbol('ETH-250328-3000-P')
        assert result['type'] == 'PUT'
        assert result['underlying'] == 'ETH'

    def test_invalid_symbol_returns_defaults(self):
        svc = make_service()
        result = svc._parse_option_symbol('INVALID')
        assert result == {'underlying': '', 'expiry': '', 'strike': '', 'type': 'UNKNOWN'}

    def test_empty_string_returns_defaults(self):
        svc = make_service()
        result = svc._parse_option_symbol('')
        assert result['type'] == 'UNKNOWN'


# ---------------------------------------------------------------------------
# Ticker / Greeks / IV parsing
# ---------------------------------------------------------------------------

class TestProcessTickerUpdate:
    @pytest.mark.asyncio
    async def test_stores_ltp_and_greeks_in_redis(self):
        svc = make_service()
        ticker = {
            'e': 'ticker',
            's': 'BTC-250328-80000-C',
            'c': '1250.50',   # LTP
            'mp': '1248.20',  # mark price
            'bo': '1249.00', 'ao': '1251.00',
            'bq': '10.5',    'aq': '8.2',
            'd': '-0.35',    'g': '0.00012',
            'v': '8.50',     't': '-2.10',
            'vo': '0.65',    'b': '0.62',    'a': '0.68',
            'V': '1234.56',  'h': '1280.00', 'l': '1200.00',
            'P': '2.5',
        }
        await svc._process_ticker_update(ticker)

        svc.redis_client.set_price_data.assert_called_once()
        call_kwargs = svc.redis_client.set_price_data.call_args
        assert call_kwargs.kwargs['price'] == 1250.50
        stored = call_kwargs.kwargs['additional_data']
        assert stored['delta'] == '-0.35'
        assert stored['gamma'] == '0.00012'
        assert stored['iv'] == '0.65'
        assert stored['option_type'] == 'CALL'

    @pytest.mark.asyncio
    async def test_falls_back_to_mark_price_when_no_ltp(self):
        """When 'c' is empty, mark price 'mp' should be used as LTP."""
        svc = make_service()
        ticker = {
            'e': 'ticker',
            's': 'BTC-250328-80000-C',
            'c': '',           # no trade yet
            'mp': '1248.20',
        }
        await svc._process_ticker_update(ticker)

        svc.redis_client.set_price_data.assert_called_once()
        stored_price = svc.redis_client.set_price_data.call_args.kwargs['price']
        assert stored_price == 1248.20

    @pytest.mark.asyncio
    async def test_skips_when_both_c_and_mp_empty(self):
        svc = make_service()
        ticker = {'e': 'ticker', 's': 'BTC-250328-80000-C', 'c': '', 'mp': ''}
        await svc._process_ticker_update(ticker)
        svc.redis_client.set_price_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_negative_price(self):
        svc = make_service()
        ticker = {'e': 'ticker', 's': 'BTC-250328-80000-C', 'c': '-5.0'}
        await svc._process_ticker_update(ticker)
        svc.redis_client.set_price_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_missing_symbol(self):
        svc = make_service()
        await svc._process_ticker_update({'e': 'ticker', 'c': '100.0'})
        svc.redis_client.set_price_data.assert_not_called()


# ---------------------------------------------------------------------------
# Message routing
# ---------------------------------------------------------------------------

class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_routes_ticker_event(self):
        svc = make_service()
        svc._process_ticker_update = AsyncMock()
        msg = json.dumps({'e': 'ticker', 's': 'BTC-250328-80000-C', 'c': '100.0', 'mp': '100.0'})
        await svc._handle_message(msg)
        svc._process_ticker_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_trade_event(self):
        svc = make_service()
        svc._process_trade_update = AsyncMock()
        msg = json.dumps({'e': 'trade', 's': 'BTC-250328-80000-C'})
        await svc._handle_message(msg)
        svc._process_trade_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ignores_subscribe_confirmation(self):
        """SUBSCRIBE confirmation {result: null, id: N} should not crash."""
        svc = make_service()
        svc._process_ticker_update = AsyncMock()
        msg = json.dumps({'result': None, 'id': 1})
        await svc._handle_message(msg)
        svc._process_ticker_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_invalid_json_gracefully(self):
        svc = make_service()
        await svc._handle_message('not valid json {{')  # should not raise


# ---------------------------------------------------------------------------
# Incremental subscription
# ---------------------------------------------------------------------------

class TestIncrementalSubscription:
    @pytest.mark.asyncio
    async def test_sends_subscribe_message_for_new_symbols(self):
        svc = make_service()
        mock_ws = AsyncMock()
        svc._websockets = {0: mock_ws}

        await svc._subscribe_symbols_incremental(['BTC-250328-80000-C'])

        mock_ws.send.assert_awaited_once()
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent['method'] == 'SUBSCRIBE'
        assert 'BTC-250328-80000-C@ticker' in sent['params']

    @pytest.mark.asyncio
    async def test_skips_when_no_active_connections(self):
        svc = make_service()
        svc._websockets = {}
        # Should not raise
        await svc._subscribe_symbols_incremental(['BTC-250328-80000-C'])

    @pytest.mark.asyncio
    async def test_includes_orderbook_stream_when_enabled(self):
        svc = make_service(orderbook_enabled=True, orderbook_depth=5)
        mock_ws = AsyncMock()
        svc._websockets = {0: mock_ws}

        await svc._subscribe_symbols_incremental(['BTC-250328-80000-C'])

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert any('depth' in p for p in sent['params'])

    @pytest.mark.asyncio
    async def test_includes_trades_stream_when_enabled(self):
        svc = make_service(trades_enabled=True)
        mock_ws = AsyncMock()
        svc._websockets = {0: mock_ws}

        await svc._subscribe_symbols_incremental(['BTC-250328-80000-C'])

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert any('@trade' in p for p in sent['params'])


# ---------------------------------------------------------------------------
# Multi-connection batching
# ---------------------------------------------------------------------------

class TestMultiConnectionBatching:
    @pytest.mark.asyncio
    async def test_creates_correct_number_of_connection_tasks(self):
        """4 symbols, max 3 streams/conn, 1 stream/symbol → max_symbols_per_conn=3
        → 2 batches → 2 connection tasks + 1 refresh task = 3 total."""
        svc = make_service(max_streams_per_connection=3)
        svc._discover_symbols = AsyncMock(return_value=[f'BTC-250328-{i}000-C' for i in range(4)])

        captured_tasks = []

        async def fake_gather(*tasks, **kwargs):
            captured_tasks.extend(tasks)
            # Close coroutines to suppress "never awaited" warnings
            for t in tasks:
                if hasattr(t, 'close'):
                    t.close()

        with patch('asyncio.gather', side_effect=fake_gather):
            await svc.start()

        # 2 connection tasks + 1 periodic refresh task
        assert len(captured_tasks) == 3

    @pytest.mark.asyncio
    async def test_returns_early_when_no_symbols(self):
        svc = make_service()
        svc._discover_symbols = AsyncMock(return_value=[])
        # Should return without starting gather
        with patch('asyncio.gather', side_effect=AssertionError("should not be called")):
            await svc.start()  # must not raise

    @pytest.mark.asyncio
    async def test_returns_early_when_max_symbols_per_conn_is_zero(self):
        """max_streams_per_connection=0 → max_symbols_per_conn=0 → early return."""
        svc = make_service(max_streams_per_connection=0)
        svc._discover_symbols = AsyncMock(return_value=['BTC-250328-80000-C'])
        with patch('asyncio.gather', side_effect=AssertionError("should not be called")):
            await svc.start()  # must not raise


# ---------------------------------------------------------------------------
# Reconnection and backoff
# ---------------------------------------------------------------------------

class TestReconnectionBackoff:
    @pytest.mark.asyncio
    async def test_exponential_backoff_on_repeated_failures(self):
        svc = make_service()
        svc._connect_and_stream = AsyncMock(side_effect=Exception("Connection failed"))
        svc.running = True

        sleep_delays = []

        async def mock_sleep(delay):
            sleep_delays.append(delay)
            if len(sleep_delays) >= 5:
                svc.running = False

        with patch('asyncio.sleep', side_effect=mock_sleep):
            with patch('time.time', return_value=0.0):
                await svc._connection_loop(['BTC-250328-80000-C'], 0)

        assert sleep_delays == [5, 10, 20, 40, 60]

    @pytest.mark.asyncio
    async def test_backoff_resets_after_stable_connection(self):
        """If connection was stable >30s, backoff resets to first delay on next failure."""
        svc = make_service()
        svc.running = True
        call_count = 0
        # time.time called twice per iteration: connection_start_time, then duration check
        # iter1: start=0.0, end=0.0 → duration=0 (fast fail) → attempt 1 → sleep(5)
        # iter2: start=0.0, end=40.0 → duration=40 (stable) → attempt 1 → sleep(5)
        time_values = iter([0.0, 0.0, 0.0, 40.0])

        async def mock_connect(*_):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                svc.running = False
            raise Exception("Connection dropped")

        svc._connect_and_stream = mock_connect

        sleep_delays = []

        async def mock_sleep(delay):
            sleep_delays.append(delay)

        # Mock the logger to prevent LogRecord creation from calling time.time()
        # internally (which would consume iterator values before the duration check).
        with patch.object(svc, 'logger'), \
             patch('asyncio.sleep', side_effect=mock_sleep), \
             patch('services.binance_o.options_service.time.time',
                   side_effect=lambda: next(time_values, 40.0)):
            await svc._connection_loop(['BTC-250328-80000-C'], 0)

        # Both failures use backoff[0]=5 because second connection was stable >30s
        assert sleep_delays == [5, 5]


# ---------------------------------------------------------------------------
# asyncio.Lock safety on active_symbols
# ---------------------------------------------------------------------------

class TestActiveSymbolsLock:
    def test_lock_is_created_on_init(self):
        svc = make_service()
        assert hasattr(svc, '_symbols_lock')
        assert isinstance(svc._symbols_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_periodic_refresh_updates_active_symbols(self):
        """After one refresh cycle, active_symbols reflects the new list."""
        svc = make_service()
        svc.running = True
        old = ['BTC-250328-80000-C']
        new = ['BTC-250328-90000-C']
        svc.active_symbols = old
        svc._unsubscribe_symbols = AsyncMock()
        svc._subscribe_symbols_incremental = AsyncMock()

        # Stop the loop after discovery completes (not in sleep, which fires before the update)
        async def discover_and_stop():
            svc.running = False  # stop after this one cycle
            return new

        svc._discover_symbols = AsyncMock(side_effect=discover_and_stop)

        with patch('asyncio.sleep', new_callable=AsyncMock):
            await svc._periodic_symbol_refresh()

        assert svc.active_symbols == new

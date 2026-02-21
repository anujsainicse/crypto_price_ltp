"""Binance European Options Market Data Service.

Provides real-time options data via WebSocket streaming with dynamic symbol discovery.

Key differences from Bybit/Delta options:
- REST discovery via GET /eapi/v1/exchangeInfo (single call, no pagination)
- Symbol format: BTC-250328-80000-C (UNDERLYING-YYMMDD-STRIKE-C|P)
- WebSocket: wss://nbstream.binance.com/eoptions/ws with SUBSCRIBE/UNSUBSCRIBE messages
- Max 200 streams per connection → multi-connection support
- Ticker field 'c' = LTP (empty string if no trades), 'mp' = mark price
- Greeks: d=delta, g=gamma, v=vega, t=theta, vo=IV, b=bid_iv, a=ask_iv
- Depth streams (@depthN) send full snapshots, not deltas
"""

import asyncio
import json
import math
import time
import aiohttp
import websockets
from collections import deque
from typing import Optional, List, Dict, Any

from core.base_service import BaseService


class BinanceOptionsService(BaseService):
    """Service for streaming Binance European Options data via WebSocket.

    Features:
    - Dynamic symbol discovery via eapi REST endpoint (exchangeInfo)
    - Multi-connection support (max 200 streams/connection, configurable)
    - Real-time ticker with full Greeks (delta, gamma, vega, theta, IV)
    - Optional orderbook (full depth snapshots) and trades streaming
    - Periodic symbol refresh for expiring options
    - Exponential backoff auto-reconnection per connection

    Redis Key Patterns:
        Ticker:    {redis_prefix}:{symbol}            e.g. binance_options:BTC-250328-80000-C
        Orderbook: {orderbook_redis_prefix}:{symbol}  e.g. binance_options_ob:BTC-250328-80000-C
        Trades:    {trades_redis_prefix}:{symbol}     e.g. binance_options_trades:BTC-250328-80000-C
    """

    REST_API_URL = "https://eapi.binance.com/eapi/v1/exchangeInfo"
    WS_BASE_URL = "wss://nbstream.binance.com/eoptions/ws"

    def __init__(self, config: dict):
        """Initialize Binance Options Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Binance-Options", config)
        self.config = config

        # API endpoints
        self.ws_url = config.get('websocket_url', self.WS_BASE_URL)
        self.rest_api_url = config.get('rest_api_url', self.REST_API_URL)

        # Symbol filtering
        self.underlying_assets = config.get('underlying_assets', ['BTC', 'ETH'])
        self.subscribe_all = config.get('subscribe_all', False)
        self.max_symbols_per_asset = config.get('max_symbols_per_asset', 100)
        self.max_active_symbols = config.get('max_active_symbols', 500)

        # Connection settings (Binance: max 200 streams per connection)
        self.max_streams_per_connection = config.get('max_streams_per_connection', 180)
        self.subscription_batch_size = config.get('subscription_batch_size', 50)
        self.subscription_batch_delay = config.get('subscription_batch_delay', 0.1)
        self.symbol_refresh_interval = config.get('symbol_refresh_interval', 3600)
        self.reconnect_interval = config.get('reconnect_interval', 5)

        # Redis storage
        self.redis_prefix = config.get('redis_prefix', 'binance_options')
        self.redis_ttl = config.get('redis_ttl', 60)

        # Orderbook (optional)
        self.orderbook_enabled = config.get('orderbook_enabled', False)
        self.orderbook_depth = config.get('orderbook_depth', 20)
        self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'binance_options_ob')

        # Trades (optional)
        self.trades_enabled = config.get('trades_enabled', False)
        self.trades_limit = config.get('trades_limit', 50)
        self.trades_redis_prefix = config.get('trades_redis_prefix', 'binance_options_trades')

        # State management
        self.active_symbols: List[str] = []
        self._symbols_lock = asyncio.Lock()
        self._websockets: Dict[int, Any] = {}  # connection_id -> websocket
        self._trades: Dict[str, deque] = {}
        self._trade_counter = 0
        self._subscribe_id = 0  # Auto-increment for SUBSCRIBE message IDs

        # Exponential backoff: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

    async def _fetch_valid_options_symbols(self) -> List[Dict]:
        """Fetch option instruments from Binance eapi REST API.

        Single call to /eapi/v1/exchangeInfo returns all option symbols.

        Returns:
            List of instrument dicts from optionSymbols array
        """
        try:
            self.logger.info("Fetching options symbols from Binance eapi exchangeInfo...")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.rest_api_url,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        self.logger.error(f"REST API returned status {response.status}")
                        return []

                    data = await response.json()
                    instruments = data.get('optionSymbols', [])
                    self.logger.info(f"Fetched {len(instruments)} options instruments from Binance")
                    return instruments

        except asyncio.TimeoutError:
            self.logger.error("Timeout fetching options symbols from Binance eapi")
            return []
        except aiohttp.ClientError as e:
            self.logger.error(f"HTTP error fetching options symbols: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching options symbols: {e}")
            return []

    def _filter_symbols(self, all_instruments: List[Dict]) -> List[str]:
        """Filter symbols to top N by expiry per underlying asset.

        exchangeInfo does not include OI, so we sort by expiryDate ascending
        (nearest expiry first = most active contracts).

        Args:
            all_instruments: List of instrument dicts from REST API

        Returns:
            List of symbol strings to subscribe to
        """
        selected = []

        # Group by underlying base currency (BTCUSDT → BTC)
        by_underlying: Dict[str, List[Dict]] = {}
        for inst in all_instruments:
            # e.g. "BTCUSDT" → strip known quote to get "BTC"
            underlying_raw = inst.get('underlying', '')
            base = underlying_raw
            for quote in ['USDT', 'USDC', 'BTC', 'ETH', 'USD']:
                if underlying_raw.endswith(quote):
                    base = underlying_raw[:-len(quote)]
                    break

            if base not in by_underlying:
                by_underlying[base] = []
            by_underlying[base].append(inst)

        for base, instruments in by_underlying.items():
            # Only subscribe to configured underlying assets
            if base not in self.underlying_assets:
                continue

            if self.subscribe_all:
                symbols = [i['symbol'] for i in instruments if i.get('symbol')]
                selected.extend(symbols)
                self.logger.info(f"Selected ALL {len(symbols)} options for {base}")
            else:
                # Sort by expiryDate ascending (nearest first = most liquid)
                instruments.sort(key=lambda x: int(x.get('expiryDate', 0)))
                top = instruments[:self.max_symbols_per_asset]
                symbols = [i['symbol'] for i in top if i.get('symbol')]
                selected.extend(symbols)
                self.logger.info(
                    f"Selected {len(symbols)} options for {base} "
                    f"(nearest expiry first, max {self.max_symbols_per_asset})"
                )

        # Enforce global limit
        if len(selected) > self.max_active_symbols:
            self.logger.warning(
                f"Symbol count ({len(selected)}) exceeds limit ({self.max_active_symbols}). Truncating."
            )
            selected = selected[:self.max_active_symbols]

        self.logger.info(f"Total options to subscribe: {len(selected)}")
        return selected

    def _parse_option_symbol(self, symbol: str) -> dict:
        """Parse Binance option symbol into components.

        Binance format: BTC-250328-80000-C
        Parts: UNDERLYING-YYMMDD-STRIKE-C|P

        Args:
            symbol: Option symbol string

        Returns:
            Dict with underlying, expiry, strike, type keys
        """
        try:
            parts = symbol.split('-')
            if len(parts) >= 4:
                return {
                    'underlying': parts[0],
                    'expiry': parts[1],
                    'strike': parts[2],
                    'type': 'CALL' if parts[3] == 'C' else 'PUT'
                }
        except Exception as e:
            self.logger.debug(f"Error parsing option symbol {symbol}: {e}")

        return {'underlying': '', 'expiry': '', 'strike': '', 'type': 'UNKNOWN'}

    async def _discover_symbols(self) -> List[str]:
        """Discover and filter options symbols.

        Returns:
            List of symbol strings to subscribe to
        """
        self.logger.info("Discovering Binance options symbols...")
        all_instruments = await self._fetch_valid_options_symbols()

        if all_instruments:
            symbols = self._filter_symbols(all_instruments)
            if symbols:
                return symbols
            self.logger.warning("No symbols matched filter criteria")

        self.logger.error("Failed to discover Binance options symbols")
        return []

    async def start(self):
        """Start the Binance options streaming service with multi-connection support."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        self.active_symbols = await self._discover_symbols()

        if not self.active_symbols:
            self.logger.error("No valid options symbols to subscribe to")
            return

        self.running = True
        total = len(self.active_symbols)
        self.logger.info(f"Starting Binance Options service: {total} symbols")
        if total <= 10:
            self.logger.info(f"Symbols: {', '.join(self.active_symbols)}")
        else:
            self.logger.info(f"First 5 symbols: {', '.join(self.active_symbols[:5])}...")

        # Streams per symbol depends on enabled features
        streams_per_symbol = 1  # ticker always
        if self.orderbook_enabled:
            streams_per_symbol += 1
        if self.trades_enabled:
            streams_per_symbol += 1

        # Max symbols per connection = floor(max_streams / streams_per_symbol)
        max_symbols_per_conn = self.max_streams_per_connection // streams_per_symbol

        if max_symbols_per_conn <= 0:
            self.logger.error(
                f"Invalid stream configuration: max_streams_per_connection={self.max_streams_per_connection} "
                f"yields max_symbols_per_conn={max_symbols_per_conn}"
            )
            return

        # Split into connection batches
        batches = [
            self.active_symbols[i:i + max_symbols_per_conn]
            for i in range(0, total, max_symbols_per_conn)
        ]
        self.logger.info(f"Using {len(batches)} WebSocket connection(s) (max {max_symbols_per_conn} symbols each)")

        # Run all connection loops + periodic refresh concurrently
        tasks = [self._connection_loop(batch, i) for i, batch in enumerate(batches)]
        tasks.append(self._periodic_symbol_refresh())

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _connection_loop(self, symbols_batch: List[str], connection_id: int):
        """Reconnect loop for a single WebSocket connection batch.

        Args:
            symbols_batch: Symbols assigned to this connection
            connection_id: Numeric ID for this connection
        """
        reconnect_attempts = 0

        while self.running:
            try:
                connection_start_time = time.time()
                await self._connect_and_stream(symbols_batch, connection_id)
                reconnect_attempts = 0  # Reset on successful stable connection
            except Exception as e:
                # Don't escalate backoff if connection was stable for >30s
                connection_duration = time.time() - connection_start_time
                if connection_duration > 30:
                    reconnect_attempts = 1
                else:
                    reconnect_attempts += 1

                self._websockets.pop(connection_id, None)
                self.logger.warning(
                    f"[Conn-{connection_id}] Connection error (attempt {reconnect_attempts}): {e}"
                )

                delay = self.backoff_delays[min(reconnect_attempts - 1, len(self.backoff_delays) - 1)]
                self.logger.info(f"[Conn-{connection_id}] Reconnecting in {delay}s...")
                await asyncio.sleep(delay)

    async def _connect_and_stream(self, symbols_batch: List[str], connection_id: int):
        """Connect to WebSocket and stream data for this batch of symbols.

        Args:
            symbols_batch: Symbols for this connection
            connection_id: Numeric ID for logging
        """
        # Clear stale Redis keys on reconnect to avoid serving data from the previous connection.
        # Orderbook: full snapshots are rebuilt on next message, so stale keys must go.
        if self.orderbook_enabled:
            for symbol in symbols_batch:
                self.redis_client.delete_key(f"{self.orderbook_redis_prefix}:{symbol}")

        # Trades: also clear in-memory buffer so old trades don't bleed into the new connection.
        if self.trades_enabled:
            for symbol in symbols_batch:
                self._trades.pop(symbol, None)
                self.redis_client.delete_key(f"{self.trades_redis_prefix}:{symbol}")

        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=30
        ) as websocket:
            self._websockets[connection_id] = websocket
            self.logger.info(
                f"[Conn-{connection_id}] WebSocket connected ({len(symbols_batch)} symbols)"
            )

            await self._subscribe_to_channels(websocket, symbols_batch, connection_id)

            async for message in websocket:
                if not self.running:
                    break
                try:
                    await self._handle_message(message)
                except Exception as e:
                    self.logger.error(f"[Conn-{connection_id}] Error handling message: {e}")

    async def _subscribe_to_channels(
        self,
        ws,
        symbols_batch: List[str],
        connection_id: int
    ):
        """Build and send SUBSCRIBE messages for ticker (and optionally depth/trades).

        Sends in sub-batches of subscription_batch_size to respect rate limits.

        Args:
            ws: Active WebSocket connection
            symbols_batch: Symbols to subscribe for this connection
            connection_id: Numeric ID for logging
        """
        # Build full params list
        all_params = []
        for symbol in symbols_batch:
            all_params.append(f"{symbol}@ticker")
            if self.orderbook_enabled:
                all_params.append(f"{symbol}@depth{self.orderbook_depth}")
            if self.trades_enabled:
                all_params.append(f"{symbol}@trade")

        total_params = len(all_params)
        total_batches = (total_params + self.subscription_batch_size - 1) // self.subscription_batch_size

        self.logger.info(
            f"[Conn-{connection_id}] Subscribing to {total_params} streams in {total_batches} batches"
        )

        for i in range(0, total_params, self.subscription_batch_size):
            batch = all_params[i:i + self.subscription_batch_size]
            self._subscribe_id += 1
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": batch,
                "id": self._subscribe_id
            }
            await ws.send(json.dumps(subscribe_msg))

            batch_num = i // self.subscription_batch_size + 1
            self.logger.info(
                f"[Conn-{connection_id}] Sent subscribe batch {batch_num}/{total_batches}: "
                f"{len(batch)} streams"
            )

            # Delay between batches (but not after the last one)
            if i + self.subscription_batch_size < total_params:
                await asyncio.sleep(self.subscription_batch_delay)

        self.logger.info(f"[Conn-{connection_id}] Subscription complete: {len(symbols_batch)} symbols")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message.

        Routes by event type ('e' field) in the raw event.
        SUBSCRIBE confirmations have {"result": null, "id": N} format.

        Args:
            message: Raw WebSocket message string
        """
        try:
            data = json.loads(message)

            # SUBSCRIBE/UNSUBSCRIBE confirmation: {"result": null, "id": N}
            if 'result' in data and 'id' in data:
                if data['result'] is None:
                    self.logger.debug(f"Subscription confirmed (id={data['id']})")
                else:
                    self.logger.warning(f"Subscription response with result: {data}")
                return

            # Route by event type
            event_type = data.get('e', '')

            if event_type == 'ticker':
                await self._process_ticker_update(data)
            elif event_type in ('depthUpdate', 'depth'):
                await self._process_orderbook_update(data)
            elif event_type == 'trade':
                await self._process_trade_update(data)
            else:
                self.logger.debug(f"Received unknown event type: {event_type or 'N/A'}")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}", exc_info=True)

    async def _process_ticker_update(self, data: dict):
        """Process options ticker update and store in Redis.

        Ticker fields:
          s=symbol, c=close/LTP ('' if no trades), mp=mark_price,
          bo=bid, ao=ask, bq=bid_qty, aq=ask_qty,
          d=delta, g=gamma, v=vega, t=theta, vo=mark_iv, b=bid_iv, a=ask_iv,
          V=volume_24h, h=high_24h, l=low_24h, P=price_change_pct

        Args:
            data: Ticker event data
        """
        try:
            symbol = data.get('s')
            if not symbol:
                return

            # LTP from close price 'c'; falls back to mark price if no trades yet
            price_raw = data.get('c', '')
            if not price_raw:
                price_raw = data.get('mp', '')
            if not price_raw:
                return

            try:
                price_float = float(price_raw)
                if not math.isfinite(price_float) or price_float < 0:
                    return
            except (ValueError, TypeError):
                return

            option_info = self._parse_option_symbol(symbol)
            redis_key = f"{self.redis_prefix}:{symbol}"

            additional_data = {
                'mark_price': str(data.get('mp', '0') or '0'),
                'bid': str(data.get('bo', '0') or '0'),
                'ask': str(data.get('ao', '0') or '0'),
                'bid_size': str(data.get('bq', '0') or '0'),
                'ask_size': str(data.get('aq', '0') or '0'),
                # Greeks
                'delta': str(data.get('d', '0') or '0'),
                'gamma': str(data.get('g', '0') or '0'),
                'vega': str(data.get('v', '0') or '0'),
                'theta': str(data.get('t', '0') or '0'),
                # Implied volatility
                'iv': str(data.get('vo', '0') or '0'),
                'bid_iv': str(data.get('b', '0') or '0'),
                'ask_iv': str(data.get('a', '0') or '0'),
                # 24h market stats
                'volume_24h': str(data.get('V', '0') or '0'),
                'high_24h': str(data.get('h', '0') or '0'),
                'low_24h': str(data.get('l', '0') or '0'),
                'price_change_percent': str(data.get('P', '0') or '0'),
                # Option metadata
                'option_type': option_info.get('type', 'UNKNOWN'),
                'underlying': option_info.get('underlying', ''),
                'strike_price': option_info.get('strike', ''),
                'expiry_date': option_info.get('expiry', ''),
            }

            success = self.redis_client.set_price_data(
                key=redis_key,
                price=price_float,
                symbol=symbol,
                additional_data=additional_data,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"[REDIS] Stored {symbol}: ${price_float:.4f} "
                    f"(Type: {option_info.get('type')}, Strike: {option_info.get('strike')}, "
                    f"Delta: {data.get('d', 'N/A')}, IV: {data.get('vo', 'N/A')})"
                )
            else:
                self.logger.warning(f"Failed to store {symbol} in Redis")

        except Exception as e:
            self.logger.error(f"Error processing ticker update: {e}")

    async def _process_orderbook_update(self, data: dict):
        """Process depth snapshot and store in Redis.

        Binance eOptions @depthN streams send full snapshots (not deltas).

        Depth fields: s=symbol, b=bids [[price,qty],...], a=asks, u=update_id

        Args:
            data: Depth event data
        """
        if not self.orderbook_enabled:
            return

        try:
            symbol = data.get('s', '')
            if not symbol:
                return

            raw_bids = data.get('b', [])
            raw_asks = data.get('a', [])

            def parse_levels(raw: list) -> List[List[float]]:
                """Parse [[price_str, qty_str], ...] into [[float, float], ...]."""
                parsed = []
                for entry in raw:
                    if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                        continue
                    try:
                        price = float(entry[0])
                        qty = float(entry[1])
                        if math.isfinite(price) and math.isfinite(qty) and price > 0 and qty > 0:
                            parsed.append([price, qty])
                    except (ValueError, TypeError):
                        continue
                return parsed

            bids = sorted(
                parse_levels(raw_bids),
                key=lambda x: x[0],
                reverse=True
            )[:self.orderbook_depth]

            asks = sorted(
                parse_levels(raw_asks),
                key=lambda x: x[0]
            )[:self.orderbook_depth]

            if not bids or not asks:
                return

            best_bid = bids[0][0]
            best_ask = asks[0][0]
            spread = best_ask - best_bid

            if spread < 0:
                self.logger.warning(f"Crossed book for {symbol}: spread={spread:.4f}")
                # Remove stale Redis data to avoid serving bad orderbook
                self.redis_client.delete_key(f"{self.orderbook_redis_prefix}:{symbol}")
                return

            mid_price = (best_bid + best_ask) / 2

            redis_key = f"{self.orderbook_redis_prefix}:{symbol}"
            success = self.redis_client.set_orderbook_data(
                key=redis_key,
                bids=bids,
                asks=asks,
                spread=spread,
                mid_price=mid_price,
                update_id=data.get('u', 0),
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated orderbook {symbol}: {len(bids)} bids, {len(asks)} asks, "
                    f"spread={spread:.4f}, mid={mid_price:.4f}"
                )

        except Exception as e:
            self.logger.error(f"Error processing orderbook update: {e}")

    async def _process_trade_update(self, data: dict):
        """Process real-time trade update and store in Redis.

        Trade fields: s=symbol, t=tradeId, p=price, q=qty, S=side(BUY/SELL), T=timestamp_ms

        Args:
            data: Trade event data
        """
        if not self.trades_enabled:
            return

        try:
            symbol = data.get('s', '')
            if not symbol:
                return

            if symbol not in self._trades:
                self._trades[symbol] = deque(maxlen=self.trades_limit)

            try:
                price = float(data.get('p', 0))
                qty = float(data.get('q', 0))
            except (ValueError, TypeError):
                return

            if price <= 0 or qty <= 0 or not math.isfinite(price) or not math.isfinite(qty):
                return

            timestamp = data.get('T', int(time.time() * 1000))
            self._trade_counter += 1
            trade_id = str(data.get('t', f"{timestamp}_{self._trade_counter}"))

            # Binance side: "BUY" or "SELL"
            side_raw = data.get('S', 'BUY')
            side = 'Buy' if side_raw == 'BUY' else 'Sell'

            self._trades[symbol].append({
                'p': price,
                'q': qty,
                's': side,
                't': timestamp,
                'id': trade_id
            })

            redis_key = f"{self.trades_redis_prefix}:{symbol}"
            self.redis_client.set_trades_data(
                key=redis_key,
                trades=list(self._trades[symbol]),
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            self.logger.debug(
                f"Updated {symbol} trades: {len(self._trades[symbol])} trades in buffer"
            )

        except Exception as e:
            self.logger.error(f"Error processing trade update: {e}")

    async def _periodic_symbol_refresh(self):
        """Periodically refresh symbols to handle expiring options."""
        while self.running:
            try:
                await asyncio.sleep(self.symbol_refresh_interval)

                if not self.running:
                    break

                self.logger.info("Refreshing Binance options symbols...")
                new_symbols = await self._discover_symbols()

                if not new_symbols:
                    self.logger.warning("Symbol refresh returned empty, keeping existing symbols")
                    continue

                async with self._symbols_lock:
                    old_set = set(self.active_symbols)
                new_set = set(new_symbols)
                to_unsubscribe = old_set - new_set
                to_subscribe = new_set - old_set

                if to_unsubscribe:
                    self.logger.info(f"Unsubscribing from {len(to_unsubscribe)} expired symbols")
                    await self._unsubscribe_symbols(list(to_unsubscribe))
                    for symbol in to_unsubscribe:
                        self._trades.pop(symbol, None)

                if to_subscribe:
                    self.logger.info(f"Subscribing to {len(to_subscribe)} new symbols")
                    await self._subscribe_symbols_incremental(list(to_subscribe))

                async with self._symbols_lock:
                    self.active_symbols = new_symbols
                self.logger.info(
                    f"Symbol refresh complete. Now tracking {len(self.active_symbols)} symbols"
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error during symbol refresh: {e}")

    async def _subscribe_symbols_incremental(self, symbols: List[str]):
        """Subscribe to new symbols via first available WebSocket connection.

        Args:
            symbols: New symbols to subscribe to
        """
        if not self._websockets:
            self.logger.warning("No active connections for incremental subscribe")
            return

        ws = next(iter(self._websockets.values()))
        params = []
        for symbol in symbols:
            params.append(f"{symbol}@ticker")
            if self.orderbook_enabled:
                params.append(f"{symbol}@depth{self.orderbook_depth}")
            if self.trades_enabled:
                params.append(f"{symbol}@trade")

        self._subscribe_id += 1
        msg = {
            "method": "SUBSCRIBE",
            "params": params,
            "id": self._subscribe_id
        }
        try:
            await ws.send(json.dumps(msg))
            self.logger.debug(f"Subscribed to {len(symbols)} new symbols incrementally")
        except Exception as e:
            self.logger.error(f"Error in incremental subscribe: {e}")

    async def _unsubscribe_symbols(self, symbols: List[str]):
        """Unsubscribe from symbols on all active connections.

        Args:
            symbols: Symbols to unsubscribe from
        """
        params = []
        for symbol in symbols:
            params.append(f"{symbol}@ticker")
            if self.orderbook_enabled:
                params.append(f"{symbol}@depth{self.orderbook_depth}")
            if self.trades_enabled:
                params.append(f"{symbol}@trade")

        for conn_id, ws in list(self._websockets.items()):
            try:
                self._subscribe_id += 1
                msg = {
                    "method": "UNSUBSCRIBE",
                    "params": params,
                    "id": self._subscribe_id
                }
                await ws.send(json.dumps(msg))
                self.logger.debug(f"[Conn-{conn_id}] Sent unsubscribe for {len(symbols)} symbols")
            except Exception as e:
                self.logger.error(f"[Conn-{conn_id}] Error sending unsubscribe: {e}")

    async def stop(self):
        """Stop the service and close all WebSocket connections."""
        self.running = False

        for conn_id, ws in list(self._websockets.items()):
            try:
                await ws.close()
                self.logger.info(f"[Conn-{conn_id}] WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"[Conn-{conn_id}] Error closing WebSocket: {e}")

        self._websockets.clear()
        self._trades.clear()
        self.logger.info("Binance Options Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('binance')
    service_config = config.get('services', {}).get('options', {})

    if not service_config:
        print("No options configuration found in exchanges.yaml for binance")
        return

    service = BinanceOptionsService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

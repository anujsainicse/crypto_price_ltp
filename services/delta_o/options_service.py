"""Delta Exchange Options Service."""

import asyncio
import json
import math
import time
import websockets
import aiohttp
from collections import deque
from typing import Optional, List, Dict, Any
from datetime import datetime

from core.base_service import BaseService


class DeltaOptionsService(BaseService):
    """Service for streaming Delta Exchange options data via WebSocket.

    Redis Key Patterns:
        Ticker:    {redis_prefix}:{symbol} (Hash) - e.g., delta_options:C-BTC-106000-241220
        Orderbook: {orderbook_redis_prefix}:{symbol} (Hash) - e.g., delta_options_ob:C-BTC-106000-241220
        Trades:    {trades_redis_prefix}:{symbol} (Hash) - e.g., delta_options_trades:C-BTC-106000-241220
    """

    # REST API endpoint for fetching tickers (using India API for more options)
    REST_API_URL = "https://api.india.delta.exchange/v2/tickers"

    def __init__(self, config: dict):
        """Initialize Delta Options Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Delta-Options", config)
        self.config = config  # Store full config for later use
        self.ws_url = config.get('websocket_url', 'wss://socket.delta.exchange')
        # Static symbols from config (fallback)
        self.static_symbols = config.get('symbols', [])
        # Underlying assets to track
        self.underlying_assets = config.get('underlying_assets', ['BTC', 'ETH'])
        # Whether to subscribe to ALL options (no filtering)
        self.subscribe_all = config.get('subscribe_all', True)
        # Max symbols per underlying asset (only used if subscribe_all is False)
        self.max_symbols_per_asset = config.get('max_symbols_per_asset', 10)
        # Whether to use dynamic symbol discovery
        self.use_dynamic_discovery = config.get('use_dynamic_discovery', True)
        # Batch subscription settings
        self.subscription_batch_size = config.get('subscription_batch_size', 20)
        self.subscription_batch_delay = config.get('subscription_batch_delay', 0.5)
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.redis_prefix = config.get('redis_prefix', 'delta_options')
        self.redis_ttl = config.get('redis_ttl', 60)

        # Orderbook and trades feature flags
        self.orderbook_enabled = config.get('orderbook_enabled', False)
        self.trades_enabled = config.get('trades_enabled', False)
        self.orderbook_depth = config.get('orderbook_depth', 50)
        self.trades_limit = config.get('trades_limit', 50)

        # Redis prefixes for orderbook and trades
        self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'delta_options_ob')
        self.trades_redis_prefix = config.get('trades_redis_prefix', 'delta_options_trades')

        # In-memory state for orderbook and trades
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._trades: Dict[str, deque] = {}
        self._trade_counter = 0

        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        # Exponential backoff delays as per CLAUDE.md: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]
        # Active symbols (will be populated dynamically or from config)
        self.active_symbols: List[str] = []
        # Maximum symbols to track to prevent unbounded memory growth
        self.max_active_symbols = config.get('max_active_symbols', 500)
        # Symbol refresh interval (1 hour)
        self.symbol_refresh_interval = config.get('symbol_refresh_interval', 3600)

    async def _fetch_valid_options_symbols(self) -> List[Dict]:
        """Fetch currently active options symbols from Delta Exchange REST API.

        Returns:
            List of option ticker dictionaries with symbol info
        """
        try:
            self.logger.info("Fetching valid options symbols from Delta Exchange API...")
            params = {"contract_types": "call_options,put_options"}

            async with aiohttp.ClientSession() as session:
                async with session.get(self.REST_API_URL, params=params, timeout=30) as response:
                    if response.status != 200:
                        self.logger.error(f"REST API returned status {response.status}")
                        return []

                    data = await response.json()

                    if not data.get('success', False):
                        self.logger.error(f"REST API returned error: {data.get('error', 'Unknown error')}")
                        return []

                    result = data.get('result', [])
                    self.logger.info(f"Fetched {len(result)} options contracts from Delta Exchange")
                    return result

        except asyncio.TimeoutError:
            self.logger.error("Timeout fetching options symbols from REST API")
            return []
        except aiohttp.ClientError as e:
            self.logger.error(f"HTTP error fetching options symbols: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching options symbols: {e}")
            return []

    def _filter_symbols(self, all_tickers: List[Dict]) -> List[str]:
        """Filter and select options symbols to track.

        Args:
            all_tickers: List of ticker data from REST API

        Returns:
            List of symbol strings to subscribe to
        """
        selected = []

        for underlying in self.underlying_assets:
            # Filter options for this underlying
            # The API returns 'underlying_asset_symbol' as a direct field
            underlying_options = [
                t for t in all_tickers
                if t.get('underlying_asset_symbol', '') == underlying
            ]

            if not underlying_options:
                self.logger.warning(f"No options found for underlying: {underlying}")
                continue

            # Count calls and puts
            calls = [t for t in underlying_options if t.get('symbol', '').startswith('C-')]
            puts = [t for t in underlying_options if t.get('symbol', '').startswith('P-')]

            if self.subscribe_all:
                # Subscribe to ALL options for this underlying
                selected_calls = [t['symbol'] for t in calls]
                selected_puts = [t['symbol'] for t in puts]
            else:
                # Sort by open interest (most liquid first)
                calls.sort(key=lambda x: float(x.get('oi', 0) or 0), reverse=True)
                puts.sort(key=lambda x: float(x.get('oi', 0) or 0), reverse=True)

                # Take top N symbols (balanced calls and puts)
                max_each = self.max_symbols_per_asset // 2
                selected_calls = [t['symbol'] for t in calls[:max_each]]
                selected_puts = [t['symbol'] for t in puts[:max_each]]

            selected.extend(selected_calls)
            selected.extend(selected_puts)

            self.logger.info(
                f"Selected {len(selected_calls)} calls and {len(selected_puts)} puts for {underlying}"
            )

        # Enforce maximum symbol limit to prevent unbounded memory growth
        if len(selected) > self.max_active_symbols:
            self.logger.warning(
                f"Symbol count ({len(selected)}) exceeds max limit ({self.max_active_symbols}). "
                f"Truncating to first {self.max_active_symbols} symbols."
            )
            selected = selected[:self.max_active_symbols]

        self.logger.info(f"Total options to subscribe: {len(selected)}")
        return selected

    async def _discover_symbols(self) -> List[str]:
        """Discover valid options symbols, either dynamically or from static config.

        Returns:
            List of symbol strings to subscribe to
        """
        if self.use_dynamic_discovery:
            self.logger.info("Using dynamic symbol discovery...")
            all_tickers = await self._fetch_valid_options_symbols()

            if all_tickers:
                symbols = self._filter_symbols(all_tickers)
                if symbols:
                    return symbols
                else:
                    self.logger.warning("No symbols matched filter criteria")

            self.logger.warning("Dynamic discovery failed, falling back to static symbols")

        # Fallback to static symbols from config
        if self.static_symbols:
            self.logger.info(f"Using {len(self.static_symbols)} static symbols from config")
            return self.static_symbols

        self.logger.error("No symbols available (dynamic discovery failed and no static symbols)")
        return []

    async def start(self):
        """Start the Delta options streaming service."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        # Discover valid symbols
        self.active_symbols = await self._discover_symbols()

        if not self.active_symbols:
            self.logger.error("No valid options symbols to subscribe to")
            return

        self.running = True
        self.logger.info(f"Starting WebSocket connection to {self.ws_url}")
        self.logger.info(f"Monitoring {len(self.active_symbols)} options: {', '.join(self.active_symbols[:5])}...")

        reconnect_attempts = 0

        while self.running:
            try:
                connection_start_time = time.time()
                await self._connect_and_stream()
                reconnect_attempts = 0  # Reset on successful connection
            except Exception as e:
                # Reset attempts if connection was stable for >30s
                connection_duration = time.time() - connection_start_time
                if connection_duration > 30:
                    reconnect_attempts = 1
                else:
                    reconnect_attempts += 1

                # Clear stale WebSocket reference
                self.websocket = None
                self.logger.warning(f"Connection error (attempt {reconnect_attempts}): {e}")

                # Exponential backoff with 60s cap (never give up)
                delay = self.backoff_delays[min(reconnect_attempts - 1, len(self.backoff_delays) - 1)]
                self.logger.info(f"Reconnecting in {delay} seconds...")
                await asyncio.sleep(delay)

    async def _connect_and_stream(self):
        """Connect to WebSocket and stream options data."""
        # Clear stale state on reconnection (but NOT _trade_counter to avoid duplicate IDs)
        self._orderbooks.clear()
        self._trades.clear()

        # Clear stale Redis keys to prevent serving stale data after reconnection
        # This is important because fresh snapshots will repopulate the data
        if self.orderbook_enabled:
            for symbol in self.active_symbols:
                redis_key = f"{self.orderbook_redis_prefix}:{symbol}"
                self.redis_client.delete_key(redis_key)

        if self.trades_enabled:
            for symbol in self.active_symbols:
                redis_key = f"{self.trades_redis_prefix}:{symbol}"
                self.redis_client.delete_key(redis_key)

        async with websockets.connect(
            self.ws_url,
            ping_interval=30,
            ping_timeout=60  # Increased to allow time for bulk subscriptions
        ) as websocket:
            self.websocket = websocket
            self.logger.info("WebSocket connected successfully")

            # Subscribe to symbols
            await self._subscribe_to_symbols()

            # Start symbol refresh task
            refresh_task = asyncio.create_task(self._periodic_symbol_refresh())

            try:
                # Listen for messages
                async for message in websocket:
                    if not self.running:
                        break

                    try:
                        await self._handle_message(message)
                    except Exception as e:
                        self.logger.error(f"Error handling message: {e}")
            finally:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass

    async def _periodic_symbol_refresh(self):
        """Periodically refresh symbols to handle expiring options."""
        while self.running:
            try:
                await asyncio.sleep(self.symbol_refresh_interval)

                if not self.running:
                    break

                self.logger.info("Refreshing options symbols...")

                # Discover new symbols
                new_symbols = await self._discover_symbols()

                if not new_symbols:
                    self.logger.warning("Symbol refresh returned empty, keeping existing symbols")
                    continue

                # Find symbols to unsubscribe and subscribe
                old_set = set(self.active_symbols)
                new_set = set(new_symbols)

                to_unsubscribe = old_set - new_set
                to_subscribe = new_set - old_set

                if to_unsubscribe:
                    self.logger.info(f"Unsubscribing from {len(to_unsubscribe)} expired symbols")
                    await self._unsubscribe_symbols(list(to_unsubscribe))
                    # Clean up in-memory state for expired symbols
                    for symbol in to_unsubscribe:
                        self._orderbooks.pop(symbol, None)
                        self._trades.pop(symbol, None)

                if to_subscribe:
                    self.logger.info(f"Subscribing to {len(to_subscribe)} new symbols")
                    for symbol in to_subscribe:
                        await self._subscribe_single_symbol(symbol)

                self.active_symbols = new_symbols
                self.logger.info(f"Symbol refresh complete. Now tracking {len(self.active_symbols)} symbols")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error during symbol refresh: {e}")

    async def _subscribe_single_symbol(self, symbol: str):
        """Subscribe to a single symbol (all enabled channels)."""
        if not self.websocket:
            return

        # Build channel list based on feature flags
        channels = [
            {
                "name": "v2/ticker",
                "symbols": [symbol]
            }
        ]

        if self.orderbook_enabled:
            channels.append({
                "name": "l2_orderbook",
                "symbols": [symbol]
            })

        if self.trades_enabled:
            channels.append({
                "name": "all_trades",
                "symbols": [symbol]
            })

        subscribe_msg = {
            "type": "subscribe",
            "payload": {
                "channels": channels
            }
        }
        await self.websocket.send(json.dumps(subscribe_msg))
        self.logger.info(f"Subscribed to {symbol}")

    async def _unsubscribe_symbols(self, symbols: List[str]):
        """Unsubscribe from a list of symbols (all channels)."""
        if not self.websocket or not symbols:
            return

        # Build channel list matching subscription pattern
        channels = [
            {
                "name": "v2/ticker",
                "symbols": symbols
            }
        ]

        if self.orderbook_enabled:
            channels.append({
                "name": "l2_orderbook",
                "symbols": symbols
            })

        if self.trades_enabled:
            channels.append({
                "name": "all_trades",
                "symbols": symbols
            })

        unsubscribe_msg = {
            "type": "unsubscribe",
            "payload": {
                "channels": channels
            }
        }
        await self.websocket.send(json.dumps(unsubscribe_msg))
        self.logger.info(f"Unsubscribed from {len(symbols)} symbols")

    async def _subscribe_to_symbols(self):
        """Subscribe to options ticker/orderbook/trade updates for discovered symbols in batches."""
        if not self.websocket:
            return

        total_symbols = len(self.active_symbols)
        batch_size = self.subscription_batch_size
        batch_delay = self.subscription_batch_delay

        # Build feature description for logging
        features = ["ticker"]
        if self.orderbook_enabled:
            features.append("orderbook")
        if self.trades_enabled:
            features.append("trades")
        self.logger.info(
            f"Subscribing to {total_symbols} options in batches of {batch_size}... "
            f"(Features: {', '.join(features)})"
        )

        for i in range(0, total_symbols, batch_size):
            batch = self.active_symbols[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_symbols + batch_size - 1) // batch_size

            # Build channel list based on feature flags
            channels = [
                {
                    "name": "v2/ticker",
                    "symbols": batch
                }
            ]

            # Add orderbook channel if enabled
            if self.orderbook_enabled:
                channels.append({
                    "name": "l2_orderbook",
                    "symbols": batch
                })

            # Add trades channel if enabled
            if self.trades_enabled:
                channels.append({
                    "name": "all_trades",
                    "symbols": batch
                })

            # Subscribe to entire batch in one message (multiplexed channels)
            subscribe_msg = {
                "type": "subscribe",
                "payload": {
                    "channels": channels
                }
            }
            await self.websocket.send(json.dumps(subscribe_msg))

            channel_names = [ch['name'] for ch in channels]
            self.logger.info(
                f"Subscribed batch {batch_num}/{total_batches}: {len(batch)} symbols, "
                f"channels: {channel_names}"
            )

            # Add delay between batches (but not after the last one)
            if i + batch_size < total_symbols:
                await asyncio.sleep(batch_delay)

        self.logger.info(f"Subscription complete: {total_symbols} total options symbols")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message.

        Args:
            message: Raw WebSocket message
        """
        try:
            data = json.loads(message)
            msg_type = data.get('type', 'unknown')

            # Handle subscription confirmation
            if msg_type == 'subscriptions':
                channels = data.get('channels', [])
                self.logger.info(f"Subscription confirmed for {len(channels)} channels")
                for channel in channels:
                    symbols = channel.get('symbols', [])
                    self.logger.info(f"  Channel '{channel.get('name')}': {len(symbols)} symbols")
                return

            # Handle subscription errors
            if msg_type == 'error':
                error_msg = data.get('message', 'Unknown error')
                error_code = data.get('code', 'N/A')
                self.logger.error(f"WebSocket error from Delta: [{error_code}] {error_msg}")
                return

            # Handle ticker updates
            if msg_type == 'v2/ticker':
                await self._process_ticker_update(data)
            # Handle orderbook updates
            elif msg_type == 'l2_orderbook':
                await self._process_orderbook_update(data)
            # Handle trade snapshots (initial batch on subscribe)
            elif msg_type == 'all_trades_snapshot':
                await self._process_trade_snapshot(data)
            # Handle real-time trade updates
            elif msg_type == 'all_trades':
                await self._process_trade_update(data)
            else:
                # Log unhandled message types (but not too verbosely)
                self.logger.debug(f"Received message type: {msg_type}")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}", exc_info=True)

    async def _process_ticker_update(self, data: dict):
        """Process options ticker update and store in Redis.

        Args:
            data: Ticker update data
        """
        try:
            # Delta ticker data structure for options
            symbol = data.get('symbol')
            mark_price = data.get('mark_price')
            close_price = data.get('close')

            if not symbol:
                return

            # Use mark_price if available, otherwise close price
            price = mark_price if mark_price else close_price

            if not price:
                return

            # Validate price before float conversion
            try:
                price_float = float(price)
                if not math.isfinite(price_float) or price_float < 0:  # Options can have 0 price
                    self.logger.warning(f"Invalid price for {symbol}: {price}")
                    return
            except (ValueError, TypeError):
                self.logger.warning(f"Cannot convert price to float for {symbol}: {price}")
                return

            # Extract option details from symbol
            # Delta options format: C-BTC-106000-241220 (Call, BTC, Strike 106000, Expiry 20-Dec-24)
            # or P-BTC-106000-241220 (Put)
            option_info = self._parse_option_symbol(symbol)

            # Store in Redis
            # Use full symbol as key for options since each strike/expiry is unique
            redis_key = f"{self.redis_prefix}:{symbol}"

            # Prepare options-specific data
            additional_data = {
                'mark_price': str(data.get('mark_price', '0')),
                'volume_24h': str(data.get('volume', '0')),
                'high_24h': str(data.get('high', '0')),
                'low_24h': str(data.get('low', '0')),
                'open_interest': str(data.get('oi', '0')),
                'price_change_percent': str(data.get('price_change_24h', '0')),
                # Options-specific fields
                'option_type': option_info.get('type', 'UNKNOWN'),
                'underlying': option_info.get('underlying', ''),
                'strike_price': option_info.get('strike', ''),
                'expiry_date': option_info.get('expiry', ''),
                # Greeks (if available in data)
                'delta': str(data.get('greeks', {}).get('delta', '0')),
                'gamma': str(data.get('greeks', {}).get('gamma', '0')),
                'vega': str(data.get('greeks', {}).get('vega', '0')),
                'theta': str(data.get('greeks', {}).get('theta', '0')),
                'implied_volatility': str(data.get('iv', '0'))
            }

            # Store in Redis
            success = self.redis_client.set_price_data(
                key=redis_key,
                price=price_float,
                symbol=symbol,
                additional_data=additional_data,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.info(
                    f"[REDIS] Stored {symbol}: ${price_float} "
                    f"(Type: {option_info.get('type')}, Strike: {option_info.get('strike')}, "
                    f"IV: {additional_data.get('implied_volatility', 'N/A')})"
                )
            else:
                self.logger.warning(f"Failed to store {symbol} in Redis")

        except Exception as e:
            self.logger.error(f"Error processing ticker update: {e}")

    def _parse_option_symbol(self, symbol: str) -> dict:
        """Parse Delta options symbol to extract details.

        Args:
            symbol: Options symbol (e.g., C-BTC-106000-241220)

        Returns:
            Dictionary with option details
        """
        try:
            parts = symbol.split('-')
            if len(parts) >= 4:
                return {
                    'type': 'CALL' if parts[0] == 'C' else 'PUT',
                    'underlying': parts[1],
                    'strike': parts[2],
                    'expiry': parts[3]  # Format: YYMMDD
                }
            else:
                return {
                    'type': 'UNKNOWN',
                    'underlying': symbol,
                    'strike': '0',
                    'expiry': ''
                }
        except Exception as e:
            self.logger.error(f"Error parsing option symbol {symbol}: {e}")
            return {
                'type': 'UNKNOWN',
                'underlying': symbol,
                'strike': '0',
                'expiry': ''
            }

    async def _process_orderbook_update(self, data: dict):
        """Process l2_orderbook message and store in Redis.

        Delta sends full orderbook snapshots each time (not deltas).

        Args:
            data: Orderbook update data
        """
        if not self.orderbook_enabled:
            return

        try:
            symbol = data.get('symbol', '')
            if not symbol or symbol not in self.active_symbols:
                return

            # Extract buy/sell orders from Delta format
            buy_orders = data.get('buy') or []
            sell_orders = data.get('sell') or []

            # Parse orders into [[price, qty], ...] format
            def parse_orders(orders: List) -> List[List[float]]:
                parsed = []
                for order in orders:
                    if not isinstance(order, dict):
                        continue
                    try:
                        price = float(order.get('limit_price', 0))
                        size = float(order.get('size', 0))
                        if price > 0 and size > 0 and math.isfinite(price) and math.isfinite(size):
                            parsed.append([price, size])
                    except (ValueError, TypeError, AttributeError):
                        continue
                return parsed

            # Sort: bids descending, asks ascending
            bids = sorted(
                parse_orders(buy_orders),
                key=lambda x: x[0],
                reverse=True
            )[:self.orderbook_depth]

            asks = sorted(
                parse_orders(sell_orders),
                key=lambda x: x[0]
            )[:self.orderbook_depth]

            # Validate non-empty orderbook
            if not bids or not asks:
                return

            # Calculate spread and mid price BEFORE updating state
            best_bid = bids[0][0]
            best_ask = asks[0][0]
            spread = best_ask - best_bid

            # Check for crossed book (invalid) - do this before storing state
            if spread < 0:
                self.logger.warning(f"Invalid spread for {symbol}: {spread} (crossed book)")
                # Clear any existing corrupted state
                if symbol in self._orderbooks:
                    del self._orderbooks[symbol]
                # Remove stale Redis data
                redis_key = f"{self.orderbook_redis_prefix}:{symbol}"
                self.redis_client.delete_key(redis_key)
                return

            mid_price = (best_bid + best_ask) / 2

            # Update in-memory state (only after validation passes)
            self._orderbooks[symbol] = {
                'bids': bids,
                'asks': asks,
                'update_id': data.get('last_sequence_no', ''),
                'timestamp': int(time.time())
            }

            # Store in Redis
            redis_key = f"{self.orderbook_redis_prefix}:{symbol}"

            success = self.redis_client.set_orderbook_data(
                key=redis_key,
                bids=bids,
                asks=asks,
                spread=spread,
                mid_price=mid_price,
                update_id=data.get('last_sequence_no', ''),
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated {symbol} orderbook: spread=${spread:.4f}, "
                    f"mid=${mid_price:.2f}, {len(bids)} bids, {len(asks)} asks"
                )
            else:
                self.logger.warning(f"Failed to update orderbook in Redis for {symbol}")

        except Exception as e:
            self.logger.error(f"Error processing orderbook update: {e}")

    async def _process_trade_snapshot(self, data: dict):
        """Process all_trades_snapshot message (initial trades on subscribe).

        Args:
            data: Trade snapshot data
        """
        if not self.trades_enabled:
            return

        try:
            symbol = data.get('symbol', '')
            if not symbol or symbol not in self.active_symbols:
                return

            trades_data = data.get('trades', [])

            # Initialize deque with max length
            self._trades[symbol] = deque(maxlen=self.trades_limit)

            # Add trades from snapshot
            for i, trade in enumerate(trades_data):
                if not isinstance(trade, dict):
                    continue

                # Determine side (Delta uses buyer_role/seller_role)
                side = 'Buy' if trade.get('buyer_role') == 'taker' else 'Sell'

                try:
                    price = float(trade.get('price', 0))
                    size = float(trade.get('size', 0))
                except (ValueError, TypeError):
                    continue

                if price <= 0 or size <= 0 or not math.isfinite(price) or not math.isfinite(size):
                    continue

                # Generate robust fallback ID
                current_ts = int(time.time() * 1000)
                fallback_id = f"snapshot_{current_ts}_{i}"

                # ID priority: Exchange ID -> Trade ID -> Timestamp -> Fallback
                # Use explicit None checks to avoid "None" string
                raw_id = trade.get('id')
                if raw_id is None:
                    raw_id = trade.get('trade_id')
                if raw_id is None:
                    raw_id = timestamp
                if raw_id is None:
                    raw_id = fallback_id
                trade_id = str(raw_id)

                self._trades[symbol].append({
                    'p': price,
                    'q': size,
                    's': side,
                    't': timestamp if timestamp is not None else current_ts,
                    'id': trade_id
                })

            # Store in Redis
            await self._store_trades(symbol)

            self.logger.info(f"Received trade snapshot for {symbol}: {len(trades_data)} trades")

        except Exception as e:
            self.logger.error(f"Error processing trade snapshot: {e}")

    async def _process_trade_update(self, data: dict):
        """Process real-time all_trades message.

        Args:
            data: Trade update data
        """
        if not self.trades_enabled:
            return

        try:
            symbol = data.get('symbol', '')
            if not symbol or symbol not in self.active_symbols:
                return

            # Initialize deque if needed
            if symbol not in self._trades:
                self._trades[symbol] = deque(maxlen=self.trades_limit)

            # Determine side
            side = 'Buy' if data.get('buyer_role') == 'taker' else 'Sell'

            try:
                price = float(data.get('price', 0))
                size = float(data.get('size', 0))
            except (ValueError, TypeError):
                return

            if price <= 0 or size <= 0 or not math.isfinite(price) or not math.isfinite(size):
                return

            # Generate robust fallback ID with counter
            current_ts = int(time.time() * 1000)
            self._trade_counter += 1
            fallback_id = f"realtime_{current_ts}_{self._trade_counter}"

            # ID priority: Exchange ID -> Trade ID -> Timestamp -> Fallback
            # Use explicit None checks to avoid "None" string
            timestamp = data.get('timestamp')
            raw_id = data.get('id')
            if raw_id is None:
                raw_id = data.get('trade_id')
            if raw_id is None:
                raw_id = timestamp
            if raw_id is None:
                raw_id = fallback_id
            trade_id = str(raw_id)

            # Append new trade (auto-evicts oldest due to maxlen)
            self._trades[symbol].append({
                'p': price,
                'q': size,
                's': side,
                't': timestamp if timestamp is not None else current_ts,
                'id': trade_id
            })

            # Store in Redis
            await self._store_trades(symbol)

            self.logger.debug(f"Updated {symbol} trades: {len(self._trades[symbol])} trades in buffer")

        except Exception as e:
            self.logger.error(f"Error processing trade update: {e}")

    async def _store_trades(self, symbol: str):
        """Store trades to Redis.

        Args:
            symbol: Option symbol (e.g., C-BTC-106000-241220)
        """
        redis_key = f"{self.trades_redis_prefix}:{symbol}"

        # Convert deque to list for storage
        trades_list = list(self._trades.get(symbol, []))

        success = self.redis_client.set_trades_data(
            key=redis_key,
            trades=trades_list,
            original_symbol=symbol,
            ttl=self.redis_ttl
        )

        if not success:
            self.logger.warning(f"Failed to update trades in Redis for {symbol}")

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        # Clear in-memory state
        self._orderbooks.clear()
        self._trades.clear()

        self.logger.info("Delta Options Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('delta')
    service_config = config.get('services', {}).get('options', {})

    service = DeltaOptionsService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

"""CoinDCX Spot Price Service using Socket.IO.

Following the same patterns as BybitSpotService for orderbook and trades streaming.
"""

import asyncio
import json
import socketio
import time
from collections import deque
from typing import Optional, Dict, Any
from datetime import datetime

from core.base_service import BaseService


class CoinDCXSpotService(BaseService):
    """Service for streaming CoinDCX spot prices, orderbook, and trades via Socket.IO.

    Redis Key Patterns:
        - coindcx_spot_ob:{base_coin} -> Hash containing 'bids', 'asks', 'spread', 'mid_price', 'update_id', 'timestamp'
        - coindcx_spot_trades:{base_coin} -> Hash containing 'trades' (JSON list), 'count', 'timestamp'
    """

    def __init__(self, config: dict):
        """Initialize CoinDCX Spot Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("CoinDCX-Spot", config)
        self.ws_url = config.get('websocket_url', 'wss://stream.coindcx.com')
        self.symbols = config.get('symbols', [])
        # Exponential backoff delays as per CLAUDE.md: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]
        self.redis_prefix = config.get('redis_prefix', 'coindcx_spot')
        self.redis_ttl = config.get('redis_ttl', 60)  # Default to 60s if not in config
        self.sio: Optional[socketio.AsyncClient] = None
        self.ws_connected = False
        self.ping_task: Optional[asyncio.Task] = None

        # Orderbook configuration
        self.orderbook_enabled = config.get('orderbook_enabled', False)
        self.orderbook_depth = config.get('orderbook_depth', 20)
        self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'coindcx_spot_ob')

        # Trades configuration
        self.trades_enabled = config.get('trades_enabled', False)
        self.trades_limit = config.get('trades_limit', 50)
        self.trades_redis_prefix = config.get('trades_redis_prefix', 'coindcx_spot_trades')

        # Quote currencies for symbol parsing
        self.quote_currencies = config.get('quote_currencies', ['USDT', 'USDC'])

        # Symbol prefixes to strip
        self.symbol_prefixes = config.get('symbol_prefixes', ['KC-', 'B-'])

        # In-memory state for orderbooks and trades (matching Bybit pattern)
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._trades: Dict[str, deque] = {}
        self._initialized_symbols: set = set()

    def _extract_base_coin(self, symbol: str) -> str:
        """Extract base coin from CoinDCX symbol format.

        Args:
            symbol: Symbol in format 'BTCUSDT' or 'KC-BTC_USDT'

        Returns:
            Base coin (e.g., 'BTC')
        """
        # Remove prefixes
        for prefix in self.symbol_prefixes:
            if symbol.startswith(prefix):
                symbol = symbol[len(prefix):]
                break

        # Remove separator if present
        if '_' in symbol:
            return symbol.split('_')[0]

        # Handle standard format: BTCUSDT -> BTC
        for quote in self.quote_currencies:
            if symbol.endswith(quote):
                return symbol[:-len(quote)]
        return symbol

    def _normalize_symbol(self, raw_symbol: str) -> str:
        """Normalize CoinDCX symbol format to standard format.

        Args:
            raw_symbol: Raw symbol from message (e.g., 'BTCUSDT')

        Returns:
            Normalized symbol (e.g., 'BTCUSDT')
        """
        return raw_symbol.upper().replace("-", "").replace("_", "")

    async def start(self):
        """Start the CoinDCX spot price streaming service."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        if not self.symbols:
            self.logger.error("No symbols configured")
            return

        self.running = True
        self.logger.info(f"Starting Socket.IO connection to {self.ws_url}")
        self.logger.info(f"Monitoring symbols: {', '.join(self.symbols)}")

        reconnect_attempts = 0

        while self.running:
            try:
                connection_start_time = time.time()
                await self._connect_and_stream()
                reconnect_attempts = 0  # Reset on clean exit
            except Exception as e:
                # Reset attempts if connection was stable for >30s
                connection_duration = time.time() - connection_start_time
                if connection_duration > 30:
                    reconnect_attempts = 0

                reconnect_attempts += 1
                self.logger.error(f"Connection error (attempt {reconnect_attempts}): {e}")

                # Cleanup
                await self._cleanup_connection()

                # Exponential backoff with 60s cap (never give up)
                delay = self.backoff_delays[min(reconnect_attempts - 1, len(self.backoff_delays) - 1)]
                self.logger.info(f"Reconnecting in {delay} seconds...")
                await asyncio.sleep(delay)

    async def _connect_and_stream(self):
        """Connect to Socket.IO and stream data."""
        # Clear stale state on reconnection
        self._orderbooks.clear()
        self._trades.clear()
        self._initialized_symbols.clear()

        # Create Socket.IO client
        self.sio = socketio.AsyncClient(
            logger=False,
            engineio_logger=False
        )

        # Register event handlers
        self._register_event_handlers()

        # Connect
        await self.sio.connect(self.ws_url, transports=['websocket'])
        self.ws_connected = True
        self.logger.info("Socket.IO connected successfully")

        # Start ping task
        self.ping_task = asyncio.create_task(self._ping_task())

        # Subscribe to all channels
        await self._subscribe_to_channels()

        # Keep connection alive
        while self.running and self.ws_connected:
            await asyncio.sleep(1)

    def _register_event_handlers(self):
        """Register Socket.IO event handlers."""

        @self.sio.event
        async def connect():
            self.ws_connected = True
            self.logger.info("Socket.IO connected event")

        @self.sio.event
        async def disconnect():
            self.ws_connected = False
            self.logger.warning("Socket.IO disconnected")

        @self.sio.event
        async def connect_error(data):
            self.logger.error(f"Socket.IO connection error: {data}")

        @self.sio.on('depth-snapshot')
        async def handle_depth_snapshot(data):
            """Handle orderbook snapshot."""
            await self._process_orderbook_update(data, is_snapshot=True)

        @self.sio.on('depth-update')
        async def handle_depth_update(data):
            """Handle orderbook delta update."""
            await self._process_orderbook_update(data, is_snapshot=False)

        @self.sio.on('new-trade')
        async def handle_new_trade(data):
            """Handle new trade event."""
            await self._process_trade_update(data)

    async def _subscribe_to_channels(self):
        """Subscribe to ticker, orderbook, and trades channels for configured symbols."""
        for symbol in self.symbols:
            try:
                # Orderbook channel: {symbol}@orderbook@{depth}
                if self.orderbook_enabled:
                    ob_channel = f"{symbol}@orderbook@{self.orderbook_depth}"
                    await self.sio.emit('join', {'channelName': ob_channel})
                    self.logger.info(f"Subscribed to orderbook: {ob_channel}")

                # Trades channel: just {symbol}
                if self.trades_enabled:
                    await self.sio.emit('join', {'channelName': symbol})
                    self.logger.info(f"Subscribed to trades: {symbol}")

                await asyncio.sleep(0.1)  # Small delay between subscriptions

            except Exception as e:
                self.logger.error(f"Failed to subscribe to {symbol}: {e}")

    def _parse_message(self, data) -> Optional[dict]:
        """Parse CoinDCX wrapped message format.

        CoinDCX may send messages in format:
        {"event": "...", "data": "<json string>"}

        Args:
            data: Raw message from Socket.IO

        Returns:
            Parsed data dict, or None if parsing fails
        """
        try:
            # If data is already the inner content (dict with 's', 'bids', etc.)
            if isinstance(data, dict) and 's' in data:
                return data

            # If data is the wrapped format with 'data' key containing JSON string
            if isinstance(data, dict) and 'data' in data:
                inner_data = data['data']
                if isinstance(inner_data, str):
                    return json.loads(inner_data)
                elif isinstance(inner_data, dict):
                    return inner_data

            # If data is a JSON string directly
            if isinstance(data, str):
                return json.loads(data)

            # Unknown format, return as-is if dict
            return data if isinstance(data, dict) else None

        except json.JSONDecodeError as e:
            self.logger.warning(f"JSON parse error: {e}")
            return None

    async def _process_orderbook_update(self, data, is_snapshot: bool):
        """Process orderbook snapshot or delta update.

        Args:
            data: Orderbook message data
            is_snapshot: True if this is a full snapshot, False if delta
        """
        try:
            parsed = self._parse_message(data)
            if not parsed:
                return

            symbol = parsed.get('s', '')
            if not symbol:
                return

            # Normalize symbol for consistent key naming
            normalized_symbol = self._normalize_symbol(symbol)
            base_coin = self._extract_base_coin(symbol)

            if is_snapshot:
                # Full orderbook replacement
                # CoinDCX sends objects {price: qty}, not arrays [[price, qty]]
                raw_bids = parsed.get('bids', {})
                raw_asks = parsed.get('asks', {})

                self._orderbooks[normalized_symbol] = {
                    'bids': {price: qty for price, qty in raw_bids.items()},
                    'asks': {price: qty for price, qty in raw_asks.items()},
                    'update_id': parsed.get('vs', 0)
                }
                self._initialized_symbols.add(normalized_symbol)
                self.logger.debug(f"Received orderbook snapshot for {normalized_symbol}")

            else:
                # Delta update
                if normalized_symbol not in self._initialized_symbols:
                    # Initialize from first delta if no snapshot received
                    raw_bids = parsed.get('bids', {})
                    raw_asks = parsed.get('asks', {})
                    self._orderbooks[normalized_symbol] = {
                        'bids': {price: qty for price, qty in raw_bids.items()},
                        'asks': {price: qty for price, qty in raw_asks.items()},
                        'update_id': parsed.get('vs', 0)
                    }
                    self._initialized_symbols.add(normalized_symbol)
                    self.logger.info(f"Initialized orderbook from delta for {normalized_symbol}")
                else:
                    # Apply bid updates (zero qty = remove)
                    for price, qty in parsed.get('bids', {}).items():
                        if float(qty) == 0:
                            self._orderbooks[normalized_symbol]['bids'].pop(price, None)
                        else:
                            self._orderbooks[normalized_symbol]['bids'][price] = qty

                    # Apply ask updates
                    for price, qty in parsed.get('asks', {}).items():
                        if float(qty) == 0:
                            self._orderbooks[normalized_symbol]['asks'].pop(price, None)
                        else:
                            self._orderbooks[normalized_symbol]['asks'][price] = qty

                    self._orderbooks[normalized_symbol]['update_id'] = parsed.get('vs', 0)

            # Prepare sorted orderbook for Redis storage
            await self._store_orderbook(normalized_symbol, base_coin)

        except Exception as e:
            self.logger.error(f"Error processing orderbook update: {e}")

    async def _store_orderbook(self, symbol: str, base_coin: str):
        """Build sorted orderbook and store in Redis.

        Args:
            symbol: Normalized symbol (e.g., 'BTCUSDT')
            base_coin: Base coin (e.g., 'BTC')
        """
        try:
            ob = self._orderbooks.get(symbol, {})
            if not ob:
                return

            # Sort bids descending, asks ascending (limit to configured depth)
            sorted_bids = sorted(
                [[p, q] for p, q in ob.get('bids', {}).items()],
                key=lambda x: float(x[0]),
                reverse=True
            )[:self.orderbook_depth]

            sorted_asks = sorted(
                [[p, q] for p, q in ob.get('asks', {}).items()],
                key=lambda x: float(x[0])
            )[:self.orderbook_depth]

            # Calculate spread and mid_price (pre-calculated for fast access per spec)
            spread = None
            mid_price = None
            if sorted_bids and sorted_asks:
                best_bid = float(sorted_bids[0][0])
                best_ask = float(sorted_asks[0][0])
                spread = best_ask - best_bid

                # Skip storing if spread is invalid (crossed book)
                if spread < 0:
                    self.logger.warning(
                        f"Invalid spread for {symbol}: {spread} (crossed book). Clearing state."
                    )
                    del self._orderbooks[symbol]
                    self._initialized_symbols.discard(symbol)
                    return

                mid_price = (best_bid + best_ask) / 2

            # Store in Redis using public API
            redis_key = f"{self.orderbook_redis_prefix}:{base_coin}"
            success = self.redis_client.set_orderbook_data(
                key=redis_key,
                bids=sorted_bids,
                asks=sorted_asks,
                spread=spread,
                mid_price=mid_price,
                update_id=ob.get('update_id', 0),
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated orderbook {base_coin}: {len(sorted_bids)} bids, {len(sorted_asks)} asks, "
                    f"spread: {spread}"
                )
        except Exception as e:
            self.logger.error(f"Error storing orderbook for {symbol}: {e}")

    async def _process_trade_update(self, data):
        """Process trade update and store in Redis.

        Args:
            data: Trade message data
        """
        try:
            parsed = self._parse_message(data)
            if not parsed:
                return

            # Validate required fields
            required_fields = ['s', 'p', 'q', 'S', 'T']
            if not all(k in parsed for k in required_fields):
                return

            symbol = parsed.get('s')
            try:
                price = float(parsed.get('p', 0))
                quantity = float(parsed.get('q', 0))
            except (ValueError, TypeError):
                return

            if price <= 0 or quantity <= 0:
                return

            # Normalize symbol and extract base coin
            normalized_symbol = self._normalize_symbol(symbol)
            base_coin = self._extract_base_coin(symbol)

            # Initialize deque for this symbol if not exists
            self._trades.setdefault(normalized_symbol, deque(maxlen=self.trades_limit))

            # Append trade with compact field names (per spec: p, q, s, t, id)
            self._trades[normalized_symbol].append({
                'p': price,                      # price
                'q': quantity,                   # quantity
                's': parsed.get('S', ''),        # side (buy/sell)
                't': parsed.get('T', 0),         # timestamp
                'id': parsed.get('t', '')        # trade id
            })

            # Store in Redis using public API
            redis_key = f"{self.trades_redis_prefix}:{base_coin}"
            trades_list = list(self._trades[normalized_symbol])
            success = self.redis_client.set_trades_data(
                key=redis_key,
                trades=trades_list,
                original_symbol=normalized_symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated trades {base_coin}: {len(trades_list)} trades, "
                    f"latest: {price} @ {parsed.get('S', 'unknown')}"
                )

        except Exception as e:
            self.logger.error(f"Error processing trade update: {e}")

    async def _ping_task(self):
        """Send periodic ping to keep Socket.IO connection alive."""
        while self.running and self.ws_connected:
            await asyncio.sleep(25)
            try:
                if self.sio and self.ws_connected:
                    await self.sio.emit('ping', {'data': 'Ping message'})
            except Exception as e:
                self.logger.error(f"Ping failed: {e}")

    async def _cleanup_connection(self):
        """Cleanup Socket.IO connection."""
        try:
            if self.ping_task:
                self.ping_task.cancel()
                try:
                    await self.ping_task
                except asyncio.CancelledError:
                    pass

            if self.sio and self.ws_connected:
                await self.sio.disconnect()

            self.ws_connected = False
            self.sio = None

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    async def stop(self):
        """Stop the service."""
        self.running = False
        await self._cleanup_connection()
        self.logger.info("CoinDCX Spot Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('coindcx')
    service_config = config.get('services', {}).get('spot', {})

    service = CoinDCXSpotService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

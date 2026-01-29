"""Delta Exchange Futures WebSocket service for LTP, orderbook, and trades."""

import asyncio
import json
import math
import time
from collections import deque
from typing import Dict, Any, Optional, List

import websockets

from core.base_service import BaseService


class DeltaFuturesLTPService(BaseService):
    """Service for streaming Delta Exchange futures data via WebSocket.

    Streams LTP, orderbook, and trades from a single WebSocket connection.

    Redis Key Patterns:
        Ticker:    {redis_prefix}:{base_coin} (Hash)
        Orderbook: {orderbook_redis_prefix}:{base_coin} (Hash)
        Trades:    {trades_redis_prefix}:{base_coin} (Hash)
    """

    def __init__(self, config: dict):
        """Initialize Delta Futures Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Delta-Futures", config)

        # Connection settings
        self.ws_url = config.get('websocket_url', 'wss://socket.india.delta.exchange')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)

        # Redis settings for LTP
        self.redis_prefix = config.get('redis_prefix', 'delta_futures')
        self.redis_ttl = config.get('redis_ttl', 60)

        # Symbol parsing
        self.quote_currencies = config.get('quote_currencies', ['USD', 'USDT'])

        # Feature flags
        self.orderbook_enabled = config.get('orderbook_enabled', False)
        self.trades_enabled = config.get('trades_enabled', False)
        self.orderbook_depth = config.get('orderbook_depth', 50)
        self.trades_limit = config.get('trades_limit', 50)

        # Redis prefixes for orderbook and trades
        self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'delta_futures_ob')
        self.trades_redis_prefix = config.get('trades_redis_prefix', 'delta_futures_trades')

        # In-memory state
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._trades: Dict[str, deque] = {}

        # WebSocket connection
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None

        # Exponential backoff delays as per CLAUDE.md: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

    async def start(self):
        """Start the Delta futures streaming service."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        if not self.symbols:
            self.logger.error("No symbols configured")
            return

        self.running = True
        self.logger.info(f"Starting WebSocket connection to {self.ws_url}")
        self.logger.info(f"Monitoring symbols: {', '.join(self.symbols)}")
        self.logger.info(f"Features: LTP=enabled, Orderbook={self.orderbook_enabled}, Trades={self.trades_enabled}")

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

                self.logger.warning(f"Connection error (attempt {reconnect_attempts}): {e}")

                # Clear stale WebSocket reference
                if self.websocket:
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                self.websocket = None

                # Exponential backoff with 60s cap (never give up)
                delay = self.backoff_delays[min(reconnect_attempts - 1, len(self.backoff_delays) - 1)]
                self.logger.info(f"Reconnecting in {delay} seconds...")
                await asyncio.sleep(delay)

    async def _connect_and_stream(self):
        """Connect to WebSocket and stream data."""
        # Clear stale state on reconnection
        self._orderbooks.clear()
        self._trades.clear()

        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=30  # Matches CLAUDE.md specification
        ) as websocket:
            self.websocket = websocket
            self.logger.info("WebSocket connected successfully")

            # Subscribe to channels
            await self._subscribe_to_channels()

            # Listen for messages
            async for message in websocket:
                if not self.running:
                    break

                try:
                    await self._handle_message(message)
                except Exception as e:
                    self.logger.error(f"Error handling message: {e}")

    async def _subscribe_to_channels(self):
        """Subscribe to ticker, orderbook, and trade channels based on feature flags."""
        if not self.websocket:
            return

        channels = []

        # Always subscribe to ticker for LTP
        channels.append({
            "name": "v2/ticker",
            "symbols": self.symbols
        })

        # Subscribe to orderbook if enabled
        if self.orderbook_enabled:
            channels.append({
                "name": "l2_orderbook",
                "symbols": self.symbols
            })

        # Subscribe to trades if enabled
        if self.trades_enabled:
            channels.append({
                "name": "all_trades",
                "symbols": self.symbols
            })

        # Delta Exchange subscription format
        subscribe_msg = {
            "type": "subscribe",
            "payload": {
                "channels": channels
            }
        }

        await self.websocket.send(json.dumps(subscribe_msg))
        channel_names = [c['name'] for c in channels]
        self.logger.info(f"Subscribed to channels: {channel_names} for symbols: {self.symbols}")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message.

        Args:
            message: Raw WebSocket message
        """
        try:
            data = json.loads(message)

            # Handle subscription confirmation
            if data.get('type') == 'subscriptions':
                self.logger.info(f"Subscription confirmed: {data.get('channels', [])}")
                return

            # Route by message type
            msg_type = data.get('type', '')

            if msg_type == 'v2/ticker':
                await self._process_ticker_update(data)
            elif msg_type == 'l2_orderbook':
                await self._process_orderbook_update(data)
            elif msg_type == 'all_trades_snapshot':
                await self._process_trade_snapshot(data)
            elif msg_type == 'all_trades':
                await self._process_trade_update(data)
            elif msg_type == 'heartbeat':
                pass  # Ignore heartbeats
            else:
                # Log only if it's not a common ignored type
                if msg_type not in ['']:
                    self.logger.debug(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    async def _process_ticker_update(self, data: dict):
        """Process ticker update and store in Redis.

        Args:
            data: Ticker update data
        """
        try:
            # Delta ticker data structure
            ticker_data = data.get('symbol')
            mark_price = data.get('mark_price')
            close_price = data.get('close')

            if not ticker_data:
                return

            # Use mark_price if available, otherwise close price
            price = mark_price if mark_price else close_price

            if not price:
                return

            # Validate price before float conversion
            try:
                price_float = float(price)
                if not math.isfinite(price_float) or price_float <= 0:
                    self.logger.warning(f"Invalid price for {ticker_data}: {price}")
                    return
            except (ValueError, TypeError):
                self.logger.warning(f"Cannot convert price to float for {ticker_data}: {price}")
                return

            # Extract base coin (e.g., BTC from BTCUSD or BTCUSDT)
            base_coin = self._extract_base_coin(ticker_data)

            # Store in Redis
            redis_key = f"{self.redis_prefix}:{base_coin}"

            # Prepare additional data
            additional_data = {
                'mark_price': str(data.get('mark_price', '0')),
                'volume_24h': str(data.get('volume', '0')),
                'high_24h': str(data.get('high', '0')),
                'low_24h': str(data.get('low', '0')),
                'open_interest': str(data.get('oi', '0')),
                'funding_rate': str(data.get('funding_rate', '0')),
                'price_change_percent': str(data.get('price_change_24h', '0'))
            }

            # Store in Redis
            success = self.redis_client.set_price_data(
                key=redis_key,
                price=price_float,
                symbol=ticker_data,
                additional_data=additional_data,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated {base_coin}: ${price_float} "
                    f"(Mark: ${data.get('mark_price', 'N/A')})"
                )

        except Exception as e:
            self.logger.error(f"Error processing ticker update: {e}")

    async def _process_orderbook_update(self, data: dict):
        """Process l2_orderbook message.

        Delta sends full orderbook each time.
        """
        try:
            symbol = data.get('symbol', '')
            if symbol not in self.symbols:
                return

            # Extract base coin (BTCUSD -> BTC)
            base_coin = self._extract_base_coin(symbol)

            # Convert Delta format to [[price, qty], ...] format
            buy_orders = data.get('buy') or []
            sell_orders = data.get('sell') or []

            # Build sorted orderbook
            def parse_orders(orders: List) -> List[List[float]]:
                parsed = []
                for order in orders:
                    # Validate order is a dict before accessing
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

            bids = sorted(
                parse_orders(buy_orders),
                key=lambda x: x[0],
                reverse=True
            )[:self.orderbook_depth]

            asks = sorted(
                parse_orders(sell_orders),
                key=lambda x: x[0]
            )[:self.orderbook_depth]

            # Validate empty orderbook
            if not bids or not asks:
                return

            # Update in-memory state
            self._orderbooks[symbol] = {
                'bids': bids,
                'asks': asks,
                'update_id': data.get('last_sequence_no', '')
            }

            # Calculate spread and mid price
            best_bid = bids[0][0] if bids else 0
            best_ask = asks[0][0] if asks else 0

            # Initialize metrics
            spread = 0.0
            mid_price = 0.0

            if best_bid > 0 and best_ask > 0:
                spread = best_ask - best_bid

                # Check for crossed book
                if spread < 0:
                    self.logger.warning(f"Invalid spread for {symbol}: {spread} (crossed book)")
                    # Clear corrupted state
                    if symbol in self._orderbooks:
                        del self._orderbooks[symbol]

                    # Ensure stale data is removed from Redis immediately
                    redis_key = f"{self.orderbook_redis_prefix}:{base_coin}"
                    self.redis_client.delete_key(redis_key)
                    return

                mid_price = (best_bid + best_ask) / 2

            # Store in Redis hash
            redis_key = f"{self.orderbook_redis_prefix}:{base_coin}"

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
                    f"Updated {base_coin} order book: spread=${spread:.2f}, "
                    f"mid=${mid_price:.2f}, {len(bids)} bids, {len(asks)} asks"
                )
            else:
                self.logger.warning(f"Failed to update orderbook in Redis for {base_coin}")

        except Exception as e:
            self.logger.error(f"Error processing order book update: {e}")

    async def _process_trade_snapshot(self, data: dict):
        """Process all_trades_snapshot message (initial trades)."""
        try:
            symbol = data.get('symbol', '')
            if symbol not in self.symbols:
                return

            base_coin = self._extract_base_coin(symbol)
            trades_data = data.get('trades', [])

            # Initialize deque
            self._trades[symbol] = deque(maxlen=self.trades_limit)

            # Add trades from snapshot
            for trade in trades_data:
                # Validate trade is a dict before accessing
                if not isinstance(trade, dict):
                    continue

                # Delta sends buyer_role/seller_role.
                # If buyer is taker -> Buy side initiated
                side = 'Buy' if trade.get('buyer_role') == 'taker' else 'Sell'

                try:
                    price = float(trade.get('price', 0))
                    size = float(trade.get('size', 0))
                except (ValueError, TypeError):
                    continue

                if price <= 0 or size <= 0 or not math.isfinite(price) or not math.isfinite(size):
                    continue

                # Generate unique trade ID (timestamp + price + size hash)
                timestamp = trade.get('timestamp')
                if timestamp is not None:
                    trade_id = f"{timestamp}_{hash((price, size)) % 100000}"
                else:
                    trade_id = f"{int(time.time() * 1000)}_{hash((price, size)) % 100000}"

                self._trades[symbol].append({
                    'p': price,
                    'q': size,
                    's': side,
                    't': timestamp if timestamp is not None else int(time.time() * 1000),
                    'id': trade_id
                })

            # Store in Redis
            await self._store_trades(symbol, base_coin)

            self.logger.info(f"Received trade snapshot for {symbol}: {len(trades_data)} trades")

        except Exception as e:
            self.logger.error(f"Error processing trade snapshot: {e}")

    async def _process_trade_update(self, data: dict):
        """Process real-time all_trades message."""
        try:
            symbol = data.get('symbol', '')
            if symbol not in self.symbols:
                return

            base_coin = self._extract_base_coin(symbol)

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

            # Generate unique trade ID (timestamp + price + size hash)
            timestamp = data.get('timestamp')
            if timestamp is not None:
                trade_id = f"{timestamp}_{hash((price, size)) % 100000}"
            else:
                trade_id = f"{int(time.time() * 1000)}_{hash((price, size)) % 100000}"

            # Append new trade
            self._trades[symbol].append({
                'p': price,
                'q': size,
                's': side,
                't': timestamp if timestamp is not None else int(time.time() * 1000),
                'id': trade_id
            })

            # Store in Redis
            await self._store_trades(symbol, base_coin)

            self.logger.debug(f"Updated {base_coin} trades: {len(self._trades[symbol])} trades in buffer")

        except Exception as e:
            self.logger.error(f"Error processing trade update: {e}")

    async def _store_trades(self, symbol: str, base_coin: str):
        """Store trades to Redis."""
        redis_key = f"{self.trades_redis_prefix}:{base_coin}"

        # Convert deque to list for storage
        trades_list = list(self._trades[symbol])

        success = self.redis_client.set_trades_data(
            key=redis_key,
            trades=trades_list,
            original_symbol=symbol,
            ttl=self.redis_ttl
        )

        if not success:
            self.logger.warning(f"Failed to update trades in Redis for {base_coin}")

    def _extract_base_coin(self, symbol: str) -> str:
        """Extract base coin from Delta symbol (e.g., BTCUSD -> BTC).

        Args:
            symbol: Original exchange symbol

        Returns:
            Base coin (e.g., BTC, ETH)
        """
        # Remove common quote currencies
        for quote in self.quote_currencies:
            if symbol.endswith(quote):
                return symbol[:-len(quote)]
        return symbol

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        self.logger.info("Delta Futures Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('delta')
    service_config = config.get('services', {}).get('futures_ltp', {})

    service = DeltaFuturesLTPService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

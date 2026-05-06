"""Bybit Spot TestNet Price Service."""

import asyncio
import json
import math
import time
import websockets
from collections import deque
from typing import Optional, Dict, Any
from datetime import datetime

from core.base_service import BaseService


class BybitSpotTestnetService(BaseService):
    """Service for streaming Bybit Spot TestNet prices via WebSocket.

    Redis Key Patterns:
        Ticker: {redis_prefix}:{base_coin} (Hash)
        Orderbook: {orderbook_redis_prefix}:{base_coin} (Hash)
        Trades: {trades_redis_prefix}:{base_coin} (Hash)
    """

    def __init__(self, config: dict):
        """Initialize Bybit Spot TestNet Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Bybit-Spot-Testnet", config)
        self.ws_url = config.get('websocket_url', 'wss://stream-testnet.bybit.com/v5/public/spot')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.redis_prefix = config.get('redis_prefix', 'bybit_spot_testnet')
        self.redis_ttl = config.get('redis_ttl', 60)
        self.quote_currencies = config.get('quote_currencies', ['USDT', 'USDC', 'BTC', 'ETH'])
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        # Exponential backoff delays as per CLAUDE.md: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

        # Orderbook configuration
        self.orderbook_enabled = config.get('orderbook_enabled', False)
        self.orderbook_depth = config.get('orderbook_depth', 50)
        self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'bybit_spot_testnet_ob')

        # Trades configuration
        self.trades_enabled = config.get('trades_enabled', False)
        self.trades_limit = config.get('trades_limit', 50)
        self.trades_redis_prefix = config.get('trades_redis_prefix', 'bybit_spot_testnet_trades')

        # In-memory state for orderbooks and trades
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._trades: Dict[str, deque] = {}

    def _get_redis_symbol(self, symbol: str) -> str:
        """Return the symbol to use as Redis key suffix (full exchange symbol)."""
        return symbol

    async def start(self):
        """Start the Bybit Spot TestNet price streaming service."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        if not self.symbols:
            self.logger.error("No symbols configured")
            return

        self.running = True
        self.logger.info(f"Starting WebSocket connection to {self.ws_url}")
        self.logger.info(f"Monitoring symbols: {', '.join(self.symbols)}")

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
        """Connect to WebSocket and stream prices."""
        # Clear stale state on reconnection to prevent memory leaks and stale data
        self._orderbooks.clear()
        self._trades.clear()

        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=30  # Matches CLAUDE.md specification
        ) as websocket:
            self.websocket = websocket
            self.logger.info("WebSocket connected successfully")

            # Subscribe to all channels
            await self._subscribe_to_channels()

            # Start periodic Redis refresh for illiquid markets
            refresh_task = asyncio.create_task(self._refresh_orderbooks_periodically())

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

    async def _subscribe_to_channels(self):
        """Subscribe to ticker, orderbook, and trades updates for configured symbols."""
        if not self.websocket:
            return

        for symbol in self.symbols:
            # Build channel list for this symbol
            channels = [f"kline.1.{symbol}"]

            if self.orderbook_enabled:
                channels.append(f"orderbook.{self.orderbook_depth}.{symbol}")

            if self.trades_enabled:
                channels.append(f"publicTrade.{symbol}")

            # Subscribe to all channels for this symbol
            subscribe_msg = {
                "op": "subscribe",
                "args": channels
            }
            await self.websocket.send(json.dumps(subscribe_msg))
            self.logger.info(f"Subscribed to channels for {symbol}: {channels}")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message.

        Args:
            message: Raw WebSocket message
        """
        try:
            data = json.loads(message)

            # Handle subscription confirmation
            if data.get('op') == 'subscribe':
                self.logger.debug(f"Subscription confirmed: {data}")
                return

            topic = data.get('topic', '')

            # Route to appropriate handler based on topic prefix
            if topic.startswith('kline.'):
                await self._process_ticker_update(data)
            elif topic.startswith('orderbook.'):
                await self._process_orderbook_update(data)
            elif topic.startswith('publicTrade.'):
                await self._process_trade_update(data)

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    async def _process_ticker_update(self, data: dict):
        """Process kline update and store in Redis.

        Kline payload shape: data["data"] is a list of candle dicts and the
        symbol is only present in the topic string (kline.{interval}.{symbol}).
        """
        try:
            candles = data.get('data') or []
            if not candles:
                return

            candle = candles[-1]
            if not isinstance(candle, dict):
                return

            topic = data.get('topic', '')
            # split('.', 2) keeps inner dots in symbol intact for forward compatibility
            symbol = topic.split('.', 2)[-1]
            close_price = candle.get('close')

            if not symbol or close_price is None:
                return

            try:
                price = float(close_price)
                if not math.isfinite(price) or price <= 0:
                    self.logger.warning(f"Invalid price for {symbol}: {close_price}")
                    return
            except (ValueError, TypeError):
                self.logger.warning(f"Cannot convert price to float for {symbol}: {close_price}")
                return

            redis_symbol = self._get_redis_symbol(symbol)

            additional_data = {
                'open': candle.get('open', '0'),
                'high': candle.get('high', '0'),
                'low': candle.get('low', '0'),
                'close': candle.get('close', '0'),
                'volume': candle.get('volume', '0'),
            }

            # Bybit candle timestamp is ms since epoch; consumers expect seconds
            candle_ts_ms = candle.get('timestamp')
            if candle_ts_ms is not None:
                try:
                    additional_data['timestamp'] = str(int(int(candle_ts_ms) / 1000))
                except (ValueError, TypeError):
                    pass

            redis_key = f"{self.redis_prefix}:{redis_symbol}"
            success = self.redis_client.set_price_data(
                key=redis_key,
                price=price,
                symbol=symbol,
                additional_data=additional_data,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated {redis_symbol}: ${close_price} "
                    f"(O:{candle.get('open')} H:{candle.get('high')} "
                    f"L:{candle.get('low')} V:{candle.get('volume')})"
                )

        except Exception as e:
            self.logger.error(f"Error processing kline update: {e}")

    async def _process_orderbook_update(self, data: dict):
        """Process orderbook update and store in Redis.

        Args:
            data: Orderbook update data (snapshot or delta)
        """
        try:
            update_type = data.get('type', '')  # 'snapshot' or 'delta'
            ob_data = data.get('data', {})

            if not isinstance(ob_data, dict):
                return

            symbol = ob_data.get('s', '')

            if not symbol:
                return

            redis_symbol = self._get_redis_symbol(symbol)

            if update_type == 'snapshot':
                # Full orderbook replacement (validate item length to prevent IndexError)
                self._orderbooks[symbol] = {
                    'bids': {item[0]: item[1] for item in ob_data.get('b', []) if len(item) >= 2},
                    'asks': {item[0]: item[1] for item in ob_data.get('a', []) if len(item) >= 2},
                    'update_id': ob_data.get('u', 0)
                }
            elif update_type == 'delta':
                # Incremental update
                if symbol not in self._orderbooks:
                    self.logger.warning(f"Received delta before snapshot for {symbol}")
                    return

                # Apply bid updates (validate entry length to prevent IndexError/ValueError)
                for entry in ob_data.get('b', []):
                    if len(entry) < 2:
                        continue
                    price, qty = entry[0], entry[1]
                    try:
                        qty_float = float(qty)
                        if not math.isfinite(qty_float):
                            continue
                        if qty_float == 0:
                            self._orderbooks[symbol]['bids'].pop(price, None)
                        else:
                            self._orderbooks[symbol]['bids'][price] = qty
                    except (ValueError, TypeError):
                        continue

                # Apply ask updates (validate entry length to prevent IndexError/ValueError)
                for entry in ob_data.get('a', []):
                    if len(entry) < 2:
                        continue
                    price, qty = entry[0], entry[1]
                    try:
                        qty_float = float(qty)
                        if not math.isfinite(qty_float):
                            continue
                        if qty_float == 0:
                            self._orderbooks[symbol]['asks'].pop(price, None)
                        else:
                            self._orderbooks[symbol]['asks'][price] = qty
                    except (ValueError, TypeError):
                        continue

                self._orderbooks[symbol]['update_id'] = ob_data.get('u', 0)

            # Store sorted orderbook to Redis
            self._store_orderbook_to_redis(symbol)

        except Exception as e:
            self.logger.error(f"Error processing orderbook update: {e}")

    async def _process_trade_update(self, data: dict):
        """Process trade update and store in Redis.

        Args:
            data: Trade update data
        """
        try:
            trades_data = data.get('data', [])

            if not trades_data:
                return

            for trade in trades_data:
                symbol = trade.get('s', '')
                try:
                    price = float(trade.get('p', 0))
                    quantity = float(trade.get('v', 0))
                except (ValueError, TypeError):
                    continue

                # Validate required fields
                if not symbol or price <= 0 or quantity <= 0:
                    continue

                redis_symbol = self._get_redis_symbol(symbol)

                # Initialize deque for this symbol if not exists (atomic)
                self._trades.setdefault(symbol, deque(maxlen=self.trades_limit))

                # Append trade with compact field names
                self._trades[symbol].append({
                    'p': price,                   # price
                    'q': quantity,                # quantity (v is volume in Bybit)
                    's': trade.get('S', ''),      # side (Buy/Sell)
                    't': trade.get('T', 0),       # timestamp
                    'id': trade.get('i', '')      # trade id
                })

                # Store trades to Redis
                self._store_trades_to_redis(symbol)

        except Exception as e:
            self.logger.error(f"Error processing trade update: {e}")

    def _store_orderbook_to_redis(self, symbol: str):
        """Store in-memory orderbook for a symbol to Redis.

        Sorts bids/asks, validates spread, and writes to Redis with TTL.
        Shared by _process_orderbook_update and the periodic refresh task.
        """
        ob = self._orderbooks.get(symbol, {})
        if not ob:
            return

        redis_symbol = self._get_redis_symbol(symbol)

        # Sort bids descending, asks ascending
        sorted_bids = sorted(
            [[p, q] for p, q in ob.get('bids', {}).items()],
            key=lambda x: float(x[0]),
            reverse=True
        )[:self.orderbook_depth]

        sorted_asks = sorted(
            [[p, q] for p, q in ob.get('asks', {}).items()],
            key=lambda x: float(x[0])
        )[:self.orderbook_depth]

        # Validate empty orderbook
        if not sorted_bids or not sorted_asks:
            return

        # Calculate spread and mid_price
        spread = None
        mid_price = None
        if sorted_bids and sorted_asks:
            # Validate nested structure before indexing
            if len(sorted_bids[0]) < 1 or len(sorted_asks[0]) < 1:
                self.logger.warning(f"Malformed orderbook entry for {symbol}")
                return
            try:
                best_bid = float(sorted_bids[0][0])
                best_ask = float(sorted_asks[0][0])

                if not math.isfinite(best_bid) or not math.isfinite(best_ask):
                    return

                spread = best_ask - best_bid
                # Skip storing if spread is invalid (crossed book)
                if spread < 0:
                    self.logger.warning(f"Invalid spread for {symbol}: {spread} (crossed book)")
                    del self._orderbooks[symbol]  # Clear corrupted state to force fresh snapshot

                    # Ensure stale data is removed from Redis immediately
                    redis_key = f"{self.orderbook_redis_prefix}:{redis_symbol}"
                    self.redis_client.delete_key(redis_key)
                    return
                mid_price = (best_bid + best_ask) / 2
            except (ValueError, TypeError):
                return

        # Store in Redis using public API
        redis_key = f"{self.orderbook_redis_prefix}:{redis_symbol}"
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
                f"Updated orderbook {redis_symbol}: {len(sorted_bids)} bids, {len(sorted_asks)} asks, "
                f"spread: {spread}"
            )

    def _store_trades_to_redis(self, symbol: str):
        """Store in-memory trades for a symbol to Redis.

        Shared by _process_trade_update and the periodic refresh task.
        """
        trades_deque = self._trades.get(symbol)
        if not trades_deque:
            return

        redis_symbol = self._get_redis_symbol(symbol)
        redis_key = f"{self.trades_redis_prefix}:{redis_symbol}"
        trades_list = list(trades_deque)

        success = self.redis_client.set_trades_data(
            key=redis_key,
            trades=trades_list,
            original_symbol=symbol,
            ttl=self.redis_ttl
        )

        if success:
            self.logger.debug(
                f"Refreshed trades {redis_symbol}: {len(trades_list)} trades"
            )

    async def _refresh_orderbooks_periodically(self):
        """Periodically re-write in-memory orderbook and trades data to Redis.

        Prevents TTL expiration on illiquid markets where WebSocket updates
        are infrequent. Runs every 45 seconds (safely before the 60s TTL).
        """
        while self.running:
            await asyncio.sleep(45)
            refreshed_ob = 0
            refreshed_trades = 0
            for symbol in list(self._orderbooks.keys()):
                try:
                    self._store_orderbook_to_redis(symbol)
                    refreshed_ob += 1
                except Exception as e:
                    self.logger.error(f"Error refreshing orderbook for {symbol}: {e}")
            for symbol in list(self._trades.keys()):
                try:
                    self._store_trades_to_redis(symbol)
                    refreshed_trades += 1
                except Exception as e:
                    self.logger.error(f"Error refreshing trades for {symbol}: {e}")
            if refreshed_ob > 0 or refreshed_trades > 0:
                self.logger.debug(
                    f"Periodic refresh: {refreshed_ob} orderbooks, {refreshed_trades} trades"
                )

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        self.logger.info("Bybit Spot TestNet Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('bybit_spot_testnet')
    service_config = config.get('services', {}).get('spot', {})

    service = BybitSpotTestnetService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

"""Bybit Futures Orderbook Service."""

import asyncio
import json
import time
import websockets
from typing import Optional, Dict, Any

from core.base_service import BaseService


class BybitFuturesOrderbookService(BaseService):
    """Service for streaming Bybit futures orderbook data via WebSocket.

    Redis Key Pattern:
        Orderbook: {redis_prefix}:{base_coin} (Hash)
    """

    def __init__(self, config: dict):
        """Initialize Bybit Futures Orderbook Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Bybit-Futures-Orderbook", config)
        self.ws_url = config.get('websocket_url', 'wss://stream.bybit.com/v5/public/linear')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.redis_prefix = config.get('redis_prefix', 'bybit_futures_ob')
        self.redis_ttl = config.get('redis_ttl', 60)
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        # Exponential backoff delays: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

        # Orderbook configuration
        self.orderbook_depth = config.get('orderbook_depth', 50)

        # Quote currencies for symbol parsing (order matters - longest first)
        self.quote_currencies = config.get('quote_currencies', ['USDT', 'USDC'])

        # In-memory state for orderbooks
        self._orderbooks: Dict[str, Dict[str, Any]] = {}

    def _extract_base_coin(self, symbol: str) -> str:
        """Extract base coin from symbol by removing quote currency.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')

        Returns:
            Base coin (e.g., 'BTC')
        """
        for quote in self.quote_currencies:
            if symbol.endswith(quote):
                return symbol[:-len(quote)]
        return symbol

    async def start(self):
        """Start the Bybit futures orderbook streaming service."""
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
        """Connect to WebSocket and stream orderbook data."""
        # Clear stale state on reconnection to prevent memory leaks and stale data
        self._orderbooks.clear()

        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=30
        ) as websocket:
            self.websocket = websocket
            self.logger.info("WebSocket connected successfully")

            # Subscribe to orderbook channels
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
        """Subscribe to orderbook updates for configured symbols."""
        if not self.websocket:
            return

        # Build channel list for all symbols
        channels = [f"orderbook.{self.orderbook_depth}.{symbol}" for symbol in self.symbols]

        # Subscribe to all channels
        subscribe_msg = {
            "op": "subscribe",
            "args": channels
        }
        await self.websocket.send(json.dumps(subscribe_msg))
        self.logger.info(f"Subscribed to orderbook channels: {channels}")

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

            # Route to orderbook handler based on topic prefix
            if topic.startswith('orderbook.'):
                await self._process_orderbook_update(data)

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    async def _process_orderbook_update(self, data: dict):
        """Process orderbook update and store in Redis.

        Args:
            data: Orderbook update data (snapshot or delta)
        """
        try:
            update_type = data.get('type', '')  # 'snapshot' or 'delta'
            ob_data = data.get('data', {})
            symbol = ob_data.get('s', '')

            if not symbol:
                return

            # Extract base coin (e.g., BTC from BTCUSDT)
            base_coin = self._extract_base_coin(symbol)

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

                # Apply bid updates (validate entry length to prevent IndexError)
                for entry in ob_data.get('b', []):
                    if len(entry) < 2:
                        continue
                    price, qty = entry[0], entry[1]
                    if float(qty) == 0:
                        self._orderbooks[symbol]['bids'].pop(price, None)
                    else:
                        self._orderbooks[symbol]['bids'][price] = qty

                # Apply ask updates (validate entry length to prevent IndexError)
                for entry in ob_data.get('a', []):
                    if len(entry) < 2:
                        continue
                    price, qty = entry[0], entry[1]
                    if float(qty) == 0:
                        self._orderbooks[symbol]['asks'].pop(price, None)
                    else:
                        self._orderbooks[symbol]['asks'][price] = qty

                self._orderbooks[symbol]['update_id'] = ob_data.get('u', 0)

            # Prepare sorted orderbook for Redis storage
            ob = self._orderbooks.get(symbol, {})
            if not ob:
                return

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

            # Calculate spread and mid_price
            spread = None
            mid_price = None
            if sorted_bids and sorted_asks:
                # Validate nested structure before indexing
                if len(sorted_bids[0]) < 1 or len(sorted_asks[0]) < 1:
                    self.logger.warning(f"Malformed orderbook entry for {symbol}")
                    return
                best_bid = float(sorted_bids[0][0])
                best_ask = float(sorted_asks[0][0])
                spread = best_ask - best_bid
                # Skip storing if spread is invalid (crossed book)
                if spread < 0:
                    self.logger.warning(f"Invalid spread for {symbol}: {spread} (crossed book)")
                    del self._orderbooks[symbol]  # Clear corrupted state to force fresh snapshot
                    return
                mid_price = (best_bid + best_ask) / 2

            # Store in Redis using public API
            redis_key = f"{self.redis_prefix}:{base_coin}"
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
            self.logger.error(f"Error processing orderbook update: {e}")

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        self.logger.info("Bybit Futures Orderbook Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('bybit')
    service_config = config.get('services', {}).get('futures_orderbook', {})

    service = BybitFuturesOrderbookService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

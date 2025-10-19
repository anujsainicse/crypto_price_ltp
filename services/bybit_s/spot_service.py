"""Bybit Spot Price Service."""

import asyncio
import json
import websockets
from typing import Optional
from datetime import datetime

from core.base_service import BaseService


class BybitSpotService(BaseService):
    """Service for streaming Bybit spot prices via WebSocket."""

    def __init__(self, config: dict):
        """Initialize Bybit Spot Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Bybit-Spot", config)
        self.ws_url = config.get('websocket_url', 'wss://stream.bybit.com/v5/public/spot')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.max_reconnect_attempts = config.get('max_reconnect_attempts', 10)
        self.redis_prefix = config.get('redis_prefix', 'bybit_spot')
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None

    async def start(self):
        """Start the Bybit spot price streaming service."""
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

        while self.running and reconnect_attempts < self.max_reconnect_attempts:
            try:
                await self._connect_and_stream()
                reconnect_attempts = 0  # Reset on successful connection
            except Exception as e:
                reconnect_attempts += 1
                self.logger.error(
                    f"Connection error (attempt {reconnect_attempts}/{self.max_reconnect_attempts}): {e}"
                )

                if reconnect_attempts < self.max_reconnect_attempts:
                    self.logger.info(f"Reconnecting in {self.reconnect_interval} seconds...")
                    await asyncio.sleep(self.reconnect_interval)
                else:
                    self.logger.error("Max reconnection attempts reached")
                    break

    async def _connect_and_stream(self):
        """Connect to WebSocket and stream prices."""
        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=10
        ) as websocket:
            self.websocket = websocket
            self.logger.info("WebSocket connected successfully")

            # Subscribe to tickers
            await self._subscribe_to_tickers()

            # Listen for messages
            async for message in websocket:
                if not self.running:
                    break

                try:
                    await self._handle_message(message)
                except Exception as e:
                    self.logger.error(f"Error handling message: {e}")

    async def _subscribe_to_tickers(self):
        """Subscribe to ticker updates for configured symbols."""
        if not self.websocket:
            return

        # Subscribe to tickers.{symbol} for each symbol
        for symbol in self.symbols:
            subscribe_msg = {
                "op": "subscribe",
                "args": [f"tickers.{symbol}"]
            }
            await self.websocket.send(json.dumps(subscribe_msg))
            self.logger.info(f"Subscribed to tickers.{symbol}")

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

            # Handle ticker updates
            if data.get('topic', '').startswith('tickers.'):
                await self._process_ticker_update(data)

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
            ticker_data = data.get('data', {})
            symbol = ticker_data.get('symbol', '')
            last_price = ticker_data.get('lastPrice')

            if not symbol or not last_price:
                return

            # Extract base coin (e.g., BTC from BTCUSDT)
            base_coin = symbol.replace('USDT', '')

            # Store in Redis
            redis_key = f"{self.redis_prefix}:{base_coin}"
            success = self.redis_client.set_price_data(
                key=redis_key,
                price=float(last_price),
                symbol=symbol,
                additional_data={
                    'volume_24h': ticker_data.get('volume24h', '0'),
                    'high_24h': ticker_data.get('highPrice24h', '0'),
                    'low_24h': ticker_data.get('lowPrice24h', '0'),
                    'price_change_percent': ticker_data.get('price24hPcnt', '0')
                }
            )

            if success:
                self.logger.debug(
                    f"Updated {base_coin}: ${last_price} "
                    f"(24h change: {ticker_data.get('price24hPcnt', '0')}%)"
                )

        except Exception as e:
            self.logger.error(f"Error processing ticker update: {e}")

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        self.logger.info("Bybit Spot Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('bybit')
    service_config = config.get('services', {}).get('spot', {})

    service = BybitSpotService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

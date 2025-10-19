"""CoinDCX Futures LTP Service."""

import asyncio
import json
import websockets
from typing import Optional
from datetime import datetime

from core.base_service import BaseService


class CoinDCXFuturesLTPService(BaseService):
    """Service for streaming CoinDCX futures LTP via WebSocket."""

    def __init__(self, config: dict):
        """Initialize CoinDCX Futures LTP Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("CoinDCX-Futures-LTP", config)
        self.ws_url = config.get('websocket_url', 'wss://futures-stream.coindcx.com')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.max_reconnect_attempts = config.get('max_reconnect_attempts', 10)
        self.redis_prefix = config.get('redis_prefix', 'coindcx_futures')
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None

    async def start(self):
        """Start the CoinDCX futures LTP streaming service."""
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

            # Subscribe to symbols
            await self._subscribe_to_symbols()

            # Listen for messages
            async for message in websocket:
                if not self.running:
                    break

                try:
                    await self._handle_message(message)
                except Exception as e:
                    self.logger.error(f"Error handling message: {e}")

    async def _subscribe_to_symbols(self):
        """Subscribe to LTP updates for configured symbols."""
        if not self.websocket:
            return

        for symbol in self.symbols:
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": [f"{symbol}@ticker"],
                "id": 1
            }
            await self.websocket.send(json.dumps(subscribe_msg))
            self.logger.info(f"Subscribed to {symbol}@ticker")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message.

        Args:
            message: Raw WebSocket message
        """
        try:
            data = json.loads(message)

            # Handle subscription confirmation
            if 'result' in data:
                self.logger.debug(f"Subscription result: {data}")
                return

            # Handle ticker updates
            if 'data' in data and 'e' in data:
                if data['e'] == 'ticker':
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
            symbol = ticker_data.get('s', '')  # Symbol
            last_price = ticker_data.get('c')  # Close/Last price

            if not symbol or not last_price:
                return

            # Extract base coin (e.g., BTC from B-BTC_USDT)
            base_coin = symbol.replace('B-', '').split('_')[0]

            # Store in Redis - append to existing data if available
            redis_key = f"{self.redis_prefix}:{base_coin}"

            # Get existing data to preserve funding rates
            existing_data = self.redis_client.get_price_data(redis_key) or {}

            # Prepare additional data
            additional_data = {
                'volume_24h': ticker_data.get('v', '0'),
                'high_24h': ticker_data.get('h', '0'),
                'low_24h': ticker_data.get('l', '0'),
                'price_change': ticker_data.get('p', '0'),
                'price_change_percent': ticker_data.get('P', '0')
            }

            # Preserve funding rate data if exists
            if 'current_funding_rate' in existing_data:
                additional_data['current_funding_rate'] = existing_data['current_funding_rate']
            if 'estimated_funding_rate' in existing_data:
                additional_data['estimated_funding_rate'] = existing_data['estimated_funding_rate']
            if 'funding_timestamp' in existing_data:
                additional_data['funding_timestamp'] = existing_data['funding_timestamp']

            # Store in Redis
            success = self.redis_client.set_price_data(
                key=redis_key,
                price=float(last_price),
                symbol=symbol,
                additional_data=additional_data
            )

            if success:
                self.logger.debug(
                    f"Updated {base_coin}: ${last_price} "
                    f"(24h change: {ticker_data.get('P', '0')}%)"
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

        self.logger.info("CoinDCX Futures LTP Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('coindcx')
    service_config = config.get('services', {}).get('futures_ltp', {})

    service = CoinDCXFuturesLTPService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

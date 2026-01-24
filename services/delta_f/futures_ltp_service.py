"""Delta Exchange Futures LTP Service."""

import asyncio
import json
import math
import websockets
from typing import Optional
from datetime import datetime

from core.base_service import BaseService


class DeltaFuturesLTPService(BaseService):
    """Service for streaming Delta Exchange futures LTP via WebSocket.

    Redis Key Patterns:
        Ticker: {redis_prefix}:{base_coin} (Hash)
    """

    def __init__(self, config: dict):
        """Initialize Delta Futures LTP Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Delta-Futures-LTP", config)
        self.ws_url = config.get('websocket_url', 'wss://socket.delta.exchange')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.redis_prefix = config.get('redis_prefix', 'delta_futures')
        self.redis_ttl = config.get('redis_ttl', 60)
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        # Exponential backoff delays as per CLAUDE.md: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

    async def start(self):
        """Start the Delta futures LTP streaming service."""
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
                await self._connect_and_stream()
                reconnect_attempts = 0  # Reset on successful connection
            except Exception as e:
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
        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=30  # Matches CLAUDE.md specification
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
        """Subscribe to ticker/trade updates for configured symbols."""
        if not self.websocket:
            return

        for symbol in self.symbols:
            # Delta Exchange subscription format
            # Channel format: v2/ticker or trades
            subscribe_msg = {
                "type": "subscribe",
                "payload": {
                    "channels": [
                        {
                            "name": "v2/ticker",
                            "symbols": [symbol]
                        }
                    ]
                }
            }
            await self.websocket.send(json.dumps(subscribe_msg))
            self.logger.info(f"Subscribed to {symbol}")
            await asyncio.sleep(0.1)  # Small delay between subscriptions

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message.

        Args:
            message: Raw WebSocket message
        """
        try:
            data = json.loads(message)

            # Handle subscription confirmation
            if data.get('type') == 'subscriptions':
                self.logger.debug(f"Subscription confirmed: {data}")
                return

            # Handle ticker updates
            if data.get('type') == 'v2/ticker':
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
            # Delta format is usually like: BTCUSD, ETHUSDT
            base_coin = ticker_data.replace('USDT', '').replace('USD', '').replace('PERP', '')

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

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        self.logger.info("Delta Futures LTP Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('delta')
    service_config = config.get('services', {}).get('futures_ltp', {})

    service = DeltaFuturesLTPService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

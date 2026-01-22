"""HyperLiquid Perpetual Price Service."""

import asyncio
import json
import websockets
from typing import Optional
from datetime import datetime

from core.base_service import BaseService


class HyperLiquidPerpetualService(BaseService):
    """Service for streaming HyperLiquid perpetual prices via WebSocket.

    Redis Key Patterns:
        Ticker: {redis_prefix}:{symbol} (Hash)
    """

    def __init__(self, config: dict):
        """Initialize HyperLiquid Perpetual Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("HyperLiquid-Perpetual", config)
        self.ws_url = config.get('websocket_url', 'wss://api.hyperliquid.xyz/ws')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.max_reconnect_attempts = config.get('max_reconnect_attempts', 10)
        self.redis_prefix = config.get('redis_prefix', 'hyperliquid_perp')
        self.redis_ttl = config.get('redis_ttl', 60)
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None

    async def start(self):
        """Start the HyperLiquid perpetual price streaming service."""
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

            # Subscribe to allMids for all mid prices
            await self._subscribe()

            # Listen for messages
            async for message in websocket:
                if not self.running:
                    break

                try:
                    await self._handle_message(message)
                except Exception as e:
                    self.logger.error(f"Error handling message: {e}")

    async def _subscribe(self):
        """Subscribe to allMids channel for mid prices."""
        if not self.websocket:
            return

        # HyperLiquid uses allMids to get all mid prices in one subscription
        subscribe_msg = {
            "method": "subscribe",
            "subscription": {
                "type": "allMids"
            }
        }
        await self.websocket.send(json.dumps(subscribe_msg))
        self.logger.info("Subscribed to allMids channel")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message.

        Args:
            message: Raw WebSocket message
        """
        try:
            data = json.loads(message)

            # Handle subscription confirmation
            if data.get('channel') == 'subscriptionResponse':
                self.logger.debug(f"Subscription confirmed: {data}")
                return

            # Handle allMids updates
            if data.get('channel') == 'allMids':
                await self._process_mids_update(data)

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    async def _process_mids_update(self, data: dict):
        """Process allMids update and store in Redis.

        Args:
            data: AllMids update data containing mid prices for all symbols
        """
        try:
            mids_data = data.get('data', {}).get('mids', {})

            if not mids_data:
                return

            # Process only configured symbols
            for symbol in self.symbols:
                if symbol in mids_data:
                    mid_price = mids_data[symbol]

                    try:
                        price = float(mid_price)
                        if price <= 0:
                            continue
                    except (ValueError, TypeError):
                        continue

                    # Store in Redis
                    redis_key = f"{self.redis_prefix}:{symbol}"
                    success = self.redis_client.set_price_data(
                        key=redis_key,
                        price=price,
                        symbol=symbol,
                        additional_data={
                            'price_type': 'mid',
                            'contract_type': 'perpetual'
                        },
                        ttl=self.redis_ttl
                    )

                    if success:
                        self.logger.debug(f"Updated {symbol}: ${price}")

        except Exception as e:
            self.logger.error(f"Error processing mids update: {e}")

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        self.logger.info("HyperLiquid Perpetual Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('hyperliquid')
    service_config = config.get('services', {}).get('perpetual', {})

    service = HyperLiquidPerpetualService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

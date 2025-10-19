"""Delta Exchange Options Service."""

import asyncio
import json
import websockets
from typing import Optional
from datetime import datetime

from core.base_service import BaseService


class DeltaOptionsService(BaseService):
    """Service for streaming Delta Exchange options data via WebSocket."""

    def __init__(self, config: dict):
        """Initialize Delta Options Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Delta-Options", config)
        self.ws_url = config.get('websocket_url', 'wss://socket.delta.exchange')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.max_reconnect_attempts = config.get('max_reconnect_attempts', 10)
        self.redis_prefix = config.get('redis_prefix', 'delta_options')
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None

    async def start(self):
        """Start the Delta options streaming service."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        if not self.symbols:
            self.logger.error("No symbols configured")
            return

        self.running = True
        self.logger.info(f"Starting WebSocket connection to {self.ws_url}")
        self.logger.info(f"Monitoring options: {', '.join(self.symbols)}")

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
        """Connect to WebSocket and stream options data."""
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
        """Subscribe to options ticker/trade updates for configured symbols."""
        if not self.websocket:
            return

        for symbol in self.symbols:
            # Delta Exchange subscription format for options
            # Channel format: v2/ticker for options symbols
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

            # Log ALL messages to see what we're receiving
            self.logger.info(f"Received message type: {data.get('type')}")
            self.logger.debug(f"Full message: {data}")

            # Handle subscription confirmation
            if data.get('type') == 'subscriptions':
                self.logger.info(f"Subscription confirmed: {data}")
                return

            # Handle ticker updates
            if data.get('type') == 'v2/ticker':
                await self._process_ticker_update(data)
            else:
                # Log unhandled message types
                self.logger.warning(f"Unhandled message type: {data.get('type')}")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

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
                price=float(price),
                symbol=symbol,
                additional_data=additional_data
            )

            if success:
                self.logger.debug(
                    f"Updated {symbol}: ${price} "
                    f"(Type: {option_info.get('type')}, Strike: {option_info.get('strike')})"
                )

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

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

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

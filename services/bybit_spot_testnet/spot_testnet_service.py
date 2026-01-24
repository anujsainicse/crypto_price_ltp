"""Bybit Spot TestNet Price Service."""

import asyncio
import json
import math
import time
import websockets
from typing import Optional
from datetime import datetime

from core.base_service import BaseService


class BybitSpotTestnetService(BaseService):
    """Service for streaming Bybit Spot TestNet prices via WebSocket.

    Redis Key Patterns:
        Ticker: {redis_prefix}:{base_coin} (Hash)
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
                    reconnect_attempts = 0

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

            try:
                price = float(last_price)
                if not math.isfinite(price) or price <= 0:
                    self.logger.warning(f"Invalid price for {symbol}: {last_price}")
                    return
            except (ValueError, TypeError):
                self.logger.warning(f"Cannot convert price to float for {symbol}: {last_price}")
                return

            # Extract base coin (e.g., BTC from BTCUSDT)
            base_coin = self._extract_base_coin(symbol)

            # Store in Redis
            redis_key = f"{self.redis_prefix}:{base_coin}"
            success = self.redis_client.set_price_data(
                key=redis_key,
                price=price,
                symbol=symbol,
                additional_data={
                    'volume_24h': ticker_data.get('volume24h', '0'),
                    'high_24h': ticker_data.get('highPrice24h', '0'),
                    'low_24h': ticker_data.get('lowPrice24h', '0'),
                    'price_change_percent': ticker_data.get('price24hPcnt', '0')
                },
                ttl=self.redis_ttl
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

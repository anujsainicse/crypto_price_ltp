"""CoinDCX Futures LTP Service using Socket.IO."""

import asyncio
import json
import math
import socketio
import time
from typing import Optional
from datetime import datetime

from core.base_service import BaseService


class CoinDCXFuturesLTPService(BaseService):
    """Service for streaming CoinDCX futures LTP via Socket.IO.

    Redis Key Patterns:
        Ticker: {redis_prefix}:{base_coin} (Hash)
    """

    def __init__(self, config: dict):
        """Initialize CoinDCX Futures LTP Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("CoinDCX-Futures-LTP", config)
        self.ws_url = config.get('websocket_url', 'wss://stream.coindcx.com')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.redis_prefix = config.get('redis_prefix', 'coindcx_futures')
        self.redis_ttl = config.get('redis_ttl', 60)
        self.sio: Optional[socketio.AsyncClient] = None
        self.ws_connected = False
        self.ping_task: Optional[asyncio.Task] = None
        # Exponential backoff delays as per CLAUDE.md: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

    async def start(self):
        """Start the CoinDCX futures LTP streaming service."""
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
                reconnect_attempts = 0  # Reset on successful connection
            except Exception as e:
                # Reset attempts if connection was stable for >30s
                connection_duration = time.time() - connection_start_time
                if connection_duration > 30:
                    reconnect_attempts = 0

                reconnect_attempts += 1
                self.logger.warning(f"Connection error (attempt {reconnect_attempts}): {e}")

                # Cleanup
                await self._cleanup_connection()

                # Exponential backoff with 60s cap (never give up)
                delay = self.backoff_delays[min(reconnect_attempts - 1, len(self.backoff_delays) - 1)]
                self.logger.info(f"Reconnecting in {delay} seconds...")
                await asyncio.sleep(delay)

    async def _connect_and_stream(self):
        """Connect to Socket.IO and stream prices."""
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

        # Subscribe to trade channels
        await self._subscribe_to_trades()

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
            # Cancel ping task immediately on disconnect
            if self.ping_task and not self.ping_task.done():
                self.ping_task.cancel()
                try:
                    await self.ping_task
                except asyncio.CancelledError:
                    pass
                self.ping_task = None

        @self.sio.event
        async def connect_error(data):
            self.logger.error(f"Socket.IO connection error: {data}")

        @self.sio.on('new-trade')
        async def handle_new_trade(data):
            """Handle incoming trade messages."""
            await self._handle_trade_message(data)

    async def _subscribe_to_trades(self):
        """Subscribe to trade updates for configured symbols."""
        for symbol in self.symbols:
            try:
                channel = f"{symbol}@trades-futures"
                await self.sio.emit('join', {'channelName': channel})
                self.logger.info(f"Subscribed to {channel}")
                await asyncio.sleep(0.1)  # Small delay between subscriptions
            except Exception as e:
                self.logger.error(f"Failed to subscribe to {symbol}: {e}")

    async def _handle_trade_message(self, data):
        """Handle incoming trade message and store in Redis.

        Args:
            data: Trade message data
        """
        try:
            if not isinstance(data, dict) or 'data' not in data:
                return

            # Parse trade data
            trade_data = data.get('data')
            if isinstance(trade_data, str):
                trade_data = json.loads(trade_data)

            # Extract symbol and price
            symbol = trade_data.get('s')  # Symbol
            price = trade_data.get('p')    # Price

            if not symbol or not price:
                return

            # Validate price before float conversion
            try:
                price_float = float(price)
                if not math.isfinite(price_float) or price_float <= 0:
                    self.logger.warning(f"Invalid price for {symbol}: {price}")
                    return
            except (ValueError, TypeError):
                self.logger.warning(f"Cannot convert price to float for {symbol}: {price}")
                return

            # Extract base coin (e.g., BTC from B-BTC_USDT)
            base_coin = symbol.replace('B-', '').split('_')[0]

            # Store in Redis - preserve funding rates if available
            redis_key = f"{self.redis_prefix}:{base_coin}"

            # Get existing data to preserve funding rates
            existing_data = self.redis_client.get_price_data(redis_key) or {}

            # Prepare additional data
            additional_data = {}

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
                price=price_float,
                symbol=symbol,
                additional_data=additional_data,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(f"Updated {base_coin}: ${price_float}")

        except Exception as e:
            self.logger.error(f"Error processing trade message: {e}")

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

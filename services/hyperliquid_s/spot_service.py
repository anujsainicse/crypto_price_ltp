"""HyperLiquid Spot Price Service."""

import asyncio
import json
import math
import time
import websockets
from typing import Optional, Dict, List, Any, Deque
from datetime import datetime
from collections import deque

from core.base_service import BaseService


class HyperLiquidSpotService(BaseService):
    """Service for streaming HyperLiquid spot prices, orderbooks, and trades via WebSocket.

    Redis Key Patterns:
        Ticker:    {redis_prefix}:{symbol} (Hash)
        Orderbook: {orderbook_redis_prefix}:{symbol} (Hash)
        Trades:    {trades_redis_prefix}:{symbol} (Hash)
    """

    def __init__(self, config: dict):
        """Initialize HyperLiquid Spot Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("HyperLiquid-Spot", config)
        self.ws_url = config.get('websocket_url', 'wss://api.hyperliquid.xyz/ws')
        self.symbols = config.get('symbols', [])
        self.redis_prefix = config.get('redis_prefix', 'hyperliquid_spot')
        self.redis_ttl = config.get('redis_ttl', 60)

        # Orderbook and Trades configuration
        self.orderbook_enabled = config.get('orderbook_enabled', True)
        self.trades_enabled = config.get('trades_enabled', True)
        self.orderbook_depth = config.get('orderbook_depth', 50)
        self.trades_limit = config.get('trades_limit', 50)
        self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'hyperliquid_spot_ob')
        self.trades_redis_prefix = config.get('trades_redis_prefix', 'hyperliquid_spot_trades')

        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        # Exponential backoff delays as per CLAUDE.md: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

        # In-memory state
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._trades: Dict[str, Deque] = {}

    async def start(self):
        """Start the HyperLiquid spot price streaming service."""
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
                reconnect_attempts = 0  # Reset on clean exit
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
        """Subscribe to channels."""
        if not self.websocket:
            return

        # 1. Subscribe to allMids (efficient global ticker)
        await self.websocket.send(json.dumps({
            "method": "subscribe",
            "subscription": {"type": "allMids"}
        }))
        self.logger.info("Subscribed to allMids channel")

        # 2. Subscribe to Orderbook and Trades per symbol
        for symbol in self.symbols:
            # Note: Hyperliquid symbols are raw (e.g., "BTC", "ETH")

            if self.orderbook_enabled:
                await self.websocket.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "l2Book", "coin": symbol}
                }))
                self.logger.debug(f"Subscribed to l2Book for {symbol}")

            if self.trades_enabled:
                await self.websocket.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "trades", "coin": symbol}
                }))
                self.logger.debug(f"Subscribed to trades for {symbol}")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message.

        Args:
            message: Raw WebSocket message
        """
        try:
            data = json.loads(message)
            channel = data.get('channel')

            # Handle subscription confirmation
            if channel == 'subscriptionResponse':
                # self.logger.debug(f"Subscription confirmed: {data}")
                return

            # Route by channel
            if channel == 'allMids':
                await self._process_mids_update(data)
            elif channel == 'l2Book':
                await self._process_l2book_update(data)
            elif channel == 'trades':
                await self._process_trade_update(data)

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

                    # Validate price before float conversion
                    try:
                        price = float(mid_price)
                        if not math.isfinite(price) or price <= 0:
                            self.logger.warning(f"Invalid price for {symbol}: {mid_price}")
                            continue
                    except (ValueError, TypeError):
                        self.logger.warning(f"Cannot convert price to float for {symbol}: {mid_price}")
                        continue

                    # Store in Redis
                    redis_key = f"{self.redis_prefix}:{symbol}"
                    success = self.redis_client.set_price_data(
                        key=redis_key,
                        price=price,
                        symbol=symbol,
                        additional_data={
                            'price_type': 'mid'
                        },
                        ttl=self.redis_ttl
                    )
                    if not success:
                        self.logger.warning(f"Failed to update price in Redis for {symbol}")

                    # Note: We don't log every mid update as it's too high frequency

        except Exception as e:
            self.logger.error(f"Error processing mids update: {e}")

    async def _process_l2book_update(self, data: dict):
        """Process l2Book snapshot."""
        try:
            # Format: {"channel": "l2Book", "data": {"coin": "BTC", "time": 123, "levels": [[...], [...]]}}
            # levels[0] are bids, levels[1] are asks. Each entry: {"px": "...", "sz": "...", "n": ...}

            content = data.get('data', {})
            symbol = content.get('coin')
            if not symbol or symbol not in self.symbols:
                return

            levels = content.get('levels', [])
            if not levels or len(levels) < 2:
                return

            # Parse bids (levels[0]) and asks (levels[1])
            raw_bids = levels[0]
            raw_asks = levels[1]

            def parse_levels(items):
                parsed = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    try:
                        px = float(item.get('px', 0))
                        sz = float(item.get('sz', 0))
                        if px > 0 and sz > 0 and math.isfinite(px) and math.isfinite(sz):
                            parsed.append([px, sz])
                    except (ValueError, TypeError):
                        continue
                return parsed

            # Sort Bids (Desc)
            bids = sorted(parse_levels(raw_bids), key=lambda x: x[0], reverse=True)[:self.orderbook_depth]
            # Sort Asks (Asc)
            asks = sorted(parse_levels(raw_asks), key=lambda x: x[0])[:self.orderbook_depth]

            # Validate empty orderbook
            if not bids or not asks:
                return

            # Validate spread before updating state
            best_bid = bids[0][0] if bids else 0
            best_ask = asks[0][0] if asks else 0
            spread = 0.0
            mid_price = 0.0

            if best_bid > 0 and best_ask > 0:
                if best_bid >= best_ask:
                    self.logger.warning(f"Crossed book for {symbol}: Bid {best_bid} >= Ask {best_ask}. Dropping update.")
                    return
                spread = best_ask - best_bid
                mid_price = (best_bid + best_ask) / 2

            # Update state
            self._orderbooks[symbol] = {
                'bids': bids,
                'asks': asks,
                'timestamp': content.get('time')
            }

            # Store in Redis
            redis_key = f"{self.orderbook_redis_prefix}:{symbol}"
            success = self.redis_client.set_orderbook_data(
                key=redis_key,
                bids=bids,
                asks=asks,
                spread=spread,
                mid_price=mid_price,
                update_id=str(content.get('time')),
                original_symbol=symbol,
                ttl=self.redis_ttl
            )
            if not success:
                self.logger.warning(f"Failed to update orderbook in Redis for {symbol}")

        except Exception as e:
            self.logger.error(f"Error processing orderbook: {e}")

    async def _process_trade_update(self, data: dict):
        """Process trades update."""
        try:
            # Format: {"channel": "trades", "data": [{"coin": "BTC", "side": "B", "px": "...", "sz": "...", "time": ...}, ...]}
            trades_list = data.get('data', [])
            if not trades_list:
                return

            # Group trades by symbol to handle mixed batches
            trades_by_symbol = {}

            for trade in trades_list:
                if not isinstance(trade, dict):
                    continue

                symbol = trade.get('coin')
                if not symbol or symbol not in self.symbols:
                    continue

                if symbol not in trades_by_symbol:
                    trades_by_symbol[symbol] = []

                trades_by_symbol[symbol].append(trade)

            # Process each symbol's trades
            for symbol, symbol_trades in trades_by_symbol.items():
                if symbol not in self._trades:
                    self._trades[symbol] = deque(maxlen=self.trades_limit)

                for trade in symbol_trades:
                    try:
                        px = float(trade.get('px', 0))
                        sz = float(trade.get('sz', 0))

                        # Hyperliquid: 'B' = Bid (Buy), 'A' = Ask (Sell)
                        raw_side = trade.get('side')
                        side = 'Buy' if raw_side == 'B' else 'Sell' if raw_side == 'A' else str(raw_side)

                        if px > 0 and sz > 0 and math.isfinite(px) and math.isfinite(sz):
                            self._trades[symbol].append({
                                'p': px,
                                'q': sz,
                                's': side,
                                't': trade.get('time'),
                                'id': str(trade.get('hash', trade.get('time') or f"unknown_{int(time.time()*1000)}")) # Use hash if available, else time, else fallback
                            })
                    except (ValueError, TypeError):
                        continue

                # Store in Redis
                redis_key = f"{self.trades_redis_prefix}:{symbol}"
                success = self.redis_client.set_trades_data(
                    key=redis_key,
                    trades=list(self._trades[symbol]),
                    original_symbol=symbol,
                    ttl=self.redis_ttl
                )
                if not success:
                    self.logger.warning(f"Failed to update trades in Redis for {symbol}")

        except Exception as e:
             self.logger.error(f"Error processing trades: {e}")

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        self.logger.info("HyperLiquid Spot Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('hyperliquid')
    service_config = config.get('services', {}).get('spot', {})

    service = HyperLiquidSpotService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

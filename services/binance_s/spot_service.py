"""Binance Spot Price Service.

Uses Binance combined WebSocket streams for LTP (miniTicker),
orderbook (partial book depth), and trades on a single connection.

Stream names use lowercase symbols (Binance requirement).
Data payloads return uppercase symbols.
"""

import asyncio
import json
import math
import time
import websockets
from collections import deque
from typing import Optional, Dict, Any

from core.base_service import BaseService


class BinanceSpotService(BaseService):
    """Service for streaming Binance spot prices via WebSocket.

    Uses the combined streams endpoint to subscribe to miniTicker,
    partial book depth, and trade streams for all configured symbols
    on a single WebSocket connection.

    Redis Key Patterns:
        Ticker: {redis_prefix}:{base_coin} (Hash)
        Orderbook: {orderbook_redis_prefix}:{base_coin} (Hash)
        Trades: {trades_redis_prefix}:{base_coin} (Hash)
    """

    def __init__(self, config: dict):
        """Initialize Binance Spot Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Binance-Spot", config)
        self.ws_base_url = config.get('websocket_url', 'wss://stream.binance.com:9443')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.redis_prefix = config.get('redis_prefix', 'binance_spot')
        self.redis_ttl = config.get('redis_ttl', 60)
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        # Exponential backoff: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

        # Orderbook configuration
        self.orderbook_enabled = config.get('orderbook_enabled', False)
        self.orderbook_depth = config.get('orderbook_depth', 20)
        self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'binance_spot_ob')

        # Trades configuration
        self.trades_enabled = config.get('trades_enabled', False)
        self.trades_limit = config.get('trades_limit', 50)
        self.trades_redis_prefix = config.get('trades_redis_prefix', 'binance_spot_trades')

        # Quote currencies for symbol parsing (order matters - longest first)
        self.quote_currencies = config.get('quote_currencies', ['USDT', 'USDC', 'BTC', 'ETH'])

        # In-memory state for trades (no orderbook state needed - partial depth gives full snapshots)
        self._trades: Dict[str, deque] = {}

        # Map lowercase stream symbol prefix to uppercase config symbol for orderbook routing
        # e.g., 'btcusdt' -> 'BTCUSDT' (orderbook payload has no symbol field)
        self._stream_symbol_map: Dict[str, str] = {
            s.lower(): s for s in self.symbols
        }

    def _extract_base_coin(self, symbol: str) -> str:
        """Extract base coin from symbol by removing quote currency.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT', 'ETHBTC')

        Returns:
            Base coin (e.g., 'BTC', 'ETH')
        """
        for quote in self.quote_currencies:
            if symbol.endswith(quote):
                return symbol[:-len(quote)]
        return symbol

    def _build_stream_url(self) -> str:
        """Build combined streams URL from configured symbols.

        Binance combined streams URL format:
            wss://stream.binance.com:9443/stream?streams=stream1/stream2/...

        Stream names must be lowercase.

        Returns:
            Full WebSocket URL with all stream subscriptions
        """
        streams = []
        for symbol in self.symbols:
            s = symbol.lower()
            # Always subscribe to miniTicker for LTP
            streams.append(f"{s}@miniTicker")

            if self.orderbook_enabled:
                streams.append(f"{s}@depth{self.orderbook_depth}@100ms")

            if self.trades_enabled:
                streams.append(f"{s}@trade")

        return f"{self.ws_base_url}/stream?streams={'/'.join(streams)}"

    async def start(self):
        """Start the Binance spot price streaming service."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        if not self.symbols:
            self.logger.error("No symbols configured")
            return

        self.running = True
        stream_url = self._build_stream_url()
        self.logger.info(f"Starting WebSocket connection to {self.ws_base_url}")
        self.logger.info(f"Monitoring symbols: {', '.join(self.symbols)}")

        reconnect_attempts = 0

        while self.running:
            try:
                connection_start_time = time.time()
                await self._connect_and_stream()
                reconnect_attempts = 0
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
        # Clear stale state on reconnection
        self._trades.clear()

        stream_url = self._build_stream_url()

        async with websockets.connect(
            stream_url,
            ping_interval=20,
            ping_timeout=30
        ) as websocket:
            self.websocket = websocket
            self.logger.info("WebSocket connected successfully")

            # No explicit subscribe needed - combined URL auto-subscribes
            stream_count = len(self.symbols) * (1 + int(self.orderbook_enabled) + int(self.trades_enabled))
            self.logger.info(f"Subscribed to {stream_count} streams on combined connection")

            # Listen for messages
            async for message in websocket:
                if not self.running:
                    break

                try:
                    await self._handle_message(message)
                except Exception as e:
                    self.logger.error(f"Error handling message: {e}")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message.

        Combined stream messages are wrapped:
            {"stream": "btcusdt@miniTicker", "data": {...}}

        Args:
            message: Raw WebSocket message
        """
        try:
            data = json.loads(message)

            # Handle subscription/property responses (have 'result' field)
            if 'result' in data:
                self.logger.debug(f"Response: {data}")
                return

            stream = data.get('stream', '')
            payload = data.get('data')

            if not stream or payload is None:
                return

            # Route based on stream name suffix
            if '@miniTicker' in stream:
                await self._process_ticker_update(payload)
            elif '@depth' in stream:
                # Extract symbol from stream name for orderbook (payload has no symbol)
                # e.g., 'btcusdt@depth20@100ms' -> 'btcusdt'
                stream_symbol = stream.split('@')[0]
                symbol = self._stream_symbol_map.get(stream_symbol, stream_symbol.upper())
                await self._process_orderbook_update(payload, symbol)
            elif '@trade' in stream:
                await self._process_trade_update(payload)

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    async def _process_ticker_update(self, ticker_data: dict):
        """Process miniTicker update and store LTP in Redis.

        Binance miniTicker fields:
            e: Event type (24hrMiniTicker)
            E: Event time (ms)
            s: Symbol (BTCUSDT)
            c: Close/last price (LTP)
            o: Open price (24h)
            h: High price (24h)
            l: Low price (24h)
            v: Total traded base asset volume (24h)
            q: Total traded quote asset volume (24h)

        Args:
            ticker_data: Parsed miniTicker payload
        """
        try:
            symbol = ticker_data.get('s', '')
            last_price = ticker_data.get('c')

            if not symbol or not last_price:
                return

            # Validate price
            try:
                price_float = float(last_price)
                if not math.isfinite(price_float) or price_float <= 0:
                    self.logger.warning(f"Invalid price for {symbol}: {last_price}")
                    return
            except (ValueError, TypeError):
                self.logger.warning(f"Cannot convert price to float for {symbol}: {last_price}")
                return

            # Compute price change percent: ((close - open) / open) * 100
            price_change_percent = '0'
            try:
                open_price = float(ticker_data.get('o', 0))
                if open_price > 0 and math.isfinite(open_price):
                    price_change_percent = str(round(((price_float - open_price) / open_price) * 100, 4))
            except (ValueError, TypeError):
                pass

            base_coin = self._extract_base_coin(symbol)

            redis_key = f"{self.redis_prefix}:{base_coin}"
            success = self.redis_client.set_price_data(
                key=redis_key,
                price=price_float,
                symbol=symbol,
                additional_data={
                    'volume_24h': ticker_data.get('v', '0'),
                    'high_24h': ticker_data.get('h', '0'),
                    'low_24h': ticker_data.get('l', '0'),
                    'price_change_percent': price_change_percent
                },
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated {base_coin}: ${last_price} "
                    f"(24h change: {price_change_percent}%)"
                )
            else:
                self.logger.warning(f"Failed to write ticker to Redis for {base_coin}")

        except Exception as e:
            self.logger.error(f"Error processing ticker update: {e}")

    async def _process_orderbook_update(self, ob_data: dict, symbol: str):
        """Process partial book depth update and store in Redis.

        Binance partial book depth is a full snapshot each time (no delta management).
        Payload has no symbol field - symbol is extracted from the stream name.

        Fields:
            lastUpdateId: Last update ID
            bids: [[price, qty], ...] (descending by price)
            asks: [[price, qty], ...] (ascending by price)

        Args:
            ob_data: Parsed orderbook payload
            symbol: Uppercase symbol (e.g., 'BTCUSDT')
        """
        try:
            if not isinstance(ob_data, dict):
                return

            raw_bids = ob_data.get('bids', [])
            raw_asks = ob_data.get('asks', [])

            if not raw_bids or not raw_asks:
                return

            # Parse and validate bids (already sorted descending by Binance)
            sorted_bids = []
            for entry in raw_bids:
                if len(entry) < 2:
                    continue
                try:
                    price = float(entry[0])
                    qty = float(entry[1])
                    if math.isfinite(price) and math.isfinite(qty) and price > 0 and qty > 0:
                        sorted_bids.append([entry[0], entry[1]])
                except (ValueError, TypeError):
                    continue

            # Parse and validate asks (already sorted ascending by Binance)
            sorted_asks = []
            for entry in raw_asks:
                if len(entry) < 2:
                    continue
                try:
                    price = float(entry[0])
                    qty = float(entry[1])
                    if math.isfinite(price) and math.isfinite(qty) and price > 0 and qty > 0:
                        sorted_asks.append([entry[0], entry[1]])
                except (ValueError, TypeError):
                    continue

            # Truncate to configured depth
            sorted_bids = sorted_bids[:self.orderbook_depth]
            sorted_asks = sorted_asks[:self.orderbook_depth]

            if not sorted_bids or not sorted_asks:
                return

            base_coin = self._extract_base_coin(symbol)

            # Calculate spread and mid_price
            try:
                best_bid = float(sorted_bids[0][0])
                best_ask = float(sorted_asks[0][0])

                if not math.isfinite(best_bid) or not math.isfinite(best_ask):
                    return

                spread = best_ask - best_bid

                # Detect crossed book (invalid state)
                if spread < 0:
                    self.logger.warning(f"Invalid spread for {symbol}: {spread} (crossed book)")
                    redis_key = f"{self.orderbook_redis_prefix}:{base_coin}"
                    self.redis_client.delete_key(redis_key)
                    return

                mid_price = (best_bid + best_ask) / 2
            except (ValueError, TypeError):
                return

            # Store in Redis
            redis_key = f"{self.orderbook_redis_prefix}:{base_coin}"
            success = self.redis_client.set_orderbook_data(
                key=redis_key,
                bids=sorted_bids,
                asks=sorted_asks,
                spread=spread,
                mid_price=mid_price,
                update_id=ob_data.get('lastUpdateId', 0),
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated orderbook {base_coin}: {len(sorted_bids)} bids, {len(sorted_asks)} asks, "
                    f"spread: {spread}"
                )
            else:
                self.logger.warning(f"Failed to write orderbook to Redis for {base_coin}")

        except Exception as e:
            self.logger.error(f"Error processing orderbook update: {e}")

    async def _process_trade_update(self, trade_data: dict):
        """Process trade update and store in Redis.

        Binance trade fields:
            e: Event type (trade)
            E: Event time (ms)
            s: Symbol (BTCUSDT)
            t: Trade ID
            p: Price
            q: Quantity
            T: Trade time (ms)
            m: Is buyer the market maker? (true = sell, false = buy)
            M: Ignore (deprecated)

        Args:
            trade_data: Parsed trade payload
        """
        try:
            symbol = trade_data.get('s', '')

            try:
                price = float(trade_data.get('p', 0))
                quantity = float(trade_data.get('q', 0))
            except (ValueError, TypeError):
                return

            # Validate required fields, positive values, and finite numbers
            if not symbol or price <= 0 or quantity <= 0:
                return
            if not math.isfinite(price) or not math.isfinite(quantity):
                self.logger.warning(f"Non-finite trade values for {symbol}: price={price}, qty={quantity}")
                return

            base_coin = self._extract_base_coin(symbol)

            # Map side: m=false -> buyer is taker (Buy), m=true -> seller is taker (Sell)
            side = 'Sell' if trade_data.get('m', False) else 'Buy'

            # Build trade ID with None-safe fallback
            raw_trade_id = trade_data.get('t')
            trade_id = str(raw_trade_id) if raw_trade_id is not None else f"binance_{int(time.time() * 1000)}"

            # Initialize deque for this symbol if not exists
            self._trades.setdefault(symbol, deque(maxlen=self.trades_limit))

            # Append trade with compact field names
            self._trades[symbol].append({
                'p': price,                                # price
                'q': quantity,                             # quantity
                's': side,                                 # side (Buy/Sell)
                't': trade_data.get('T', 0),               # trade time (ms)
                'id': trade_id                             # trade id
            })

            # Store in Redis
            redis_key = f"{self.trades_redis_prefix}:{base_coin}"
            trades_list = list(self._trades[symbol])
            success = self.redis_client.set_trades_data(
                key=redis_key,
                trades=trades_list,
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated trades {base_coin}: {len(trades_list)} trades, "
                    f"latest: {price} @ {side}"
                )
            else:
                self.logger.warning(f"Failed to write trades to Redis for {base_coin}")

        except Exception as e:
            self.logger.error(f"Error processing trade update: {e}")

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        self.logger.info("Binance Spot Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('binance')
    service_config = config.get('services', {}).get('spot', {})

    service = BinanceSpotService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

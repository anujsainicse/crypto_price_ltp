"""Binance USD-M Futures WebSocket Service.

Uses Binance combined WebSocket streams for LTP (miniTicker), orderbook
(partial book depth), aggTrade trades, and markPrice (funding rate) on a
single connection to fstream.binance.com.

Stream names use lowercase symbols (Binance requirement).
Data payloads return uppercase symbols.

Key differences from BinanceSpotService:
- Base URL: wss://fstream.binance.com
- Trades use @aggTrade (field 'a' = agg trade ID, not 't')
- Orderbook payload uses 'b'/'a' for bids/asks and 'u' for update ID,
  AND includes 's' (symbol) in the payload itself.
- Additional @markPrice@1s stream per symbol for funding rate.
"""

import asyncio
import json
import math
import time
import websockets
from collections import deque
from typing import Optional, Dict, Any

from core.base_service import BaseService


class BinanceFuturesService(BaseService):
    """Service for streaming Binance USD-M Futures prices via WebSocket.

    Uses the combined streams endpoint to subscribe to miniTicker,
    partial book depth, aggTrade, and markPrice streams for all configured
    symbols on a single WebSocket connection.

    Redis Key Patterns:
        Ticker:    {redis_prefix}:{symbol}           (Hash)
        Orderbook: {orderbook_redis_prefix}:{symbol} (Hash)
        Trades:    {trades_redis_prefix}:{symbol}    (Hash)
    """

    def __init__(self, config: dict):
        """Initialize Binance Futures Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Binance-Futures", config)
        self.ws_base_url = config.get('websocket_url', 'wss://fstream.binance.com')
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.redis_prefix = config.get('redis_prefix', 'binance_futures')
        self.redis_ttl = config.get('redis_ttl', 60)
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        # Exponential backoff: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

        # Orderbook configuration (Binance USD-M Futures partial depth: max 20 levels)
        self.orderbook_enabled = config.get('orderbook_enabled', True)
        self.orderbook_depth = min(config.get('orderbook_depth', 20), 20)
        self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'binance_futures_ob')

        # Trades configuration
        self.trades_enabled = config.get('trades_enabled', True)
        self.trades_limit = config.get('trades_limit', 50)
        self.trades_redis_prefix = config.get('trades_redis_prefix', 'binance_futures_trades')

        # Funding rate configuration
        self.funding_enabled = config.get('funding_enabled', True)

        # In-memory state
        self._trades: Dict[str, deque] = {}
        # Funding/mark-price cache: {symbol: {mark_price, current_funding_rate, next_funding_time}}
        self._mark_data: Dict[str, dict] = {}

        # Map lowercase stream symbol to uppercase config symbol
        # Futures orderbook payload DOES include 's', but we keep this for stream routing
        self._stream_symbol_map: Dict[str, str] = {s.lower(): s for s in self.symbols}

    def _build_stream_url(self) -> str:
        """Build combined streams URL from configured symbols.

        Binance combined streams URL format:
            wss://fstream.binance.com/stream?streams=stream1/stream2/...

        Stream names must be lowercase.

        Returns:
            Full WebSocket URL with all stream subscriptions
        """
        streams = []
        for symbol in self.symbols:
            s = symbol.lower()
            # LTP (2s updates)
            streams.append(f"{s}@miniTicker")
            # Orderbook snapshot (100ms)
            if self.orderbook_enabled:
                streams.append(f"{s}@depth{self.orderbook_depth}@100ms")
            # Aggregated trades
            if self.trades_enabled:
                streams.append(f"{s}@aggTrade")
            # Mark price + funding rate (1s)
            if self.funding_enabled:
                streams.append(f"{s}@markPrice@1s")

        return f"{self.ws_base_url}/stream?streams={'/'.join(streams)}"

    async def start(self):
        """Start the Binance Futures price streaming service."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        if not self.symbols:
            self.logger.error("No symbols configured")
            return

        self.running = True
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

                self.websocket = None
                self.logger.warning(f"Connection error (attempt {reconnect_attempts}): {e}")

                delay = self.backoff_delays[min(reconnect_attempts - 1, len(self.backoff_delays) - 1)]
                self.logger.info(f"Reconnecting in {delay} seconds...")
                await asyncio.sleep(delay)

    async def _connect_and_stream(self):
        """Connect to WebSocket and stream prices."""
        # Clear stale state on reconnection
        self._trades.clear()
        self._mark_data.clear()

        stream_url = self._build_stream_url()

        async with websockets.connect(
            stream_url,
            ping_interval=20,
            ping_timeout=30
        ) as websocket:
            self.websocket = websocket
            self.logger.info("WebSocket connected successfully")

            stream_count = len(self.symbols) * (
                1
                + int(self.orderbook_enabled)
                + int(self.trades_enabled)
                + int(self.funding_enabled)
            )
            self.logger.info(f"Subscribed to {stream_count} streams on combined connection")

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
                await self._process_orderbook_update(payload)
            elif '@aggTrade' in stream:
                await self._process_agg_trade_update(payload)
            elif '@markPrice' in stream:
                await self._process_mark_price_update(payload)

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    async def _process_ticker_update(self, ticker_data: dict):
        """Process miniTicker update and store LTP in Redis.

        Binance USD-M Futures miniTicker fields:
            e: Event type (24hrMiniTicker)
            E: Event time (ms)
            s: Symbol (BTCUSDT)
            c: Close/last price (LTP)
            o: Open price (24h)
            h: High price (24h)
            l: Low price (24h)
            v: Total traded base asset volume (24h)
            q: Total traded quote asset volume (24h)

        Funding data from _mark_data cache is merged in when available.

        Args:
            ticker_data: Parsed miniTicker payload
        """
        try:
            symbol = ticker_data.get('s', '')
            last_price = ticker_data.get('c')

            if not symbol or not last_price:
                return

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

            # Merge cached funding/mark data if available
            additional_data: Dict[str, Any] = {
                'volume_24h': ticker_data.get('v', '0'),
                'high_24h': ticker_data.get('h', '0'),
                'low_24h': ticker_data.get('l', '0'),
                'price_change_percent': price_change_percent,
            }
            mark = self._mark_data.get(symbol)
            if mark:
                additional_data['mark_price'] = mark.get('mark_price', '0')
                additional_data['current_funding_rate'] = mark.get('current_funding_rate', '0')
                additional_data['next_funding_time'] = str(mark.get('next_funding_time', '0'))

            redis_key = f"{self.redis_prefix}:{symbol}"
            success = self.redis_client.set_price_data(
                key=redis_key,
                price=price_float,
                symbol=symbol,
                additional_data=additional_data,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated {symbol}: ${last_price} "
                    f"(24h change: {price_change_percent}%)"
                )
            else:
                self.logger.warning(f"Failed to write ticker to Redis for {symbol}")

        except Exception as e:
            self.logger.error(f"Error processing ticker update: {e}")

    async def _process_orderbook_update(self, ob_data: dict):
        """Process partial book depth update and store in Redis.

        Binance USD-M Futures partial depth payload differs from spot:
            s: Symbol (BTCUSDT) — present in futures, absent in spot
            b: [[price, qty], ...] — bids (descending)
            a: [[price, qty], ...] — asks (ascending)
            u: Update ID (int)

        Each message is a full snapshot; no incremental management needed.

        Args:
            ob_data: Parsed orderbook payload (futures depth20@100ms)
        """
        try:
            if not isinstance(ob_data, dict):
                return

            # Futures depth payload includes 's' field directly
            symbol = ob_data.get('s', '')
            if not symbol:
                return

            raw_bids = ob_data.get('b', [])
            raw_asks = ob_data.get('a', [])

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
                    redis_key = f"{self.orderbook_redis_prefix}:{symbol}"
                    self.redis_client.delete_key(redis_key)
                    return

                mid_price = (best_bid + best_ask) / 2
            except (ValueError, TypeError):
                return

            redis_key = f"{self.orderbook_redis_prefix}:{symbol}"
            success = self.redis_client.set_orderbook_data(
                key=redis_key,
                bids=sorted_bids,
                asks=sorted_asks,
                spread=spread,
                mid_price=mid_price,
                update_id=ob_data.get('u', 0),
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated orderbook {symbol}: {len(sorted_bids)} bids, {len(sorted_asks)} asks, "
                    f"spread: {spread}"
                )
            else:
                self.logger.warning(f"Failed to write orderbook to Redis for {symbol}")

        except Exception as e:
            self.logger.error(f"Error processing orderbook update: {e}")

    async def _process_agg_trade_update(self, trade_data: dict):
        """Process aggTrade update and store in Redis.

        Binance USD-M Futures aggTrade fields:
            e: Event type (aggTrade)
            E: Event time (ms)
            s: Symbol (BTCUSDT)
            a: Aggregate trade ID (int)
            p: Price
            q: Quantity
            T: Trade time (ms)
            m: Is buyer the market maker? (true = Sell, false = Buy)

        aggTrade aggregates multiple fills at the same price and time
        into a single event (unlike @trade which emits one event per fill).

        Args:
            trade_data: Parsed aggTrade payload
        """
        try:
            symbol = trade_data.get('s', '')

            try:
                price = float(trade_data.get('p', 0))
                quantity = float(trade_data.get('q', 0))
            except (ValueError, TypeError):
                return

            if not symbol or price <= 0 or quantity <= 0:
                return
            if not math.isfinite(price) or not math.isfinite(quantity):
                self.logger.warning(f"Non-finite trade values for {symbol}: price={price}, qty={quantity}")
                return

            # m=True → buyer is maker → taker is seller
            side = 'Sell' if trade_data.get('m', False) else 'Buy'

            # Agg trade ID is field 'a' (integer), not 't' as in spot @trade
            raw_trade_id = trade_data.get('a')
            trade_id = str(raw_trade_id) if raw_trade_id is not None else f"binance_f_{int(time.time() * 1000)}"

            self._trades.setdefault(symbol, deque(maxlen=self.trades_limit))
            self._trades[symbol].append({
                'p': price,
                'q': quantity,
                's': side,
                't': trade_data.get('T', 0),
                'id': trade_id
            })

            redis_key = f"{self.trades_redis_prefix}:{symbol}"
            trades_list = list(self._trades[symbol])
            success = self.redis_client.set_trades_data(
                key=redis_key,
                trades=trades_list,
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated trades {symbol}: {len(trades_list)} trades, "
                    f"latest: {price} @ {side}"
                )
            else:
                self.logger.warning(f"Failed to write trades to Redis for {symbol}")

        except Exception as e:
            self.logger.error(f"Error processing aggTrade update: {e}")

    async def _process_mark_price_update(self, mark_data: dict):
        """Cache mark price and funding rate for next ticker update.

        Binance USD-M Futures markPrice fields:
            e: Event type (markPriceUpdate)
            E: Event time (ms)
            s: Symbol (BTCUSDT)
            p: Mark price
            r: Funding rate (decimal, e.g., '0.0001')
            T: Next funding time (ms epoch)

        Data is cached in _mark_data and merged into the LTP Redis hash
        on the next @miniTicker update for this symbol.

        Args:
            mark_data: Parsed markPrice payload
        """
        try:
            symbol = mark_data.get('s', '')
            if not symbol:
                return

            self._mark_data[symbol] = {
                'mark_price': mark_data.get('p', '0'),
                'current_funding_rate': mark_data.get('r', '0'),
                'next_funding_time': mark_data.get('T', 0),
            }

            self.logger.debug(
                f"Cached mark price for {symbol}: {mark_data.get('p')} "
                f"funding: {mark_data.get('r')}"
            )

        except Exception as e:
            self.logger.error(f"Error processing mark price update: {e}")

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        self.logger.info("Binance Futures Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('binance')
    service_config = config.get('services', {}).get('futures', {})

    service = BinanceFuturesService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

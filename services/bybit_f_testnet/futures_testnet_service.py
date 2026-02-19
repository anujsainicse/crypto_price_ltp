"""Bybit Futures TestNet Service.

Connects to the Bybit linear (futures) testnet WebSocket and streams
LTP, orderbook, and trade data for configured symbols.

Testnet endpoint: wss://stream-testnet.bybit.com/v5/public/linear

Redis Key Patterns:
    Ticker:    {redis_prefix}:{base_coin}            (Hash)
    Orderbook: {orderbook_redis_prefix}:{base_coin}  (Hash)
    Trades:    {trades_redis_prefix}:{base_coin}      (Hash)
"""

import asyncio
import json
import math
import time
import websockets
from collections import deque
from typing import Optional, Dict, Any

from core.base_service import BaseService


class BybitFuturesTestnetService(BaseService):
    """Service for streaming Bybit Futures TestNet prices via WebSocket.

    Uses the Bybit V5 linear testnet endpoint. Subscribes to tickers,
    orderbook, and publicTrade channels for each configured symbol.

    Futures tickers include a funding rate field (fundingRate) which is
    stored as current_funding_rate alongside LTP data.
    """

    def __init__(self, config: dict):
        """Initialize Bybit Futures TestNet Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Bybit-Futures-Testnet", config)
        self.ws_url = config.get(
            'websocket_url',
            'wss://stream-testnet.bybit.com/v5/public/linear'
        )
        self.symbols = config.get('symbols', [])
        self.reconnect_interval = config.get('reconnect_interval', 5)
        self.redis_prefix = config.get('redis_prefix', 'bybit_futures_testnet')
        self.redis_ttl = config.get('redis_ttl', 60)
        # Futures pairs only use USDT/USDC quotes (no BTC or ETH quote pairs)
        self.quote_currencies = config.get('quote_currencies', ['USDT', 'USDC'])
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        # Exponential backoff: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

        # Orderbook configuration
        self.orderbook_enabled = config.get('orderbook_enabled', False)
        self.orderbook_depth = config.get('orderbook_depth', 50)
        self.orderbook_redis_prefix = config.get(
            'orderbook_redis_prefix', 'bybit_futures_testnet_ob'
        )

        # Trades configuration
        self.trades_enabled = config.get('trades_enabled', False)
        self.trades_limit = config.get('trades_limit', 50)
        self.trades_redis_prefix = config.get(
            'trades_redis_prefix', 'bybit_futures_testnet_trades'
        )

        # In-memory state for orderbooks and trades
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._trades: Dict[str, deque] = {}

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
        """Start the Bybit Futures TestNet price streaming service."""
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
        # Clear stale state on reconnection to prevent memory leaks and stale data
        self._orderbooks.clear()
        self._trades.clear()

        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=30
        ) as websocket:
            self.websocket = websocket
            self.logger.info("WebSocket connected successfully")

            # Subscribe to all channels
            await self._subscribe_to_channels()

            # Listen for messages
            async for message in websocket:
                if not self.running:
                    break

                try:
                    await self._handle_message(message)
                except Exception as e:
                    self.logger.error(f"Error handling message: {e}")

    async def _subscribe_to_channels(self):
        """Subscribe to ticker, orderbook, and trades channels for each symbol."""
        if not self.websocket:
            return

        for symbol in self.symbols:
            # Build channel list for this symbol
            channels = [f"tickers.{symbol}"]

            if self.orderbook_enabled:
                channels.append(f"orderbook.{self.orderbook_depth}.{symbol}")

            if self.trades_enabled:
                channels.append(f"publicTrade.{symbol}")

            subscribe_msg = {
                "op": "subscribe",
                "args": channels
            }
            await self.websocket.send(json.dumps(subscribe_msg))
            self.logger.info(f"Subscribed to channels for {symbol}: {channels}")

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

            topic = data.get('topic', '')

            # Route to appropriate handler based on topic prefix
            if topic.startswith('tickers.'):
                await self._process_ticker_update(data)
            elif topic.startswith('orderbook.'):
                await self._process_orderbook_update(data)
            elif topic.startswith('publicTrade.'):
                await self._process_trade_update(data)

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    async def _process_ticker_update(self, data: dict):
        """Process futures ticker update and store LTP + funding rate in Redis.

        Bybit linear ticker fields include fundingRate which is stored as
        current_funding_rate alongside the standard LTP fields.

        Args:
            data: Ticker update message
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

            base_coin = self._extract_base_coin(symbol)

            # Extract funding rate (futures-specific field)
            funding_rate = ticker_data.get('fundingRate', '0') or '0'

            redis_key = f"{self.redis_prefix}:{base_coin}"
            success = self.redis_client.set_price_data(
                key=redis_key,
                price=price,
                symbol=symbol,
                additional_data={
                    'volume_24h': ticker_data.get('volume24h', '0'),
                    'high_24h': ticker_data.get('highPrice24h', '0'),
                    'low_24h': ticker_data.get('lowPrice24h', '0'),
                    'price_change_percent': ticker_data.get('price24hPcnt', '0'),
                    'current_funding_rate': funding_rate
                },
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated {base_coin}: ${last_price} "
                    f"(funding: {funding_rate}, 24h change: {ticker_data.get('price24hPcnt', '0')}%)"
                )

        except Exception as e:
            self.logger.error(f"Error processing ticker update: {e}")

    async def _process_orderbook_update(self, data: dict):
        """Process orderbook update and store in Redis.

        Handles both snapshot (full replacement) and delta (incremental) messages.
        Zero-quantity entries signal price level deletion in delta updates.

        Args:
            data: Orderbook update message (snapshot or delta)
        """
        try:
            update_type = data.get('type', '')  # 'snapshot' or 'delta'
            ob_data = data.get('data', {})

            if not isinstance(ob_data, dict):
                return

            symbol = ob_data.get('s', '')

            if not symbol:
                return

            base_coin = self._extract_base_coin(symbol)

            if update_type == 'snapshot':
                # Full orderbook replacement
                self._orderbooks[symbol] = {
                    'bids': {item[0]: item[1] for item in ob_data.get('b', []) if len(item) >= 2},
                    'asks': {item[0]: item[1] for item in ob_data.get('a', []) if len(item) >= 2},
                    'update_id': ob_data.get('u', 0)
                }
            elif update_type == 'delta':
                # Incremental update
                if symbol not in self._orderbooks:
                    self.logger.warning(f"Received delta before snapshot for {symbol}")
                    return

                # Apply bid updates (zero qty = delete price level)
                for entry in ob_data.get('b', []):
                    if len(entry) < 2:
                        continue
                    price, qty = entry[0], entry[1]
                    try:
                        qty_float = float(qty)
                        if not math.isfinite(qty_float):
                            continue
                        if qty_float == 0:
                            self._orderbooks[symbol]['bids'].pop(price, None)
                        else:
                            self._orderbooks[symbol]['bids'][price] = qty
                    except (ValueError, TypeError):
                        continue

                # Apply ask updates (zero qty = delete price level)
                for entry in ob_data.get('a', []):
                    if len(entry) < 2:
                        continue
                    price, qty = entry[0], entry[1]
                    try:
                        qty_float = float(qty)
                        if not math.isfinite(qty_float):
                            continue
                        if qty_float == 0:
                            self._orderbooks[symbol]['asks'].pop(price, None)
                        else:
                            self._orderbooks[symbol]['asks'][price] = qty
                    except (ValueError, TypeError):
                        continue

                self._orderbooks[symbol]['update_id'] = ob_data.get('u', 0)

            # Prepare sorted orderbook for Redis storage
            ob = self._orderbooks.get(symbol, {})
            if not ob:
                return

            # Sort bids descending, asks ascending; cap at configured depth
            sorted_bids = sorted(
                [[p, q] for p, q in ob.get('bids', {}).items()],
                key=lambda x: float(x[0]),
                reverse=True
            )[:self.orderbook_depth]

            sorted_asks = sorted(
                [[p, q] for p, q in ob.get('asks', {}).items()],
                key=lambda x: float(x[0])
            )[:self.orderbook_depth]

            if not sorted_bids or not sorted_asks:
                return

            # Validate spread; detect crossed book
            try:
                best_bid = float(sorted_bids[0][0])
                best_ask = float(sorted_asks[0][0])

                if not math.isfinite(best_bid) or not math.isfinite(best_ask):
                    return

                spread = best_ask - best_bid

                if spread < 0:
                    self.logger.warning(
                        f"Crossed orderbook for {symbol}: spread={spread:.6f}. "
                        f"Clearing state and forcing reconnect for fresh snapshots."
                    )
                    del self._orderbooks[symbol]

                    # Clear stale Redis data immediately
                    redis_key = f"{self.orderbook_redis_prefix}:{base_coin}"
                    self.redis_client.delete_key(redis_key)

                    # Close the WebSocket so _connect_and_stream exits and start()
                    # immediately reconnects, obtaining fresh snapshots for all symbols.
                    # Without this, delta messages continue arriving but silently
                    # early-return at the "delta before snapshot" guard indefinitely.
                    if self.websocket:
                        await self.websocket.close()
                    return

                mid_price = (best_bid + best_ask) / 2
            except (ValueError, TypeError):
                return

            redis_key = f"{self.orderbook_redis_prefix}:{base_coin}"
            success = self.redis_client.set_orderbook_data(
                key=redis_key,
                bids=sorted_bids,
                asks=sorted_asks,
                spread=spread,
                mid_price=mid_price,
                update_id=ob.get('update_id', 0),
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated orderbook {base_coin}: {len(sorted_bids)} bids, "
                    f"{len(sorted_asks)} asks, spread: {spread}"
                )

        except Exception as e:
            self.logger.error(f"Error processing orderbook update: {e}")

    async def _process_trade_update(self, data: dict):
        """Process trade update and store in Redis.

        Args:
            data: Trade update message
        """
        try:
            trades_data = data.get('data', [])

            if not trades_data:
                return

            for trade in trades_data:
                symbol = trade.get('s', '')
                try:
                    price = float(trade.get('p', 0))
                    quantity = float(trade.get('v', 0))
                except (ValueError, TypeError):
                    continue

                if not symbol or price <= 0 or quantity <= 0:
                    continue

                if not math.isfinite(price) or not math.isfinite(quantity):
                    self.logger.warning(
                        f"Non-finite trade values for {symbol}: price={price}, qty={quantity}"
                    )
                    continue

                base_coin = self._extract_base_coin(symbol)

                self._trades.setdefault(symbol, deque(maxlen=self.trades_limit))

                self._trades[symbol].append({
                    'p': price,                 # price
                    'q': quantity,              # quantity (v = volume in Bybit)
                    's': trade.get('S', ''),    # side (Buy/Sell)
                    't': trade.get('T', 0),     # timestamp (ms)
                    'id': trade.get('i', '')    # trade id
                })

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
                        f"latest: {trade.get('p')} @ {trade.get('S')}"
                    )

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

        self.logger.info("Bybit Futures TestNet Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('bybit_futures_testnet')
    service_config = config.get('services', {}).get('futures_orderbook', {})

    service = BybitFuturesTestnetService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

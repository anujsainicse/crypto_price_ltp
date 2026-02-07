"""Bybit Options Market Data Service.

Provides real-time options data via single WebSocket connection with dynamic symbol discovery.
"""

import asyncio
import json
import math
import time
import aiohttp
import websockets
from typing import Optional, List, Dict, Any
from datetime import datetime

from core.base_service import BaseService


class BybitOptionsService(BaseService):
    """Service for streaming Bybit options data via WebSocket.

    Features:
    - Dynamic base coin discovery (finds all underlying assets with options)
    - REST API symbol fetching with pagination
    - Symbol filtering by open interest (top N per underlying)
    - Single WebSocket connection multiplexing all subscriptions
    - Batch subscription management (50 symbols/batch)
    - Periodic symbol refresh for expiring options
    - Exponential backoff auto-reconnection

    Redis Key Patterns:
        Ticker: {redis_prefix}:{symbol} (Hash)
        Orderbook: {orderbook_redis_prefix}:{symbol} (Hash) - if enabled
    """

    REST_API_URL = "https://api.bybit.com/v5/market/instruments-info"
    # Known possible base coins for options on Bybit
    POSSIBLE_BASE_COINS = ['BTC', 'ETH', 'SOL', 'XRP', 'AVAX', 'DOGE', 'LINK', 'MATIC']

    def __init__(self, config: dict):
        """Initialize Bybit Options Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("Bybit-Options", config)
        self.config = config

        # API endpoints
        self.ws_url = config.get('websocket_url', 'wss://stream.bybit.com/v5/public/option')
        self.rest_api_url = config.get('rest_api_url', self.REST_API_URL)

        # Base coin discovery
        self.base_coins = config.get('base_coins', [])
        self.use_dynamic_base_coin_discovery = config.get('use_dynamic_base_coin_discovery', True)

        # Symbol filtering
        self.subscribe_all = config.get('subscribe_all', False)
        self.max_symbols_per_asset = config.get('max_symbols_per_asset', 100)
        self.max_active_symbols = config.get('max_active_symbols', 1000)

        # Subscription behavior
        self.subscription_batch_size = config.get('subscription_batch_size', 50)
        self.subscription_batch_delay = config.get('subscription_batch_delay', 0.2)
        self.symbol_refresh_interval = config.get('symbol_refresh_interval', 3600)
        self.reconnect_interval = config.get('reconnect_interval', 5)

        # Redis storage
        self.redis_prefix = config.get('redis_prefix', 'bybit_options')
        self.redis_ttl = config.get('redis_ttl', 60)

        # Optional orderbook
        self.orderbook_enabled = config.get('orderbook_enabled', False)
        self.orderbook_depth = config.get('orderbook_depth', 25)
        self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'bybit_options_ob')

        # State management
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.active_symbols: List[str] = []
        self.discovered_base_coins: List[str] = []
        self._orderbooks: Dict[str, Dict[str, Any]] = {}

        # Exponential backoff delays: 5s → 10s → 20s → 40s → 60s (max)
        self.backoff_delays = [5, 10, 20, 40, 60]

    async def _discover_base_coins(self) -> List[str]:
        """Discover all available base coins with options on Bybit.

        Returns:
            List of base coin symbols that have active options
        """
        discovered = set()

        self.logger.info("Discovering available base coins for options...")

        async with aiohttp.ClientSession() as session:
            for coin in self.POSSIBLE_BASE_COINS:
                params = {
                    'category': 'option',
                    'baseCoin': coin,
                    'status': 'Trading',
                    'limit': 1
                }
                try:
                    async with session.get(
                        self.rest_api_url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get('retCode') == 0:
                                instruments = data.get('result', {}).get('list', [])
                                if instruments:
                                    discovered.add(coin)
                                    self.logger.info(f"Discovered options for: {coin}")
                except asyncio.TimeoutError:
                    self.logger.debug(f"Timeout checking options for {coin}")
                except Exception as e:
                    self.logger.debug(f"No options available for {coin}: {e}")

        result = list(discovered)
        self.logger.info(f"Discovered {len(result)} base coins: {result}")
        return result

    async def _fetch_valid_options_symbols(self) -> List[Dict]:
        """Fetch option instruments from Bybit REST API.

        Returns:
            List of instrument dictionaries with symbol info
        """
        all_instruments = []

        # Discover base coins if dynamic discovery is enabled
        if self.use_dynamic_base_coin_discovery and not self.base_coins:
            self.discovered_base_coins = await self._discover_base_coins()
        else:
            self.discovered_base_coins = self.base_coins if self.base_coins else ['BTC', 'ETH']

        if not self.discovered_base_coins:
            self.logger.warning("No base coins discovered, defaulting to BTC and ETH")
            self.discovered_base_coins = ['BTC', 'ETH']

        self.logger.info(f"Fetching options for base coins: {self.discovered_base_coins}")

        async with aiohttp.ClientSession() as session:
            for base_coin in self.discovered_base_coins:
                params = {
                    'category': 'option',
                    'baseCoin': base_coin,
                    'status': 'Trading',
                    'limit': 1000
                }

                try:
                    # Initial request
                    async with session.get(
                        self.rest_api_url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        if response.status != 200:
                            self.logger.error(f"REST API returned status {response.status} for {base_coin}")
                            continue

                        data = await response.json()
                        if data.get('retCode') != 0:
                            self.logger.error(f"REST API error for {base_coin}: {data.get('retMsg')}")
                            continue

                        instruments = data.get('result', {}).get('list', [])
                        all_instruments.extend(instruments)
                        self.logger.info(f"Fetched {len(instruments)} options for {base_coin}")

                        # Handle pagination
                        cursor = data.get('result', {}).get('nextPageCursor')
                        page_count = 1
                        while cursor and page_count < 10:  # Safety limit
                            params['cursor'] = cursor
                            async with session.get(
                                self.rest_api_url,
                                params=params,
                                timeout=aiohttp.ClientTimeout(total=30)
                            ) as resp:
                                if resp.status != 200:
                                    break
                                page_data = await resp.json()
                                if page_data.get('retCode') != 0:
                                    break
                                page_instruments = page_data.get('result', {}).get('list', [])
                                if not page_instruments:
                                    break
                                all_instruments.extend(page_instruments)
                                cursor = page_data.get('result', {}).get('nextPageCursor')
                                page_count += 1
                                self.logger.debug(f"Fetched page {page_count} for {base_coin}: {len(page_instruments)} options")

                except asyncio.TimeoutError:
                    self.logger.error(f"Timeout fetching options for {base_coin}")
                except aiohttp.ClientError as e:
                    self.logger.error(f"HTTP error fetching options for {base_coin}: {e}")
                except Exception as e:
                    self.logger.error(f"Error fetching options for {base_coin}: {e}")

        self.logger.info(f"Total options instruments fetched: {len(all_instruments)}")
        return all_instruments

    def _filter_symbols(self, all_instruments: List[Dict]) -> List[str]:
        """Filter symbols to top N by open interest per underlying asset.

        Args:
            all_instruments: List of instrument data from REST API

        Returns:
            List of symbol strings to subscribe to
        """
        selected = []

        # Group by base coin
        by_base_coin: Dict[str, List[Dict]] = {}
        for inst in all_instruments:
            base = inst.get('baseCoin', '')
            if base not in by_base_coin:
                by_base_coin[base] = []
            by_base_coin[base].append(inst)

        for base_coin, instruments in by_base_coin.items():
            if self.subscribe_all:
                # Subscribe to all options for this underlying
                symbols = [i['symbol'] for i in instruments if i.get('symbol')]
                selected.extend(symbols)
                self.logger.info(f"Selected ALL {len(symbols)} options for {base_coin}")
            else:
                # Sort by open interest descending
                # Note: REST API may not return OI, use 0 as default
                instruments.sort(
                    key=lambda x: float(x.get('openInterest', 0) or 0),
                    reverse=True
                )
                # Take top N
                top_symbols = [i['symbol'] for i in instruments[:self.max_symbols_per_asset] if i.get('symbol')]
                selected.extend(top_symbols)
                self.logger.info(f"Selected {len(top_symbols)} options for {base_coin} (top by OI)")

        # Enforce global limit
        if len(selected) > self.max_active_symbols:
            self.logger.warning(
                f"Symbol count ({len(selected)}) exceeds limit ({self.max_active_symbols}). Truncating."
            )
            selected = selected[:self.max_active_symbols]

        self.logger.info(f"Total options to subscribe: {len(selected)}")
        return selected

    def _parse_option_symbol(self, symbol: str) -> dict:
        """Parse Bybit option symbol into components.

        Bybit option symbol format: BTC-27MAR26-70000-P
        Parts: UNDERLYING-EXPIRY-STRIKE-TYPE

        Args:
            symbol: Option symbol string

        Returns:
            Dictionary with option details (underlying, expiry, strike, type)
        """
        try:
            parts = symbol.split('-')
            if len(parts) >= 4:
                return {
                    'underlying': parts[0],
                    'expiry': parts[1],
                    'strike': parts[2],
                    'type': 'CALL' if parts[3] == 'C' else 'PUT'
                }
        except Exception as e:
            self.logger.debug(f"Error parsing option symbol {symbol}: {e}")

        return {'underlying': '', 'expiry': '', 'strike': '', 'type': 'UNKNOWN'}

    async def _discover_symbols(self) -> List[str]:
        """Discover and filter options symbols.

        Returns:
            List of symbol strings to subscribe to
        """
        self.logger.info("Discovering options symbols...")

        all_instruments = await self._fetch_valid_options_symbols()

        if all_instruments:
            symbols = self._filter_symbols(all_instruments)
            if symbols:
                return symbols
            else:
                self.logger.warning("No symbols matched filter criteria")

        self.logger.error("Failed to discover options symbols")
        return []

    async def start(self):
        """Start the Bybit options streaming service."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        # Discover valid symbols
        self.active_symbols = await self._discover_symbols()

        if not self.active_symbols:
            self.logger.error("No valid options symbols to subscribe to")
            return

        self.running = True
        self.logger.info(f"Starting WebSocket connection to {self.ws_url}")
        self.logger.info(f"Monitoring {len(self.active_symbols)} options")
        if len(self.active_symbols) <= 10:
            self.logger.info(f"Symbols: {', '.join(self.active_symbols)}")
        else:
            self.logger.info(f"First 5 symbols: {', '.join(self.active_symbols[:5])}...")

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
                    reconnect_attempts = 0  # Full reset after stable connection
                reconnect_attempts += 1

                # Clear stale WebSocket reference
                self.websocket = None
                self.logger.warning(f"Connection error (attempt {reconnect_attempts}): {e}")

                # Exponential backoff with 60s cap (never give up)
                delay = self.backoff_delays[min(reconnect_attempts - 1, len(self.backoff_delays) - 1)]
                self.logger.info(f"Reconnecting in {delay} seconds...")
                await asyncio.sleep(delay)

    async def _connect_and_stream(self):
        """Connect to WebSocket and stream options data."""
        # Clear stale state on reconnection
        self._orderbooks.clear()

        # Clear stale Redis keys for options that may have crossed books
        # This prevents serving stale data after reconnection
        if self.orderbook_enabled:
            for symbol in self.active_symbols:
                redis_key = f"{self.orderbook_redis_prefix}:{symbol}"
                self.redis_client.delete_key(redis_key)

        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=30
        ) as websocket:
            self.websocket = websocket
            self.logger.info("WebSocket connected successfully")

            # Subscribe to symbols
            await self._subscribe_to_channels()

            # Start symbol refresh task
            refresh_task = asyncio.create_task(self._periodic_symbol_refresh())

            try:
                # Listen for messages
                async for message in websocket:
                    if not self.running:
                        break

                    try:
                        await self._handle_message(message)
                    except Exception as e:
                        self.logger.error(f"Error handling message: {e}")
            finally:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass

    async def _subscribe_to_channels(self):
        """Subscribe to tickers (and optionally orderbooks) in batches."""
        if not self.websocket:
            return

        batch_size = self.subscription_batch_size
        total_symbols = len(self.active_symbols)
        total_batches = (total_symbols + batch_size - 1) // batch_size

        self.logger.info(f"Subscribing to {total_symbols} options in {total_batches} batches...")

        for i in range(0, total_symbols, batch_size):
            batch = self.active_symbols[i:i + batch_size]
            batch_num = i // batch_size + 1

            # Build topics
            topics = [f"tickers.{symbol}" for symbol in batch]
            if self.orderbook_enabled:
                topics.extend([f"orderbook.{self.orderbook_depth}.{symbol}" for symbol in batch])

            subscribe_msg = {
                "req_id": f"batch_{batch_num}",
                "op": "subscribe",
                "args": topics
            }

            await self.websocket.send(json.dumps(subscribe_msg))
            self.logger.info(f"Subscribed batch {batch_num}/{total_batches}: {len(batch)} symbols")

            # Add delay between batches (but not after the last one)
            if i + batch_size < total_symbols:
                await asyncio.sleep(self.subscription_batch_delay)

        self.logger.info(f"Subscription complete: {total_symbols} options symbols")

    async def _periodic_symbol_refresh(self):
        """Periodically refresh symbols to handle expiring options."""
        while self.running:
            try:
                await asyncio.sleep(self.symbol_refresh_interval)

                if not self.running:
                    break

                self.logger.info("Refreshing options symbols...")

                # Discover new symbols
                new_symbols = await self._discover_symbols()

                if not new_symbols:
                    self.logger.warning("Symbol refresh returned empty, keeping existing symbols")
                    continue

                # Find symbols to unsubscribe and subscribe
                old_set = set(self.active_symbols)
                new_set = set(new_symbols)

                to_unsubscribe = old_set - new_set
                to_subscribe = new_set - old_set

                if to_unsubscribe:
                    self.logger.info(f"Unsubscribing from {len(to_unsubscribe)} expired symbols")
                    await self._unsubscribe_symbols(list(to_unsubscribe))
                    # Clean up in-memory orderbook state for expired symbols
                    for symbol in to_unsubscribe:
                        self._orderbooks.pop(symbol, None)

                if to_subscribe:
                    self.logger.info(f"Subscribing to {len(to_subscribe)} new symbols")
                    await self._subscribe_symbols(list(to_subscribe))

                self.active_symbols = new_symbols
                self.logger.info(f"Symbol refresh complete. Now tracking {len(self.active_symbols)} symbols")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error during symbol refresh: {e}")

    async def _subscribe_symbols(self, symbols: List[str]):
        """Subscribe to a list of symbols.

        Args:
            symbols: List of symbols to subscribe to
        """
        if not self.websocket or not symbols:
            return

        topics = [f"tickers.{symbol}" for symbol in symbols]
        if self.orderbook_enabled:
            topics.extend([f"orderbook.{self.orderbook_depth}.{symbol}" for symbol in symbols])

        subscribe_msg = {
            "req_id": f"refresh_{int(time.time())}",
            "op": "subscribe",
            "args": topics
        }
        await self.websocket.send(json.dumps(subscribe_msg))
        self.logger.debug(f"Subscribed to {len(symbols)} new symbols")

    async def _unsubscribe_symbols(self, symbols: List[str]):
        """Unsubscribe from a list of symbols.

        Args:
            symbols: List of symbols to unsubscribe from
        """
        if not self.websocket or not symbols:
            return

        topics = [f"tickers.{symbol}" for symbol in symbols]
        if self.orderbook_enabled:
            topics.extend([f"orderbook.{self.orderbook_depth}.{symbol}" for symbol in symbols])

        unsubscribe_msg = {
            "req_id": f"unsub_{int(time.time())}",
            "op": "unsubscribe",
            "args": topics
        }
        await self.websocket.send(json.dumps(unsubscribe_msg))
        self.logger.debug(f"Unsubscribed from {len(symbols)} symbols")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message.

        Args:
            message: Raw WebSocket message
        """
        try:
            data = json.loads(message)

            # Handle subscription confirmation
            if data.get('op') == 'subscribe':
                success = data.get('success', False)
                if success:
                    self.logger.debug(f"Subscription confirmed: {data.get('req_id')}")
                else:
                    self.logger.warning(f"Subscription failed: {data.get('ret_msg')}")
                return

            # Handle unsubscription confirmation
            if data.get('op') == 'unsubscribe':
                self.logger.debug(f"Unsubscription confirmed: {data.get('req_id')}")
                return

            # Route based on topic
            topic = data.get('topic', '')

            if topic.startswith('tickers.'):
                await self._process_ticker_update(data)
            elif topic.startswith('orderbook.'):
                await self._process_orderbook_update(data)
            else:
                # Log unknown message types at debug level
                self.logger.debug(f"Received message type: {data.get('op', 'unknown')}")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}", exc_info=True)

    async def _process_ticker_update(self, data: dict):
        """Process options ticker update and store in Redis.

        Args:
            data: Ticker update data
        """
        try:
            ticker = data.get('data', {})
            symbol = ticker.get('symbol')

            if not symbol:
                return

            # Get price (use lastPrice, fall back to markPrice)
            price = ticker.get('lastPrice') or ticker.get('markPrice')
            if not price:
                return

            # Validate price
            try:
                price_float = float(price)
                if not math.isfinite(price_float) or price_float < 0:
                    self.logger.warning(f"Invalid price for {symbol}: {price}")
                    return
            except (ValueError, TypeError):
                self.logger.warning(f"Cannot convert price to float for {symbol}: {price}")
                return

            # Parse option components
            option_info = self._parse_option_symbol(symbol)

            # Store in Redis
            redis_key = f"{self.redis_prefix}:{symbol}"

            additional_data = {
                'mark_price': str(ticker.get('markPrice', '0')),
                'bid': str(ticker.get('bid1Price', '0')),
                'ask': str(ticker.get('ask1Price', '0')),
                'bid_size': str(ticker.get('bid1Size', '0')),
                'ask_size': str(ticker.get('ask1Size', '0')),
                # Greeks
                'delta': str(ticker.get('delta', '0')),
                'gamma': str(ticker.get('gamma', '0')),
                'vega': str(ticker.get('vega', '0')),
                'theta': str(ticker.get('theta', '0')),
                # Volatility & Interest
                'iv': str(ticker.get('markIv', '0')),
                'bid_iv': str(ticker.get('bidIv', '0')),
                'ask_iv': str(ticker.get('askIv', '0')),
                'open_interest': str(ticker.get('openInterest', '0')),
                'volume_24h': str(ticker.get('volume24h', '0')),
                'turnover_24h': str(ticker.get('turnover24h', '0')),
                'high_24h': str(ticker.get('highPrice24h', '0')),
                'low_24h': str(ticker.get('lowPrice24h', '0')),
                'price_change_percent': str(ticker.get('change24h', '0')),
                # Underlying price
                'underlying_price': str(ticker.get('underlyingPrice', '0')),
                # Option metadata
                'option_type': option_info.get('type', 'UNKNOWN'),
                'underlying': option_info.get('underlying', ''),
                'strike_price': option_info.get('strike', ''),
                'expiry_date': option_info.get('expiry', ''),
            }

            success = self.redis_client.set_price_data(
                key=redis_key,
                price=price_float,
                symbol=symbol,
                additional_data=additional_data,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"[REDIS] Stored {symbol}: ${price_float:.4f} "
                    f"(Type: {option_info.get('type')}, Strike: {option_info.get('strike')}, "
                    f"Delta: {ticker.get('delta', 'N/A')}, IV: {ticker.get('markIv', 'N/A')})"
                )
            else:
                self.logger.warning(f"Failed to store {symbol} in Redis")

        except Exception as e:
            self.logger.error(f"Error processing ticker update: {e}")

    async def _process_orderbook_update(self, data: dict):
        """Process orderbook update and store in Redis (if enabled).

        Args:
            data: Orderbook update data (snapshot or delta)
        """
        if not self.orderbook_enabled:
            return

        try:
            update_type = data.get('type', '')
            ob_data = data.get('data', {})

            if not isinstance(ob_data, dict):
                return

            symbol = ob_data.get('s', '')
            if not symbol:
                return

            if update_type == 'snapshot':
                # Full orderbook replacement - validate entries are lists with 2 elements
                bids = {}
                for item in ob_data.get('b', []):
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        try:
                            bids[str(item[0])] = str(item[1])
                        except (ValueError, TypeError):
                            continue

                asks = {}
                for item in ob_data.get('a', []):
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        try:
                            asks[str(item[0])] = str(item[1])
                        except (ValueError, TypeError):
                            continue

                self._orderbooks[symbol] = {
                    'bids': bids,
                    'asks': asks,
                    'update_id': ob_data.get('u', 0)
                }
            elif update_type == 'delta':
                # Incremental update
                if symbol not in self._orderbooks:
                    self.logger.warning(f"Received delta before snapshot for {symbol}")
                    return

                # Apply bid updates
                for entry in ob_data.get('b', []):
                    if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                        continue
                    price, qty = str(entry[0]), entry[1]
                    try:
                        qty_float = float(qty)
                        if not math.isfinite(qty_float):
                            continue
                        if qty_float == 0:
                            self._orderbooks[symbol]['bids'].pop(price, None)
                        else:
                            self._orderbooks[symbol]['bids'][price] = str(qty)
                    except (ValueError, TypeError):
                        continue

                # Apply ask updates
                for entry in ob_data.get('a', []):
                    if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                        continue
                    price, qty = str(entry[0]), entry[1]
                    try:
                        qty_float = float(qty)
                        if not math.isfinite(qty_float):
                            continue
                        if qty_float == 0:
                            self._orderbooks[symbol]['asks'].pop(price, None)
                        else:
                            self._orderbooks[symbol]['asks'][price] = str(qty)
                    except (ValueError, TypeError):
                        continue

                self._orderbooks[symbol]['update_id'] = ob_data.get('u', 0)

            # Store in Redis
            ob = self._orderbooks.get(symbol, {})
            if not ob:
                return

            # Sort bids descending, asks ascending
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

            # Calculate spread and mid_price
            try:
                best_bid = float(sorted_bids[0][0])
                best_ask = float(sorted_asks[0][0])

                if not math.isfinite(best_bid) or not math.isfinite(best_ask):
                    return

                spread = best_ask - best_bid
                if spread < 0:
                    self.logger.warning(f"Invalid spread for {symbol}: {spread} (crossed book)")
                    # Clear from memory
                    del self._orderbooks[symbol]
                    # Also clear stale Redis data to prevent serving bad orderbook
                    redis_key = f"{self.orderbook_redis_prefix}:{symbol}"
                    self.redis_client.delete_key(redis_key)
                    return

                mid_price = (best_bid + best_ask) / 2
            except (ValueError, TypeError, IndexError):
                return

            # Store in Redis
            redis_key = f"{self.orderbook_redis_prefix}:{symbol}"
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
                    f"Updated orderbook {symbol}: {len(sorted_bids)} bids, {len(sorted_asks)} asks, "
                    f"spread: {spread:.4f}"
                )

        except Exception as e:
            self.logger.error(f"Error processing orderbook update: {e}")

    async def stop(self):
        """Stop the service."""
        self.running = False

        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.info("WebSocket connection closed")
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")

        self._orderbooks.clear()
        self.logger.info("Bybit Options Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('bybit')
    service_config = config.get('services', {}).get('options', {})

    if not service_config:
        print("No options configuration found in exchanges.yaml for bybit")
        return

    service = BybitOptionsService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

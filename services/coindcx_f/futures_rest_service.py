"""CoinDCX Futures REST Service for LTP, Orderbook, Trades, and Funding Rate.

This service replaces the Socket.IO-based futures_ltp_service and the separate
funding_rate_service with a unified REST-based polling approach.
"""

import asyncio
import json
import math
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from core.base_service import BaseService


class CoinDCXFuturesRESTService(BaseService):
    """Unified REST-based service for CoinDCX futures market data.

    Polls LTP, orderbook, trades, and funding rate data from CoinDCX REST APIs
    and stores normalized data in Redis.

    Redis Key Patterns:
        Ticker:    {redis_prefix}:{base_coin} (Hash)
        Orderbook: {orderbook_redis_prefix}:{base_coin} (Hash)
        Trades:    {trades_redis_prefix}:{base_coin} (Hash)
    """

    def __init__(self, config: dict):
        """Initialize CoinDCX Futures REST Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("CoinDCX-Futures-REST", config)

        # API endpoints
        self.ltp_api_url = config.get(
            'ltp_api_url',
            'https://public.coindcx.com/market_data/v3/current_prices/futures/rt'
        )
        self.orderbook_api_url = config.get(
            'orderbook_api_url',
            'https://public.coindcx.com/market_data/orderbook'
        )
        self.trades_api_url = config.get(
            'trades_api_url',
            'https://public.coindcx.com/market_data/trade_history'
        )
        self.funding_api_url = config.get(
            'funding_api_url',
            'https://futures.coindcx.com/exchange/v1/funding_rate/v2'
        )

        # Polling intervals (seconds)
        self.ltp_interval = config.get('ltp_interval', 1)
        self.orderbook_interval = config.get('orderbook_interval', 1)
        self.trades_interval = config.get('trades_interval', 2)
        self.funding_interval = config.get('funding_interval', 1800)  # 30 minutes

        # Symbols (e.g., B-BTC_USDT)
        self.symbols = config.get('symbols', [])

        # Redis configuration
        self.redis_prefix = config.get('redis_prefix', 'coindcx_futures')
        self.orderbook_redis_prefix = config.get('orderbook_redis_prefix', 'coindcx_futures_ob')
        self.trades_redis_prefix = config.get('trades_redis_prefix', 'coindcx_futures_trades')
        self.redis_ttl = config.get('redis_ttl', 60)

        # Symbol parsing configuration
        self.symbol_prefix = config.get('symbol_prefix', 'B-')
        self.quote_currencies = config.get('quote_currencies', ['USDT', 'USDC', 'USD'])

        # Data limits
        self.orderbook_depth = config.get('orderbook_depth', 50)
        self.trades_limit = config.get('trades_limit', 50)

        # HTTP settings
        self.api_timeout = config.get('api_timeout', 10)
        self.max_connections = config.get('max_connections', 10)

        # Shared HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # In-memory trades buffer (per symbol)
        self._trades: Dict[str, deque] = {}
        # Use timestamp + random component for trade counter to avoid duplicates across restarts
        self._trade_counter = int(time.time() * 1000) % 1000000

        # Backoff state per data type
        self._backoff_state = {
            'ltp': {'failures': 0, 'last_success': None},
            'orderbook': {'failures': 0, 'last_success': None},
            'trades': {'failures': 0, 'last_success': None},
            'funding': {'failures': 0, 'last_success': None},
        }
        self._backoff_delays = [1, 2, 4, 8, 16, 32, 60]  # max 60s

        # Health check interval (seconds)
        self._health_log_interval = 300  # 5 minutes
        self._stale_threshold = 30  # seconds

    async def start(self):
        """Start the CoinDCX Futures REST streaming service."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        if not self.symbols:
            self.logger.error("No symbols configured")
            return

        self.running = True
        self.logger.info(f"Starting CoinDCX Futures REST Service")
        self.logger.info(f"Monitoring symbols: {', '.join(self.symbols)}")
        self.logger.info(
            f"Polling intervals: LTP={self.ltp_interval}s, "
            f"OB={self.orderbook_interval}s, "
            f"Trades={self.trades_interval}s, "
            f"Funding={self.funding_interval}s"
        )

        # Initialize trades buffers
        for symbol in self.symbols:
            self._trades[symbol] = deque(maxlen=self.trades_limit)

        # Create shared HTTP session with connection pooling
        connector = aiohttp.TCPConnector(
            limit=self.max_connections,
            limit_per_host=self.max_connections
        )
        timeout = aiohttp.ClientTimeout(total=self.api_timeout)

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        ) as session:
            self._session = session

            # Run all polling loops concurrently
            await asyncio.gather(
                self._poll_ltp_loop(),
                self._poll_orderbook_loop(),
                self._poll_trades_loop(),
                self._poll_funding_loop(),
                self._health_check_loop(),
                return_exceptions=True
            )

    async def _poll_ltp_loop(self):
        """Poll LTP/ticker data at configured interval."""
        while self.running:
            try:
                await self._fetch_and_store_ltp()
                self._backoff_state['ltp']['failures'] = 0
                self._backoff_state['ltp']['last_success'] = time.time()
                await asyncio.sleep(self.ltp_interval)

            except aiohttp.ClientError as e:
                await self._handle_backoff('ltp', e)
            except Exception as e:
                self.logger.error(f"LTP unexpected error: {e}")
                await asyncio.sleep(self.ltp_interval)

    async def _poll_orderbook_loop(self):
        """Poll orderbook data at configured interval."""
        while self.running:
            try:
                await self._fetch_and_store_orderbooks()
                self._backoff_state['orderbook']['failures'] = 0
                self._backoff_state['orderbook']['last_success'] = time.time()
                await asyncio.sleep(self.orderbook_interval)

            except aiohttp.ClientError as e:
                await self._handle_backoff('orderbook', e)
            except Exception as e:
                self.logger.error(f"Orderbook unexpected error: {e}")
                await asyncio.sleep(self.orderbook_interval)

    async def _poll_trades_loop(self):
        """Poll trades data at configured interval."""
        while self.running:
            try:
                await self._fetch_and_store_trades()
                self._backoff_state['trades']['failures'] = 0
                self._backoff_state['trades']['last_success'] = time.time()
                await asyncio.sleep(self.trades_interval)

            except aiohttp.ClientError as e:
                await self._handle_backoff('trades', e)
            except Exception as e:
                self.logger.error(f"Trades unexpected error: {e}")
                await asyncio.sleep(self.trades_interval)

    async def _poll_funding_loop(self):
        """Poll funding rate data at configured interval."""
        while self.running:
            try:
                await self._fetch_and_store_funding()
                self._backoff_state['funding']['failures'] = 0
                self._backoff_state['funding']['last_success'] = time.time()
                await asyncio.sleep(self.funding_interval)

            except aiohttp.ClientError as e:
                await self._handle_backoff('funding', e)
            except Exception as e:
                self.logger.error(f"Funding unexpected error: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error

    async def _health_check_loop(self):
        """Periodically log health status of all data types."""
        while self.running:
            await asyncio.sleep(self._health_log_interval)

            status_parts = []
            current_time = time.time()

            for data_type, state in self._backoff_state.items():
                last_success = state['last_success']
                if last_success is None:
                    status_parts.append(f"{data_type}: NEVER")
                else:
                    age = int(current_time - last_success)
                    if age > self._stale_threshold:
                        status_parts.append(f"{data_type}: STALE ({age}s)")
                    else:
                        status_parts.append(f"{data_type}: OK")

            self.logger.info(f"Health: {', '.join(status_parts)}")

    async def _handle_backoff(self, data_type: str, error: Exception):
        """Handle backoff for a failed data type.

        Args:
            data_type: Type of data that failed
            error: The exception that occurred
        """
        failures = self._backoff_state[data_type]['failures'] + 1
        self._backoff_state[data_type]['failures'] = failures

        delay = self._backoff_delays[min(failures - 1, len(self._backoff_delays) - 1)]
        self.logger.warning(f"{data_type} poll failed (attempt {failures}): {error}")
        self.logger.info(f"Retrying {data_type} in {delay}s...")

        # DO NOT delete Redis keys - preserve stale data
        await asyncio.sleep(delay)

    async def _fetch_and_store_ltp(self):
        """Fetch LTP data from API and store in Redis."""
        if not self._session:
            return

        async with self._session.get(self.ltp_api_url) as response:
            if response.status != 200:
                raise aiohttp.ClientError(f"LTP API returned status {response.status}")

            data = await response.json()
            await self._process_ltp_data(data)

    async def _process_ltp_data(self, data: Dict):
        """Process LTP data and store in Redis.

        Args:
            data: LTP data from API (format: {"prices": {"SYMBOL": {...}}})
        """
        if not isinstance(data, dict) or 'prices' not in data:
            self.logger.error("Invalid LTP data format")
            return

        prices_data = data.get('prices', {})
        updated_count = 0

        for symbol in self.symbols:
            try:
                symbol_upper = symbol.upper()
                if symbol_upper not in prices_data:
                    self.logger.debug(f"Symbol {symbol} not found in LTP response")
                    continue

                symbol_data = prices_data[symbol_upper]

                # Extract LTP - CoinDCX uses 'ls' for last price
                ltp = symbol_data.get('ls') or symbol_data.get('last_price') or symbol_data.get('ltp')
                if ltp is None:
                    continue

                # Validate price
                try:
                    price_float = float(ltp)
                    if not math.isfinite(price_float) or price_float <= 0:
                        self.logger.warning(f"Invalid LTP for {symbol}: {ltp}")
                        continue
                except (ValueError, TypeError):
                    self.logger.warning(f"Cannot convert LTP to float for {symbol}: {ltp}")
                    continue

                # Extract base coin (B-BTC_USDT -> BTC)
                base_coin = self._extract_base_coin(symbol)

                # Get existing data to preserve funding rates
                redis_key = f"{self.redis_prefix}:{base_coin}"
                existing_data = self.redis_client.get_price_data(redis_key) or {}

                # Prepare additional data - CoinDCX uses short field names: v=volume, h=high, l=low, pc=price_change, mp=mark_price
                additional_data = {
                    'volume_24h': str(symbol_data.get('v', symbol_data.get('volume', '0'))),
                    'high_24h': str(symbol_data.get('h', symbol_data.get('high', '0'))),
                    'low_24h': str(symbol_data.get('l', symbol_data.get('low', '0'))),
                    'price_change_percent': str(symbol_data.get('pc', symbol_data.get('change_24h', '0'))),
                    'mark_price': str(symbol_data.get('mp', symbol_data.get('mark_price', '0'))),
                }

                # Also update funding rates from LTP response if available (fr=funding_rate, efr=estimated)
                if symbol_data.get('fr') is not None:
                    additional_data['current_funding_rate'] = str(symbol_data.get('fr'))
                elif 'current_funding_rate' in existing_data:
                    additional_data['current_funding_rate'] = existing_data['current_funding_rate']

                if symbol_data.get('efr') is not None:
                    additional_data['estimated_funding_rate'] = str(symbol_data.get('efr'))
                elif 'estimated_funding_rate' in existing_data:
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
                    updated_count += 1
                    self.logger.debug(f"Updated {base_coin}: ${price_float}")
                else:
                    self.logger.warning(f"Failed to write LTP to Redis for {base_coin}")

            except Exception as e:
                self.logger.error(f"Error processing LTP for {symbol}: {e}")

        self.logger.debug(f"Updated LTP for {updated_count} symbols")

    async def _fetch_and_store_orderbooks(self):
        """Fetch orderbook data for all symbols and store in Redis."""
        if not self._session:
            return

        # Fetch orderbooks for all symbols concurrently
        tasks = [self._fetch_single_orderbook(symbol) for symbol in self.symbols]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_single_orderbook(self, symbol: str):
        """Fetch and store orderbook for a single symbol.

        Args:
            symbol: The trading pair symbol
        """
        try:
            url = f"{self.orderbook_api_url}?pair={symbol}"
            async with self._session.get(url) as response:
                if response.status != 200:
                    self.logger.warning(f"Orderbook API returned {response.status} for {symbol}")
                    return

                data = await response.json()
                await self._process_orderbook_data(symbol, data)

        except Exception as e:
            self.logger.error(f"Error fetching orderbook for {symbol}: {e}")

    async def _process_orderbook_data(self, symbol: str, data: Dict):
        """Process orderbook data and store in Redis.

        Args:
            symbol: The trading pair symbol
            data: Orderbook data from API
        """
        try:
            # Validate response is a dict
            if not isinstance(data, dict):
                self.logger.warning(f"Invalid orderbook response type for {symbol}: {type(data)}")
                return

            # Extract bids and asks
            bids_raw = data.get('bids', {})
            asks_raw = data.get('asks', {})

            # Extract base coin early for Redis operations
            base_coin = self._extract_base_coin(symbol)
            redis_key = f"{self.orderbook_redis_prefix}:{base_coin}"

            # Parse orderbook levels - CoinDCX format: {"price": "quantity", ...}
            def parse_levels(levels) -> List[List[float]]:
                parsed = []
                # Handle dict format: {"82942": "0.00723398", ...}
                if isinstance(levels, dict):
                    for price_str, qty_str in levels.items():
                        try:
                            price = float(price_str)
                            qty = float(qty_str)
                            if price > 0 and qty > 0 and math.isfinite(price) and math.isfinite(qty):
                                parsed.append([price, qty])
                        except (ValueError, TypeError):
                            continue
                # Handle list format: [[price, qty], ...] or [{"price": x, "quantity": y}, ...]
                elif isinstance(levels, list):
                    for level in levels:
                        try:
                            if isinstance(level, list) and len(level) >= 2:
                                price = float(level[0])
                                qty = float(level[1])
                            elif isinstance(level, dict):
                                price = float(level.get('price', 0))
                                qty = float(level.get('quantity', 0))
                            else:
                                continue

                            if price > 0 and qty > 0 and math.isfinite(price) and math.isfinite(qty):
                                parsed.append([price, qty])
                        except (ValueError, TypeError):
                            continue
                return parsed

            bids = sorted(
                parse_levels(bids_raw),
                key=lambda x: x[0],
                reverse=True
            )[:self.orderbook_depth]

            asks = sorted(
                parse_levels(asks_raw),
                key=lambda x: x[0]
            )[:self.orderbook_depth]

            # Validate non-empty orderbook - don't store empty data
            if not bids or not asks:
                self.logger.warning(f"Empty orderbook for {symbol}, skipping Redis update")
                return

            # Calculate spread and mid price
            best_bid = bids[0][0]
            best_ask = asks[0][0]
            spread = best_ask - best_bid

            # Check for crossed book - delete stale Redis data if crossed
            if spread < 0:
                self.logger.warning(f"Crossed book for {symbol}: spread={spread}, deleting stale Redis data")
                self.redis_client.delete_key(redis_key)
                return

            mid_price = (best_bid + best_ask) / 2

            # Store in Redis
            success = self.redis_client.set_orderbook_data(
                key=redis_key,
                bids=bids,
                asks=asks,
                spread=spread,
                mid_price=mid_price,
                update_id=int(time.time() * 1000),
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(
                    f"Updated {base_coin} orderbook: spread=${spread:.2f}, "
                    f"mid=${mid_price:.2f}, {len(bids)} bids, {len(asks)} asks"
                )
            else:
                self.logger.warning(f"Failed to write orderbook to Redis for {base_coin}")

        except Exception as e:
            self.logger.error(f"Error processing orderbook for {symbol}: {e}")

    async def _fetch_and_store_trades(self):
        """Fetch trades data for all symbols and store in Redis."""
        if not self._session:
            return

        # Fetch trades for all symbols concurrently
        tasks = [self._fetch_single_trades(symbol) for symbol in self.symbols]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_single_trades(self, symbol: str):
        """Fetch and store trades for a single symbol.

        Args:
            symbol: The trading pair symbol
        """
        try:
            url = f"{self.trades_api_url}?pair={symbol}&limit={self.trades_limit}"
            async with self._session.get(url) as response:
                if response.status != 200:
                    self.logger.warning(f"Trades API returned {response.status} for {symbol}")
                    return

                data = await response.json()
                await self._process_trades_data(symbol, data)

        except Exception as e:
            self.logger.error(f"Error fetching trades for {symbol}: {e}")

    async def _process_trades_data(self, symbol: str, data: Any):
        """Process trades data and store in Redis.

        Args:
            symbol: The trading pair symbol
            data: Trades data from API
        """
        try:
            # Validate response format
            if not isinstance(data, (list, dict)):
                self.logger.warning(f"Invalid trades response type for {symbol}: {type(data)}")
                return

            # Handle different response formats
            trades_raw = data if isinstance(data, list) else data.get('trades', [])

            if not trades_raw:
                self.logger.debug(f"No trades data for {symbol}")
                return

            # Validate trades_raw is actually a list
            if not isinstance(trades_raw, list):
                self.logger.warning(f"Invalid trades data type for {symbol}: {type(trades_raw)}")
                return

            # Initialize buffer if needed
            if symbol not in self._trades:
                self._trades[symbol] = deque(maxlen=self.trades_limit)

            # Clear and repopulate (REST API returns full history)
            self._trades[symbol].clear()

            valid_trades = 0
            for trade in trades_raw:
                try:
                    # Validate trade is a dict
                    if not isinstance(trade, dict):
                        continue

                    # Parse trade data - CoinDCX format: {p: price, q: qty, T: timestamp, m: is_maker}
                    price = float(trade.get('p', trade.get('price', 0)))
                    qty = float(trade.get('q', trade.get('quantity', trade.get('size', 0))))

                    # Validate price and quantity
                    if price <= 0 or qty <= 0 or not math.isfinite(price) or not math.isfinite(qty):
                        continue

                    # T = timestamp (capital T in CoinDCX), t = timestamp (lowercase fallback)
                    timestamp = trade.get('T', trade.get('t', trade.get('timestamp', int(time.time() * 1000))))

                    # Validate timestamp is numeric
                    try:
                        timestamp = int(timestamp)
                    except (ValueError, TypeError):
                        timestamp = int(time.time() * 1000)

                    # m = is_maker: true means seller is maker (so taker bought), false means buyer is maker (so taker sold)
                    is_maker = trade.get('m')
                    if is_maker is not None:
                        side = 'Sell' if is_maker else 'Buy'
                    else:
                        side_raw = trade.get('side', trade.get('s', 'Buy'))
                        if isinstance(side_raw, str):
                            side = 'Buy' if side_raw.lower() in ['buy', 'b'] else 'Sell'
                        else:
                            side = 'Buy'

                    # Generate trade ID - use exchange ID if available, otherwise generate unique one
                    raw_trade_id = trade.get('id', trade.get('trade_id'))
                    if raw_trade_id is not None and str(raw_trade_id).strip():
                        trade_id = str(raw_trade_id)
                    else:
                        # Generate unique ID using timestamp + counter
                        # Use 'unknown_' prefix for consistency with Delta/HyperLiquid services
                        self._trade_counter += 1
                        trade_id = f"unknown_{timestamp}_{self._trade_counter}"

                    self._trades[symbol].append({
                        'p': price,
                        'q': qty,
                        's': side,
                        't': timestamp,
                        'id': trade_id
                    })
                    valid_trades += 1

                except (ValueError, TypeError) as e:
                    self.logger.debug(f"Skipping invalid trade: {e}")
                    continue

            # Only store if we have valid trades
            if valid_trades == 0:
                self.logger.warning(f"No valid trades parsed for {symbol}")
                return

            # Store in Redis
            base_coin = self._extract_base_coin(symbol)
            redis_key = f"{self.trades_redis_prefix}:{base_coin}"

            trades_list = list(self._trades[symbol])

            success = self.redis_client.set_trades_data(
                key=redis_key,
                trades=trades_list,
                original_symbol=symbol,
                ttl=self.redis_ttl
            )

            if success:
                self.logger.debug(f"Updated {base_coin} trades: {len(trades_list)} trades")
            else:
                self.logger.warning(f"Failed to write trades to Redis for {base_coin}")

        except Exception as e:
            self.logger.error(f"Error processing trades for {symbol}: {e}")

    async def _fetch_and_store_funding(self):
        """Fetch funding rate data and merge into LTP Redis keys."""
        if not self._session:
            return

        async with self._session.get(self.funding_api_url) as response:
            if response.status != 200:
                raise aiohttp.ClientError(f"Funding API returned status {response.status}")

            data = await response.json()
            await self._process_funding_data(data)

    async def _process_funding_data(self, data: Dict):
        """Process funding rate data and merge into LTP Redis keys.

        Args:
            data: Funding rate data from API
        """
        if not isinstance(data, dict) or 'prices' not in data:
            self.logger.error("Invalid funding rate data format")
            return

        prices_data = data.get('prices', {})
        updated_count = 0

        for symbol in self.symbols:
            try:
                symbol_upper = symbol.upper()
                if symbol_upper not in prices_data:
                    self.logger.debug(f"Symbol {symbol} not found in funding response")
                    continue

                symbol_data = prices_data[symbol_upper]
                current_rate = symbol_data.get('fr')
                estimated_rate = symbol_data.get('efr')

                if current_rate is None:
                    continue

                # Validate floats
                try:
                    fr_float = float(current_rate)
                    efr_float = float(estimated_rate or 0)
                    if not math.isfinite(fr_float) or not math.isfinite(efr_float):
                        self.logger.warning(f"Invalid funding rate for {symbol}")
                        continue
                except (ValueError, TypeError):
                    self.logger.warning(f"Malformed funding rate for {symbol}")
                    continue

                # Extract base coin
                base_coin = self._extract_base_coin(symbol)
                redis_key = f"{self.redis_prefix}:{base_coin}"

                # Get existing data to preserve LTP
                existing_data = self.redis_client.get_price_data(redis_key) or {}

                # Prepare funding rate data
                funding_data = {
                    'current_funding_rate': str(current_rate),
                    'estimated_funding_rate': str(estimated_rate or '0'),
                    'funding_timestamp': datetime.utcnow().isoformat() + 'Z'
                }

                # If we have existing LTP data, update it; otherwise create placeholder
                if 'ltp' in existing_data:
                    additional_data = {
                        k: v for k, v in existing_data.items()
                        if k not in ['ltp', 'timestamp', 'original_symbol']
                    }
                    additional_data.update(funding_data)

                    success = self.redis_client.set_price_data(
                        key=redis_key,
                        price=float(existing_data['ltp']),
                        symbol=existing_data.get('original_symbol', symbol),
                        additional_data=additional_data,
                        ttl=self.redis_ttl
                    )
                else:
                    # Skip writing placeholder - wait for LTP poller to create the entry
                    # Writing price=0.0 would cause downstream consumers (AOE) to read invalid price
                    self.logger.debug(
                        f"Skipping funding update for {base_coin} - no LTP data yet"
                    )
                    continue

                if success:
                    updated_count += 1
                    self.logger.debug(
                        f"Updated {base_coin} funding: "
                        f"current={fr_float*100:.4f}%, estimated={efr_float*100:.4f}%"
                    )

            except Exception as e:
                self.logger.error(f"Error processing funding rate for {symbol}: {e}")

        self.logger.info(f"Updated funding rates for {updated_count} symbols")

    def _extract_base_coin(self, symbol: str) -> str:
        """Extract base coin from CoinDCX futures symbol.

        Uses configurable symbol_prefix and quote_currencies for parsing.

        Args:
            symbol: Original exchange symbol (e.g., B-BTC_USDT)

        Returns:
            Base coin (e.g., BTC)
        """
        base = symbol

        # Remove configurable prefix (e.g., B- for Binance-backed)
        if self.symbol_prefix and base.startswith(self.symbol_prefix):
            base = base[len(self.symbol_prefix):]

        # Remove quote currency suffix
        if '_' in base:
            base = base.split('_')[0]
        else:
            # Try to strip quote currencies from end (for symbols like BTCUSDT)
            for quote in self.quote_currencies:
                if base.endswith(quote):
                    base = base[:-len(quote)]
                    break

        return base

    async def stop(self):
        """Stop the service."""
        self.running = False
        self._session = None
        self.logger.info("CoinDCX Futures REST Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('coindcx')
    service_config = config.get('services', {}).get('futures_rest', {})

    service = CoinDCXFuturesRESTService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

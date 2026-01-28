"""CoinDCX Funding Rate Service."""

import asyncio
import aiohttp
import math
from typing import Optional, Dict
from datetime import datetime

from core.base_service import BaseService


class CoinDCXFundingRateService(BaseService):
    """Service for fetching CoinDCX funding rates via REST API."""

    def __init__(self, config: dict):
        """Initialize CoinDCX Funding Rate Service.

        Args:
            config: Service configuration dictionary
        """
        super().__init__("CoinDCX-Funding-Rate", config)
        self.api_url = config.get(
            'api_url',
            'https://futures.coindcx.com/exchange/v1/funding_rate/v2'
        )
        self.symbols = config.get('symbols', [])
        self.update_interval = config.get('update_interval', 1800)  # 30 minutes
        self.api_timeout = config.get('api_timeout', 10)
        self.redis_prefix = config.get('redis_prefix', 'coindcx_futures')
        self.redis_ttl = config.get('redis_ttl', 60)

    async def start(self):
        """Start the funding rate fetching service."""
        if not self.is_enabled():
            self.logger.info("Service is disabled in configuration")
            return

        if not self.symbols:
            self.logger.error("No symbols configured")
            return

        self.running = True
        self.logger.info(f"Starting funding rate updates every {self.update_interval}s")
        self.logger.info(f"Monitoring symbols: {', '.join(self.symbols)}")

        while self.running:
            try:
                await self._fetch_and_store_funding_rates()
                await asyncio.sleep(self.update_interval)
            except Exception as e:
                self.logger.error(f"Error in funding rate update: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error

    async def _fetch_and_store_funding_rates(self):
        """Fetch funding rates from API and store in Redis."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.api_url,
                    timeout=aiohttp.ClientTimeout(total=self.api_timeout)
                ) as response:
                    if response.status != 200:
                        self.logger.error(f"API returned status {response.status}")
                        return

                    data = await response.json()
                    await self._process_funding_rates(data)

        except asyncio.TimeoutError:
            self.logger.error("API request timeout")
        except Exception as e:
            self.logger.error(f"Error fetching funding rates: {e}")

    async def _process_funding_rates(self, data: Dict):
        """Process funding rate data and store in Redis.

        Args:
            data: Funding rate data from API (format: {"prices": {"SYMBOL": {"fr": x, "efr": y}}})
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
                    self.logger.debug(f"Symbol {symbol} not found in API response")
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
                        self.logger.warning(f"Invalid funding rate for {symbol}: fr={current_rate}, efr={estimated_rate}")
                        continue
                except (ValueError, TypeError):
                    self.logger.warning(f"Malformed funding rate for {symbol}: fr={current_rate}")
                    continue

                # Extract base coin (e.g., BTC from B-BTC_USDT)
                base_coin = symbol.replace('B-', '').split('_')[0]

                # Store in Redis - preserve LTP data if available
                redis_key = f"{self.redis_prefix}:{base_coin}"

                # Get existing data to preserve LTP
                existing_data = self.redis_client.get_price_data(redis_key) or {}

                # Prepare funding rate data
                additional_data = {
                    'current_funding_rate': str(current_rate),
                    'estimated_funding_rate': str(estimated_rate or '0'),
                    'funding_timestamp': datetime.utcnow().isoformat() + 'Z'
                }

                # If we have existing LTP data, update it; otherwise create new entry
                if 'ltp' in existing_data:
                    # Update existing entry with funding rate data
                    success = self.redis_client.set_price_data(
                        key=redis_key,
                        price=float(existing_data['ltp']),
                        symbol=existing_data.get('original_symbol', symbol),
                        additional_data={
                            **{k: v for k, v in existing_data.items()
                               if k not in ['ltp', 'timestamp', 'original_symbol']},
                            **additional_data
                        },
                        ttl=self.redis_ttl
                    )
                else:
                    # Create new entry with just funding rate (LTP will be updated by LTP service)
                    success = self.redis_client.set_price_data(
                        key=redis_key,
                        price=0.0,  # Placeholder until LTP updates
                        symbol=symbol,
                        additional_data=additional_data,
                        ttl=self.redis_ttl
                    )

                if success:
                    updated_count += 1
                    self.logger.debug(
                        f"Updated {base_coin} funding rate: "
                        f"current={float(current_rate)*100:.4f}%, "
                        f"estimated={float(estimated_rate or 0)*100:.4f}%"
                    )

            except Exception as e:
                self.logger.error(f"Error processing funding rate for {symbol}: {e}")

        self.logger.info(f"Updated funding rates for {updated_count} symbols")

    async def stop(self):
        """Stop the service."""
        self.running = False
        self.logger.info("CoinDCX Funding Rate Service stopped")


async def main():
    """Main entry point for running service standalone."""
    from config.settings import Settings

    config = Settings.load_exchange_config('coindcx')
    service_config = config.get('services', {}).get('funding_rate', {})

    service = CoinDCXFundingRateService(service_config)
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())

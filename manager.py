"""Service Manager for price_ltp - Coordinates all exchange services."""

import asyncio
import signal
from typing import List, Dict

from core.logging import setup_logger
from core.redis_client import RedisClient
from config.settings import Settings

# Import services
from services.bybit_s import BybitSpotService
from services.coindcx_f import CoinDCXFuturesLTPService, CoinDCXFundingRateService
from services.delta_f import DeltaFuturesLTPService


class ServiceManager:
    """Manages all exchange services."""

    def __init__(self):
        """Initialize Service Manager."""
        self.logger = setup_logger('ServiceManager', log_file='service_manager.log')
        self.services: List = []
        self.running = False
        self._shutdown_event = asyncio.Event()

    def setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals.

        Args:
            signum: Signal number
            frame: Current stack frame
        """
        self.logger.info(f"Received signal {signum}, initiating shutdown...")
        self._shutdown_event.set()

    def _initialize_services(self):
        """Initialize all configured services."""
        self.logger.info("Initializing services...")

        # Load exchange configurations
        exchanges = Settings.get_all_exchanges()
        self.logger.info(f"Found {len(exchanges)} configured exchanges: {', '.join(exchanges)}")

        for exchange in exchanges:
            try:
                config = Settings.load_exchange_config(exchange)
                if not config or not config.get('enabled', False):
                    self.logger.info(f"Exchange '{exchange}' is disabled, skipping...")
                    continue

                self._load_exchange_services(exchange, config)

            except Exception as e:
                self.logger.error(f"Error loading {exchange} services: {e}", exc_info=True)

        self.logger.info(f"Initialized {len(self.services)} services")

    def _load_exchange_services(self, exchange: str, config: Dict):
        """Load services for a specific exchange.

        Args:
            exchange: Exchange name
            config: Exchange configuration
        """
        services_config = config.get('services', {})

        if exchange == 'bybit':
            # Bybit Spot Service
            spot_config = services_config.get('spot', {})
            if spot_config.get('enabled', False):
                self.services.append(BybitSpotService(spot_config))
                self.logger.info("✓ Bybit Spot Service loaded")

        elif exchange == 'coindcx':
            # CoinDCX Futures LTP Service
            ltp_config = services_config.get('futures_ltp', {})
            if ltp_config.get('enabled', False):
                self.services.append(CoinDCXFuturesLTPService(ltp_config))
                self.logger.info("✓ CoinDCX Futures LTP Service loaded")

            # CoinDCX Funding Rate Service
            funding_config = services_config.get('funding_rate', {})
            if funding_config.get('enabled', False):
                self.services.append(CoinDCXFundingRateService(funding_config))
                self.logger.info("✓ CoinDCX Funding Rate Service loaded")

        elif exchange == 'delta':
            # Delta Futures LTP Service
            ltp_config = services_config.get('futures_ltp', {})
            if ltp_config.get('enabled', False):
                self.services.append(DeltaFuturesLTPService(ltp_config))
                self.logger.info("✓ Delta Futures LTP Service loaded")

    async def start_all_services(self):
        """Start all services concurrently."""
        if not self.services:
            self.logger.error("No services to start!")
            return

        self.logger.info("=" * 80)
        self.logger.info(f"Starting {len(self.services)} services...")
        self.logger.info("=" * 80)

        # Create tasks for all services
        tasks = [asyncio.create_task(service.run()) for service in self.services]

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Stop all services
        self.logger.info("Stopping all services...")
        for service in self.services:
            try:
                await service.stop()
            except Exception as e:
                self.logger.error(f"Error stopping service: {e}")

        # Wait for all tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        """Run the service manager."""
        self.setup_signal_handlers()

        try:
            # Test Redis connection
            redis_client = RedisClient()
            if not redis_client.ping():
                self.logger.error("Failed to connect to Redis. Please ensure Redis is running.")
                return

            self.logger.info("✓ Redis connection successful")

            # Initialize and start services
            self._initialize_services()

            if not self.services:
                self.logger.error("No services configured. Please check config/exchanges.yaml")
                return

            # Display service summary
            self._display_startup_summary()

            # Start all services
            await self.start_all_services()

        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt")
        except Exception as e:
            self.logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            self.logger.info("Service Manager shutdown complete")

    def _display_startup_summary(self):
        """Display startup summary."""
        self.logger.info("")
        self.logger.info("=" * 80)
        self.logger.info("SERVICE SUMMARY")
        self.logger.info("=" * 80)

        for i, service in enumerate(self.services, 1):
            self.logger.info(f"{i}. {service.service_name}")

        self.logger.info("=" * 80)
        self.logger.info("Press Ctrl+C to stop all services")
        self.logger.info("=" * 80)
        self.logger.info("")


async def main():
    """Main entry point."""
    manager = ServiceManager()
    await manager.run()


if __name__ == '__main__':
    # Run the service manager
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")

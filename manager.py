"""Service Manager for price_ltp - Coordinates all exchange services."""

import asyncio
import signal
from typing import List, Dict, Optional

from core.logging import setup_logger
from core.redis_client import RedisClient
from core.control_interface import ControlInterface
from config.settings import Settings

# Import services
from services.bybit_s import BybitSpotService
from services.coindcx_f import CoinDCXFuturesLTPService, CoinDCXFundingRateService
from services.delta_f import DeltaFuturesLTPService
from services.delta_o import DeltaOptionsService
from services.hyperliquid_s import HyperLiquidSpotService
from services.hyperliquid_p import HyperLiquidPerpetualService


class ServiceManager:
    """Manages all exchange services."""

    def __init__(self):
        """Initialize Service Manager."""
        self.logger = setup_logger('ServiceManager', log_file='service_manager.log')
        self.services: List = []
        self.service_registry: Dict[str, Dict] = {}  # Map service_id to service instance and task
        self.control = ControlInterface()
        self.running = False
        self._shutdown_event = asyncio.Event()
        self.control_check_interval = 2  # Check for control commands every 2 seconds

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
                service = BybitSpotService(spot_config)
                self.services.append(service)
                self.service_registry['bybit_spot'] = {
                    'service': service,
                    'task': None,
                    'config': spot_config
                }
                self.logger.info("✓ Bybit Spot Service loaded")

        elif exchange == 'coindcx':
            # CoinDCX Futures LTP Service
            ltp_config = services_config.get('futures_ltp', {})
            if ltp_config.get('enabled', False):
                service = CoinDCXFuturesLTPService(ltp_config)
                self.services.append(service)
                self.service_registry['coindcx_futures_ltp'] = {
                    'service': service,
                    'task': None,
                    'config': ltp_config
                }
                self.logger.info("✓ CoinDCX Futures LTP Service loaded")

            # CoinDCX Funding Rate Service
            funding_config = services_config.get('funding_rate', {})
            if funding_config.get('enabled', False):
                service = CoinDCXFundingRateService(funding_config)
                self.services.append(service)
                self.service_registry['coindcx_funding_rate'] = {
                    'service': service,
                    'task': None,
                    'config': funding_config
                }
                self.logger.info("✓ CoinDCX Funding Rate Service loaded")

        elif exchange == 'delta':
            # Delta Futures LTP Service
            ltp_config = services_config.get('futures_ltp', {})
            if ltp_config.get('enabled', False):
                service = DeltaFuturesLTPService(ltp_config)
                self.services.append(service)
                self.service_registry['delta_futures_ltp'] = {
                    'service': service,
                    'task': None,
                    'config': ltp_config
                }
                self.logger.info("✓ Delta Futures LTP Service loaded")

            # Delta Options Service
            options_config = services_config.get('options', {})
            if options_config.get('enabled', False):
                service = DeltaOptionsService(options_config)
                self.services.append(service)
                self.service_registry['delta_options'] = {
                    'service': service,
                    'task': None,
                    'config': options_config
                }
                self.logger.info("✓ Delta Options Service loaded")

        elif exchange == 'hyperliquid':
            # HyperLiquid Spot Service
            spot_config = services_config.get('spot', {})
            if spot_config.get('enabled', False):
                service = HyperLiquidSpotService(spot_config)
                self.services.append(service)
                self.service_registry['hyperliquid_spot'] = {
                    'service': service,
                    'task': None,
                    'config': spot_config
                }
                self.logger.info("✓ HyperLiquid Spot Service loaded")

            # HyperLiquid Perpetual Service
            perp_config = services_config.get('perpetual', {})
            if perp_config.get('enabled', False):
                service = HyperLiquidPerpetualService(perp_config)
                self.services.append(service)
                self.service_registry['hyperliquid_perpetual'] = {
                    'service': service,
                    'task': None,
                    'config': perp_config
                }
                self.logger.info("✓ HyperLiquid Perpetual Service loaded")

    async def start_service(self, service_id: str) -> bool:
        """Start a specific service.

        Args:
            service_id: Service identifier

        Returns:
            Success status
        """
        if service_id not in self.service_registry:
            self.logger.error(f"Service '{service_id}' not found in registry")
            return False

        service_info = self.service_registry[service_id]

        # Check if already running
        if service_info['task'] and not service_info['task'].done():
            self.logger.warning(f"Service '{service_id}' is already running")
            return False

        try:
            self.logger.info(f"Starting service: {service_id}")
            self.control.update_service_status(service_id, 'starting')

            # Create and start task
            service = service_info['service']
            service_info['task'] = asyncio.create_task(service.run())

            self.control.update_service_status(service_id, 'running')
            self.logger.info(f"✓ Service '{service_id}' started successfully")
            return True

        except Exception as e:
            self.logger.error(f"Error starting service '{service_id}': {e}")
            self.control.update_service_status(service_id, 'error', {'error': str(e)})
            return False

    async def stop_service(self, service_id: str) -> bool:
        """Stop a specific service.

        Args:
            service_id: Service identifier

        Returns:
            Success status
        """
        if service_id not in self.service_registry:
            self.logger.error(f"Service '{service_id}' not found in registry")
            return False

        service_info = self.service_registry[service_id]

        # Check if not running
        if not service_info['task'] or service_info['task'].done():
            self.logger.warning(f"Service '{service_id}' is not running")
            self.control.update_service_status(service_id, 'stopped')
            return False

        try:
            self.logger.info(f"Stopping service: {service_id}")
            self.control.update_service_status(service_id, 'stopping')

            # Stop service
            service = service_info['service']
            await service.stop()

            # Wait for task to complete
            try:
                await asyncio.wait_for(service_info['task'], timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning(f"Service '{service_id}' did not stop gracefully, canceling...")
                service_info['task'].cancel()

            service_info['task'] = None
            self.control.update_service_status(service_id, 'stopped')
            self.logger.info(f"✓ Service '{service_id}' stopped successfully")
            return True

        except Exception as e:
            self.logger.error(f"Error stopping service '{service_id}': {e}")
            self.control.update_service_status(service_id, 'error', {'error': str(e)})
            return False

    async def check_control_commands(self):
        """Check Redis for control commands and execute them."""
        while self.running:
            try:
                for service_id in self.service_registry.keys():
                    command = self.control.get_control_command(service_id)

                    if command:
                        action = command.get('action')
                        self.logger.info(f"Received command '{action}' for service '{service_id}'")

                        if action == 'start':
                            await self.start_service(service_id)
                        elif action == 'stop':
                            await self.stop_service(service_id)

                        # Clear the command after processing
                        self.control.clear_control_command(service_id)

            except Exception as e:
                self.logger.error(f"Error checking control commands: {e}")

            await asyncio.sleep(self.control_check_interval)

    async def start_all_services(self):
        """Start all services concurrently."""
        if not self.services:
            self.logger.error("No services to start!")
            return

        self.logger.info("=" * 80)
        self.logger.info(f"Starting {len(self.services)} services...")
        self.logger.info("=" * 80)

        # Start all services
        for service_id in self.service_registry.keys():
            await self.start_service(service_id)

        # Start control command checker
        control_task = asyncio.create_task(self.check_control_commands())

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Cancel control task
        control_task.cancel()
        try:
            await control_task
        except asyncio.CancelledError:
            pass

        # Stop all services
        self.logger.info("Stopping all services...")
        for service_id in self.service_registry.keys():
            await self.stop_service(service_id)

    async def wait_for_commands(self):
        """Wait for control commands and auto-start services with auto_start flag."""
        # Auto-start services that have auto_start flag enabled
        auto_start_services = []
        for service_id, service_info in self.service_registry.items():
            if service_info.get('config', {}).get('auto_start', False):
                auto_start_services.append(service_id)

        if auto_start_services:
            self.logger.info("=" * 80)
            self.logger.info(f"Auto-starting {len(auto_start_services)} services...")
            self.logger.info("=" * 80)
            for service_id in auto_start_services:
                await self.start_service(service_id)
                # Small delay between starting services
                await asyncio.sleep(1)

        self.logger.info("=" * 80)
        self.logger.info("Service Manager ready")
        self.logger.info("Use the web dashboard to start/stop services")
        self.logger.info("=" * 80)

        # Start control command checker
        control_task = asyncio.create_task(self.check_control_commands())

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Cancel control task
        control_task.cancel()
        try:
            await control_task
        except asyncio.CancelledError:
            pass

        # Stop all running services
        self.logger.info("Stopping all services...")
        for service_id in self.service_registry.keys():
            await self.stop_service(service_id)

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

            # Initialize all service statuses to 'stopped'
            for service_id in self.service_registry.keys():
                self.control.update_service_status(service_id, 'stopped')

            # Set running flag
            self.running = True

            # Wait for control commands (don't auto-start services)
            await self.wait_for_commands()

        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt")
        except Exception as e:
            self.logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            self.running = False
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

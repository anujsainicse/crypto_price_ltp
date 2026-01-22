"""Base service class for all exchange services."""

import asyncio
import signal
from abc import ABC, abstractmethod
from typing import Optional

from core.logging import setup_logger
from core.redis_client import RedisClient


class BaseService(ABC):
    """Base class for all exchange services."""

    def __init__(self, service_name: str, config: dict):
        """Initialize base service.

        Args:
            service_name: Name of the service
            config: Service configuration dictionary
        """
        self.service_name = service_name
        self.config = config
        self.logger = setup_logger(
            service_name,
            log_file=f"{service_name.lower().replace(' ', '_')}.log"
        )
        self.redis_client = RedisClient()
        self.running = False
        self._shutdown_event = asyncio.Event()

    @abstractmethod
    async def start(self):
        """Start the service. Must be implemented by subclasses."""
        pass

    @abstractmethod
    async def stop(self):
        """Stop the service. Must be implemented by subclasses."""
        pass

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
        self.logger.info(f"Received signal {signum}, shutting down...")
        # Thread-safe event set from synchronous signal handler
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self._shutdown_event.set)
        except RuntimeError:
            # No running loop, set directly
            self._shutdown_event.set()

    async def run(self):
        """Run the service with signal handling."""
        self.setup_signal_handlers()
        self.logger.info(f"Starting {self.service_name}...")

        try:
            await self.start()
        except Exception as e:
            self.logger.error(f"Error in {self.service_name}: {e}", exc_info=True)
        finally:
            await self.stop()
            self.logger.info(f"{self.service_name} stopped")

    def is_enabled(self) -> bool:
        """Check if service is enabled in configuration.

        Returns:
            True if enabled, False otherwise
        """
        return self.config.get('enabled', True)

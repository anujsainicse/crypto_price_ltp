"""Configuration management for price_ltp."""

import os
from typing import Dict, List
from pathlib import Path
import yaml
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Settings:
    """Application settings."""

    # Redis Configuration
    REDIS_HOST: str = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT: int = int(os.getenv('REDIS_PORT', '6379'))
    REDIS_PASSWORD: str = os.getenv('REDIS_PASSWORD', '')
    REDIS_DB: int = int(os.getenv('REDIS_DB', '0'))
    REDIS_TTL: int = int(os.getenv('REDIS_TTL', '3600'))

    # Application Configuration
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    LOG_DIR: str = os.getenv('LOG_DIR', 'logs')

    # Service Configuration
    SERVICE_RESTART_DELAY: int = int(os.getenv('SERVICE_RESTART_DELAY', '5'))
    SERVICE_MAX_RETRIES: int = int(os.getenv('SERVICE_MAX_RETRIES', '10'))

    # Monitoring
    HEALTH_CHECK_INTERVAL: int = int(os.getenv('HEALTH_CHECK_INTERVAL', '30'))
    STATUS_UPDATE_INTERVAL: int = int(os.getenv('STATUS_UPDATE_INTERVAL', '30'))

    @classmethod
    def load_exchange_config(cls, exchange_name: str) -> Dict:
        """Load exchange-specific configuration from YAML file.

        Args:
            exchange_name: Name of the exchange (e.g., 'bybit', 'coindcx')

        Returns:
            Dictionary containing exchange configuration
        """
        config_file = Path(__file__).parent / 'exchanges.yaml'
        if not config_file.exists():
            return {}

        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
            return config.get(exchange_name, {})

    @classmethod
    def get_all_exchanges(cls) -> List[str]:
        """Get list of all configured exchanges.

        Returns:
            List of exchange names
        """
        config_file = Path(__file__).parent / 'exchanges.yaml'
        if not config_file.exists():
            return []

        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
            return list(config.keys()) if config else []


settings = Settings()

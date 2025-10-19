"""Control Interface - Redis-based communication between Dashboard and Manager."""

import json
from typing import Dict, List, Optional
from datetime import datetime
from core.redis_client import RedisClient


class ControlInterface:
    """Manages service control commands and status via Redis."""

    def __init__(self):
        """Initialize control interface."""
        self.redis_client = RedisClient()
        self.CONTROL_PREFIX = "service:control"
        self.STATUS_PREFIX = "service:status"
        self.STATS_PREFIX = "service:stats"

    # ==================== Control Commands ====================

    def send_start_command(self, service_id: str) -> bool:
        """Send start command for a service.

        Args:
            service_id: Service identifier (e.g., 'bybit_spot', 'coindcx_futures_ltp')

        Returns:
            Success status
        """
        key = f"{self.CONTROL_PREFIX}:{service_id}"
        command = {
            'action': 'start',
            'timestamp': datetime.utcnow().isoformat()
        }
        return self.redis_client._client.setex(key, 60, json.dumps(command))  # Expires in 60s

    def send_stop_command(self, service_id: str) -> bool:
        """Send stop command for a service.

        Args:
            service_id: Service identifier

        Returns:
            Success status
        """
        key = f"{self.CONTROL_PREFIX}:{service_id}"
        command = {
            'action': 'stop',
            'timestamp': datetime.utcnow().isoformat()
        }
        return self.redis_client._client.setex(key, 60, json.dumps(command))  # Expires in 60s

    def get_control_command(self, service_id: str) -> Optional[Dict]:
        """Get pending control command for a service.

        Args:
            service_id: Service identifier

        Returns:
            Command dict or None
        """
        key = f"{self.CONTROL_PREFIX}:{service_id}"
        data = self.redis_client._client.get(key)
        if data:
            return json.loads(data)
        return None

    def clear_control_command(self, service_id: str) -> bool:
        """Clear control command after processing.

        Args:
            service_id: Service identifier

        Returns:
            Success status
        """
        key = f"{self.CONTROL_PREFIX}:{service_id}"
        return bool(self.redis_client._client.delete(key))

    # ==================== Status Management ====================

    def update_service_status(self, service_id: str, status: str,
                            details: Optional[Dict] = None) -> bool:
        """Update service status.

        Args:
            service_id: Service identifier
            status: Service status ('running', 'stopped', 'starting', 'stopping', 'error')
            details: Additional details (optional)

        Returns:
            Success status
        """
        key = f"{self.STATUS_PREFIX}:{service_id}"
        status_data = {
            'status': status,
            'last_update': datetime.utcnow().isoformat(),
            'details': details or {}
        }
        return self.redis_client._client.setex(key, 300, json.dumps(status_data))  # Expires in 5 min

    def get_service_status(self, service_id: str) -> Optional[Dict]:
        """Get service status.

        Args:
            service_id: Service identifier

        Returns:
            Status dict or None
        """
        key = f"{self.STATUS_PREFIX}:{service_id}"
        data = self.redis_client._client.get(key)
        if data:
            return json.loads(data)
        return None

    def get_all_services_status(self) -> Dict[str, Dict]:
        """Get status of all services.

        Returns:
            Dict mapping service_id to status
        """
        pattern = f"{self.STATUS_PREFIX}:*"
        keys = self.redis_client._client.keys(pattern)

        statuses = {}
        for key in keys:
            # Handle both bytes and string keys
            if isinstance(key, bytes):
                key_str = key.decode('utf-8')
            else:
                key_str = key

            service_id = key_str.split(':', 2)[2]
            data = self.redis_client._client.get(key)
            if data:
                statuses[service_id] = json.loads(data)

        return statuses

    # ==================== Statistics ====================

    def update_service_stats(self, service_id: str, stats: Dict) -> bool:
        """Update service statistics.

        Args:
            service_id: Service identifier
            stats: Statistics dict (data_points, last_update_time, etc.)

        Returns:
            Success status
        """
        key = f"{self.STATS_PREFIX}:{service_id}"
        stats_data = {
            'stats': stats,
            'last_update': datetime.utcnow().isoformat()
        }
        return self.redis_client._client.setex(key, 300, json.dumps(stats_data))  # Expires in 5 min

    def get_service_stats(self, service_id: str) -> Optional[Dict]:
        """Get service statistics.

        Args:
            service_id: Service identifier

        Returns:
            Stats dict or None
        """
        key = f"{self.STATS_PREFIX}:{service_id}"
        data = self.redis_client._client.get(key)
        if data:
            return json.loads(data)
        return None

    # ==================== Helper Methods ====================

    def get_exchange_data_count(self, redis_prefix: str) -> int:
        """Count data points for an exchange.

        Args:
            redis_prefix: Redis key prefix (e.g., 'bybit_spot', 'delta_futures')

        Returns:
            Number of keys
        """
        pattern = f"{redis_prefix}:*"
        keys = self.redis_client._client.keys(pattern)
        return len(keys)

    def get_all_data_counts(self) -> Dict[str, int]:
        """Get data counts for all exchanges.

        Returns:
            Dict mapping prefix to count
        """
        prefixes = [
            'bybit_spot',
            'coindcx_futures',
            'delta_futures',
            'delta_options'
        ]

        counts = {}
        for prefix in prefixes:
            counts[prefix] = self.get_exchange_data_count(prefix)

        return counts

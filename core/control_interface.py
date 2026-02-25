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
        self.LEASE_PREFIX = "service:lease"

    # ==================== Control Commands ====================

    def send_start_command(self, service_id: str) -> bool:
        """Send start command for a service.

        Args:
            service_id: Service identifier (e.g., 'bybit_spot', 'coindcx_futures_rest')

        Returns:
            Success status
        """
        key = f"{self.CONTROL_PREFIX}:{service_id}"
        command = {
            'action': 'start',
            'timestamp': datetime.utcnow().isoformat()
        }
        return self.redis_client.set_ex(key, 60, json.dumps(command))  # Expires in 60s

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
        return self.redis_client.set_ex(key, 60, json.dumps(command))  # Expires in 60s

    def get_control_command(self, service_id: str) -> Optional[Dict]:
        """Get pending control command for a service.

        Args:
            service_id: Service identifier

        Returns:
            Command dict or None
        """
        key = f"{self.CONTROL_PREFIX}:{service_id}"
        data = self.redis_client.get(key)
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
        return bool(self.redis_client.delete_key(key))

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
        return self.redis_client.set_ex(key, 300, json.dumps(status_data))  # Expires in 5 min

    def get_service_status(self, service_id: str) -> Optional[Dict]:
        """Get service status.

        Args:
            service_id: Service identifier

        Returns:
            Status dict or None
        """
        key = f"{self.STATUS_PREFIX}:{service_id}"
        data = self.redis_client.get(key)
        if data:
            return json.loads(data)
        return None

    def get_all_services_status(self) -> Dict[str, Dict]:
        """Get status of all services.

        Returns:
            Dict mapping service_id to status
        """
        pattern = f"{self.STATUS_PREFIX}:*"
        keys = self.redis_client.get_all_keys(pattern)

        statuses = {}
        for key in keys:
            # Handle both bytes and string keys
            if isinstance(key, bytes):
                key_str = key.decode('utf-8')
            else:
                key_str = key

            service_id = key_str.split(':', 2)[2]
            data = self.redis_client.get(key)
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
        return self.redis_client.set_ex(key, 300, json.dumps(stats_data))  # Expires in 5 min

    def get_service_stats(self, service_id: str) -> Optional[Dict]:
        """Get service statistics.

        Args:
            service_id: Service identifier

        Returns:
            Stats dict or None
        """
        key = f"{self.STATS_PREFIX}:{service_id}"
        data = self.redis_client.get(key)
        if data:
            return json.loads(data)
        return None

    # ==================== Lease Management ====================

    def set_service_lease(
        self,
        service_id: str,
        source: str = "dashboard",
        bot_id: Optional[str] = None,
        ttl: Optional[int] = 3600
    ) -> bool:
        """Create or refresh a service lease.

        Args:
            service_id: Service identifier (e.g., 'delta_futures_ltp')
            source: Who is requesting the lease: 'scalper_bot', 'scalper_prewarm',
                    or 'dashboard'
            bot_id: Optional bot UUID for scalper-originated leases
            ttl: Seconds until auto-expiry. None means persistent (no expiry),
                 used for dashboard-started services.

        Returns:
            True if lease was set successfully
        """
        key = f"{self.LEASE_PREFIX}:{service_id}"
        lease_data = json.dumps({
            'source': source,
            'bot_id': bot_id,
            'created_at': datetime.utcnow().isoformat(),
            'last_heartbeat': datetime.utcnow().isoformat(),
        })
        if ttl is None:
            # Persistent lease - dashboard started, never auto-expires
            return self.redis_client.set(key, lease_data)
        else:
            return self.redis_client.set_ex(key, ttl, lease_data)

    def refresh_service_lease(self, service_id: str, ttl: int = 3600) -> bool:
        """Refresh an existing lease, resetting its TTL.

        Persistent leases (TTL == -1, set by dashboard) are not downgraded —
        only the last_heartbeat timestamp is updated.

        Args:
            service_id: Service identifier
            ttl: New TTL in seconds (default 3600), ignored for persistent leases

        Returns:
            True if lease existed and was refreshed. False if no lease found.
        """
        key = f"{self.LEASE_PREFIX}:{service_id}"
        existing = self.redis_client.get(key)
        if existing is None:
            return False
        try:
            data = json.loads(existing)
            data['last_heartbeat'] = datetime.utcnow().isoformat()
        except (json.JSONDecodeError, AttributeError):
            data = {'last_heartbeat': datetime.utcnow().isoformat()}
        # Preserve persistence: if the lease has no expiry, keep it that way.
        current_ttl = self.redis_client.get_ttl(key)
        if current_ttl == -1:
            return self.redis_client.set(key, json.dumps(data))
        return self.redis_client.set_ex(key, ttl, json.dumps(data))

    def get_service_lease(self, service_id: str) -> Optional[Dict]:
        """Get current lease metadata for a service.

        Returns:
            Lease dict with 'source', 'bot_id', 'created_at', 'last_heartbeat',
            or None if no active lease exists.
        """
        key = f"{self.LEASE_PREFIX}:{service_id}"
        data = self.redis_client.get(key)
        if data:
            return json.loads(data)
        return None

    def get_lease_ttl(self, service_id: str) -> int:
        """Get remaining TTL of a service lease.

        Returns:
            Seconds remaining, -1 if persistent (no expiry), -2 if no lease.
        """
        key = f"{self.LEASE_PREFIX}:{service_id}"
        return self.redis_client.get_ttl(key)

    def revoke_service_lease(self, service_id: str) -> bool:
        """Delete a service lease immediately.

        The service will be stopped within the next health monitor cycle (~30s).
        """
        key = f"{self.LEASE_PREFIX}:{service_id}"
        return self.redis_client.delete_key(key)

    # ==================== Helper Methods ====================

    def is_redis_connected(self) -> bool:
        """Check if Redis connection is alive.

        Returns:
            True if Redis is connected, False otherwise
        """
        return self.redis_client.ping()

    def get_exchange_data_count(self, redis_prefix: str) -> int:
        """Count data points for an exchange.

        Args:
            redis_prefix: Redis key prefix (e.g., 'bybit_spot', 'delta_futures')

        Returns:
            Number of keys
        """
        pattern = f"{redis_prefix}:*"
        keys = self.redis_client.get_all_keys(pattern)
        return len(keys)

    def get_all_data_counts(self) -> Dict[str, int]:
        """Get data counts for all exchanges.

        Returns:
            Dict mapping prefix to count
        """
        prefixes = [
            'bybit_spot',
            'bybit_futures_ob',
            'bybit_options',
            'bybit_spot_testnet',
            'bybit_futures_testnet',
            'bybit_options_testnet',
            'coindcx_spot',
            'coindcx_futures',
            'delta_spot',
            'delta_futures',
            'delta_options',
            'hyperliquid_spot',
            'hyperliquid_futures',
            'binance_spot',
            'binance_futures',
            'binance_options',
        ]

        counts = {}
        for prefix in prefixes:
            # Count base (LTP) + orderbook (_ob) + trades (_trades) keys
            ltp = self.get_exchange_data_count(prefix)
            ob = self.get_exchange_data_count(f"{prefix}_ob")
            trades = self.get_exchange_data_count(f"{prefix}_trades")
            counts[prefix] = ltp + ob + trades

        return counts

    def get_all_data_counts_breakdown(self) -> Dict[str, Dict[str, int]]:
        """Get per-type data counts for all exchanges.

        Returns:
            Dict mapping prefix to {ltp: N, orderbook: N, trades: N}
        """
        prefixes = [
            'bybit_spot',
            'bybit_futures_ob',
            'bybit_options',
            'bybit_spot_testnet',
            'bybit_futures_testnet',
            'bybit_options_testnet',
            'coindcx_spot',
            'coindcx_futures',
            'delta_spot',
            'delta_futures',
            'delta_options',
            'hyperliquid_spot',
            'hyperliquid_futures',
            'binance_spot',
            'binance_futures',
            'binance_options',
        ]

        breakdown = {}
        for prefix in prefixes:
            breakdown[prefix] = {
                'ltp': self.get_exchange_data_count(prefix),
                'orderbook': self.get_exchange_data_count(f"{prefix}_ob"),
                'trades': self.get_exchange_data_count(f"{prefix}_trades"),
            }

        return breakdown

"""Redis client for price_ltp."""

import json
import redis
from typing import Optional, Dict, Any, List
from datetime import datetime

from config.settings import settings
from core.logging import get_logger


class RedisClient:
    """Redis client with connection management."""

    _instance: Optional['RedisClient'] = None
    _client: Optional[redis.Redis] = None

    def __new__(cls):
        """Singleton pattern to ensure single Redis connection."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize Redis client."""
        if self._client is None:
            self.logger = get_logger('RedisClient')
            self._connect()

    def _connect(self):
        """Establish Redis connection."""
        try:
            self._client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
                db=settings.REDIS_DB,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            # Test connection
            self._client.ping()
            self.logger.info(
                f"Connected to Redis at {settings.REDIS_HOST}:{settings.REDIS_PORT}"
            )
        except Exception as e:
            self.logger.error(f"Failed to connect to Redis: {e}")
            raise

    def ping(self) -> bool:
        """Check if Redis connection is alive.

        Returns:
            True if connection is alive, False otherwise
        """
        try:
            return self._client.ping()
        except Exception as e:
            self.logger.error(f"Redis ping failed: {e}")
            return False

    def set_price_data(
        self,
        key: str,
        price: float,
        symbol: str,
        additional_data: Optional[Dict[str, Any]] = None,
        ttl: Optional[int] = None
    ) -> bool:
        """Store price data in Redis as a hash.

        Args:
            key: Redis key (e.g., 'bybit_spot:BTC')
            price: Last traded price
            symbol: Original symbol name
            additional_data: Additional fields to store
            ttl: Time to live in seconds (default from settings)

        Returns:
            True if successful, False otherwise
        """
        try:
            data = {
                'ltp': str(price),
                'timestamp': str(int(datetime.utcnow().timestamp())),
                'original_symbol': symbol
            }

            # Add additional data if provided
            if additional_data:
                data.update({k: str(v) for k, v in additional_data.items()})

            # Store as hash
            self._client.hset(key, mapping=data)

            # Set TTL
            if ttl or settings.REDIS_TTL:
                self._client.expire(key, ttl or settings.REDIS_TTL)

            return True

        except Exception as e:
            self.logger.error(f"Failed to set price data for {key}: {e}")
            return False

    def get_price_data(self, key: str) -> Optional[Dict[str, str]]:
        """Retrieve price data from Redis.

        Args:
            key: Redis key (e.g., 'bybit_spot:BTC')

        Returns:
            Dictionary containing price data or None if not found
        """
        try:
            data = self._client.hgetall(key)
            return data if data else None
        except Exception as e:
            self.logger.error(f"Failed to get price data for {key}: {e}")
            return None

    def delete_key(self, key: str) -> bool:
        """Delete a key from Redis.

        Args:
            key: Redis key to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            self._client.delete(key)
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete key {key}: {e}")
            return False

    def get_all_keys(self, pattern: str = "*") -> list:
        """Get all keys matching a pattern using SCAN (non-blocking).

        Args:
            pattern: Key pattern (e.g., 'bybit_*')

        Returns:
            List of matching keys
        """
        try:
            keys = []
            cursor = 0
            while True:
                cursor, batch = self._client.scan(cursor=cursor, match=pattern, count=100)
                keys.extend(batch)
                if cursor == 0:
                    break
            return keys
        except Exception as e:
            self.logger.error(f"Failed to get keys for pattern {pattern}: {e}")
            return []

    def get_orderbook(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve orderbook data from Redis and parse JSON fields.

        Args:
            key: Redis key (e.g., 'bybit_spot_ob:BTC')

        Returns:
            Dictionary containing parsed orderbook data or None if not found
        """
        try:
            data = self._client.hgetall(key)
            if not data:
                return None

            # Parse JSON fields
            result = {
                'bids': json.loads(data.get('bids', '[]')),
                'asks': json.loads(data.get('asks', '[]')),
                'spread': float(data['spread']) if data.get('spread') else None,
                'mid_price': float(data['mid_price']) if data.get('mid_price') else None,
                'update_id': int(data.get('update_id', 0)),
                'timestamp': data.get('timestamp', ''),
                'original_symbol': data.get('original_symbol', '')
            }
            return result
        except Exception as e:
            self.logger.error(f"Failed to get orderbook for {key}: {e}")
            return None

    def set_orderbook_data(
        self,
        key: str,
        bids: List,
        asks: List,
        spread: Optional[float] = None,
        mid_price: Optional[float] = None,
        update_id: int = 0,
        original_symbol: str = '',
        ttl: Optional[int] = None
    ) -> bool:
        """Store orderbook data in Redis as a hash.

        Args:
            key: Redis key (e.g., 'bybit_spot_ob:BTC')
            bids: List of [price, qty] bid levels
            asks: List of [price, qty] ask levels
            spread: Bid-ask spread
            mid_price: Mid price between best bid and ask
            update_id: Sequence number for updates
            original_symbol: Original symbol name
            ttl: Time to live in seconds (default from settings)

        Returns:
            True if successful, False otherwise
        """
        try:
            data = {
                'bids': json.dumps(bids),
                'asks': json.dumps(asks),
                'spread': str(spread) if spread is not None else '',
                'mid_price': str(mid_price) if mid_price is not None else '',
                'update_id': str(update_id),
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'original_symbol': original_symbol
            }

            self._client.hset(key, mapping=data)

            if ttl or settings.REDIS_TTL:
                self._client.expire(key, ttl or settings.REDIS_TTL)

            return True

        except Exception as e:
            self.logger.error(f"Failed to set orderbook data for {key}: {e}")
            return False

    def get_trades(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve trades data from Redis and parse JSON fields.

        Args:
            key: Redis key (e.g., 'bybit_spot_trades:BTC')

        Returns:
            Dictionary containing parsed trades data or None if not found
        """
        try:
            data = self._client.hgetall(key)
            if not data:
                return None

            # Parse JSON fields
            result = {
                'trades': json.loads(data.get('trades', '[]')),
                'count': int(data.get('count', 0)),
                'timestamp': data.get('timestamp', ''),
                'original_symbol': data.get('original_symbol', '')
            }
            return result
        except Exception as e:
            self.logger.error(f"Failed to get trades for {key}: {e}")
            return None

    def set_trades_data(
        self,
        key: str,
        trades: List[Dict[str, Any]],
        original_symbol: str = '',
        ttl: Optional[int] = None
    ) -> bool:
        """Store trades data in Redis as a hash.

        Args:
            key: Redis key (e.g., 'bybit_spot_trades:BTC')
            trades: List of trade dictionaries
            original_symbol: Original symbol name
            ttl: Time to live in seconds (default from settings)

        Returns:
            True if successful, False otherwise
        """
        try:
            data = {
                'trades': json.dumps(trades),
                'count': str(len(trades)),
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'original_symbol': original_symbol
            }

            self._client.hset(key, mapping=data)

            if ttl or settings.REDIS_TTL:
                self._client.expire(key, ttl or settings.REDIS_TTL)

            return True

        except Exception as e:
            self.logger.error(f"Failed to set trades data for {key}: {e}")
            return False

    def close(self):
        """Close Redis connection."""
        if self._client:
            self._client.close()
            self.logger.info("Redis connection closed")

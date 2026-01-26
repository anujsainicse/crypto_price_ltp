"""Standalone Redis tests that don't use the project's config.

These tests verify the actual Redis operations work correctly.
"""

import redis
import time
import sys
from datetime import datetime


def test_timestamp_format_standalone():
    """Test that timestamp format is correct (Unix timestamp string)."""
    print("=" * 60)
    print("TEST: Timestamp Format (Standalone)")
    print("=" * 60)

    # Connect to localhost Redis
    client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

    # Test the timestamp generation logic (from redis_client.py)
    timestamp = str(int(datetime.utcnow().timestamp()))

    print(f"Generated timestamp: {timestamp}")

    # Verify it's all digits
    assert timestamp.isdigit(), f"Timestamp should be all digits, got: {timestamp}"

    # Verify it's a valid Unix timestamp (use same time source for consistency)
    ts_int = int(timestamp)
    current_time = int(datetime.utcnow().timestamp())
    diff = abs(current_time - ts_int)
    assert diff < 5, f"Timestamp should be within 5s of current time, diff={diff}"

    # Store in Redis and retrieve
    test_key = "test:timestamp_standalone:BTC"
    client.hset(test_key, mapping={
        'ltp': '45000.50',
        'timestamp': timestamp,
        'original_symbol': 'BTCUSDT'
    })

    # Retrieve and verify
    data = client.hgetall(test_key)
    stored_timestamp = data.get('timestamp', '')

    assert stored_timestamp == timestamp, f"Stored timestamp should match: {stored_timestamp} vs {timestamp}"
    assert stored_timestamp.isdigit(), f"Stored timestamp should be all digits"

    # Cleanup
    client.delete(test_key)

    print("✓ Timestamp is correct Unix format")
    print(f"✓ Stored and retrieved: {stored_timestamp}")
    print()


def test_redis_scan_standalone():
    """Test SCAN-based key retrieval."""
    print("=" * 60)
    print("TEST: Redis SCAN (Standalone)")
    print("=" * 60)

    # Connect to localhost Redis
    client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

    # Create test keys
    test_prefix = "test:scan_standalone"
    for i in range(10):
        client.set(f"{test_prefix}:{i}", f"value{i}")

    print(f"Created 10 test keys with prefix: {test_prefix}")

    # Use SCAN (the correct approach)
    keys = []
    cursor = 0
    while True:
        cursor, batch = client.scan(cursor=cursor, match=f"{test_prefix}:*", count=100)
        keys.extend(batch)
        if cursor == 0:
            break

    print(f"Retrieved {len(keys)} keys using SCAN")

    assert len(keys) == 10, f"Expected 10 keys, got {len(keys)}"

    # Verify keys are strings
    for key in keys:
        assert isinstance(key, str), f"Key should be string, got {type(key)}"

    # Cleanup
    for i in range(10):
        client.delete(f"{test_prefix}:{i}")

    print("✓ SCAN correctly retrieves all keys")
    print("✓ Keys are returned as strings")
    print()


def test_aoe_integration_format():
    """Test that the timestamp format works with AOE expected format."""
    print("=" * 60)
    print("TEST: AOE Integration Format")
    print("=" * 60)

    # Connect to localhost Redis
    client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

    # Store price data in the format our service uses
    test_key = "test:aoe_format:BTC"
    timestamp = str(int(datetime.utcnow().timestamp()))

    client.hset(test_key, mapping={
        'ltp': '45000.50',
        'timestamp': timestamp,
        'original_symbol': 'BTCUSDT'
    })

    # Read it back the way AOE/Monitoring would read it
    data = client.hgetall(test_key)

    # AOE expects: int(data[b'timestamp']) or int(data['timestamp'])
    stored_timestamp = data['timestamp']

    # This is what AOE does - convert to int
    try:
        ts_int = int(stored_timestamp)
        age_seconds = int(datetime.utcnow().timestamp()) - ts_int
        print(f"Timestamp: {stored_timestamp}")
        print(f"As integer: {ts_int}")
        print(f"Age: {age_seconds} seconds")
        assert age_seconds < 60, "Timestamp should be recent"
    except ValueError as e:
        print(f"✗ Failed to parse timestamp as int: {e}")
        raise e

    # Cleanup
    client.delete(test_key)

    print("✓ Timestamp format compatible with AOE/Monitoring")
    print()


def run_all_tests():
    """Run all standalone Redis tests."""
    print("\n" + "=" * 60)
    print("STANDALONE REDIS TESTS")
    print("=" * 60 + "\n")

    # Check Redis connectivity first
    try:
        client = redis.Redis(host='localhost', port=6379, db=0)
        client.ping()
        print("✓ Redis is running on localhost:6379\n")
    except redis.ConnectionError:
        print("✗ Cannot connect to Redis on localhost:6379")
        print("  Make sure Redis is running: brew services start redis")
        return False

    tests = [
        ("Timestamp Format", test_timestamp_format_standalone),
        ("Redis SCAN", test_redis_scan_standalone),
        ("AOE Integration Format", test_aoe_integration_format),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
        except Exception as e:
            print(f"✗ {name} FAILED: {e}")
            failed += 1

    print("=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

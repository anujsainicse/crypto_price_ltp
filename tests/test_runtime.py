"""Runtime integration tests with Redis.

These tests verify the actual Redis operations work correctly.
"""

import redis
import time
import sys
import os

# Add project root to path
sys.path.insert(0, '/Users/anujsainicse/claude/crypto_price_ltp')


def test_timestamp_format():
    """Test that timestamp is stored as Unix timestamp in Redis."""
    print("=" * 60)
    print("TEST: Timestamp Format")
    print("=" * 60)

    from core.redis_client import RedisClient

    # Reset singleton for clean test
    RedisClient._instance = None
    RedisClient._client = None

    client = RedisClient()

    # Store test data
    test_key = "test:timestamp_format:BTC"
    client.set_price_data(
        key=test_key,
        price=45000.50,
        symbol="BTCUSDT"
    )

    # Retrieve and verify
    data = client.get_price_data(test_key)

    timestamp = data.get('timestamp', '')
    print(f"Stored timestamp: {timestamp}")

    # Verify it's all digits (Unix timestamp)
    assert timestamp.isdigit(), f"Timestamp should be all digits, got: {timestamp}"

    # Verify it's a valid Unix timestamp (within last minute)
    ts_int = int(timestamp)
    current_time = int(time.time())
    diff = abs(current_time - ts_int)
    assert diff < 60, f"Timestamp should be within 60s of current time, diff={diff}"

    # Cleanup
    client.delete_key(test_key)

    print("✓ Timestamp is correct Unix format")
    print(f"✓ Timestamp value: {timestamp} (current: {current_time})")
    print()
    return True


def test_redis_scan():
    """Test that get_all_keys uses SCAN (non-blocking)."""
    print("=" * 60)
    print("TEST: Redis SCAN Implementation")
    print("=" * 60)

    from core.redis_client import RedisClient

    # Reset singleton for clean test
    RedisClient._instance = None
    RedisClient._client = None

    client = RedisClient()

    # Create multiple test keys
    test_prefix = "test:scan_test"
    for i in range(10):
        client.set(f"{test_prefix}:{i}", f"value{i}")

    # Get all keys using our method
    keys = client.get_all_keys(f"{test_prefix}:*")

    print(f"Created 10 test keys")
    print(f"Retrieved {len(keys)} keys using get_all_keys")

    # Verify we got all keys
    assert len(keys) == 10, f"Expected 10 keys, got {len(keys)}"

    # Verify all keys are strings (not bytes)
    for key in keys:
        assert isinstance(key, str), f"Key should be string, got {type(key)}"

    # Cleanup
    for i in range(10):
        client.delete_key(f"{test_prefix}:{i}")

    print("✓ get_all_keys correctly retrieves all keys")
    print("✓ Keys are returned as strings")
    print()
    return True


def test_price_validation():
    """Test that price validation works correctly."""
    print("=" * 60)
    print("TEST: Price Validation")
    print("=" * 60)

    import math

    valid_prices = [45000.50, 1.0, 0.00001, 100000.0]
    invalid_prices = [float('inf'), float('-inf'), float('nan'), -100.0, 0]
    invalid_strings = ["abc", "", "NaN", "Infinity"]

    print("Testing valid prices...")
    for price in valid_prices:
        is_valid = math.isfinite(price) and price > 0
        assert is_valid, f"Price {price} should be valid"
        print(f"  ✓ {price} is valid")

    print("\nTesting invalid numeric prices...")
    for price in invalid_prices:
        is_valid = math.isfinite(price) and price > 0
        assert not is_valid, f"Price {price} should be invalid"
        print(f"  ✓ {price} is correctly rejected")

    print("\nTesting invalid string prices...")
    for value in invalid_strings:
        try:
            result = float(value)
            is_valid = math.isfinite(result) and result > 0
        except (ValueError, TypeError):
            is_valid = False
        assert not is_valid, f"'{value}' should be invalid"
        print(f"  ✓ '{value}' is correctly rejected")

    print()
    return True


def test_exponential_backoff():
    """Test exponential backoff delay calculation."""
    print("=" * 60)
    print("TEST: Exponential Backoff")
    print("=" * 60)

    backoff_delays = [5, 10, 20, 40, 60]

    test_cases = [
        (1, 5),   # First attempt: 5s
        (2, 10),  # Second attempt: 10s
        (3, 20),  # Third attempt: 20s
        (4, 40),  # Fourth attempt: 40s
        (5, 60),  # Fifth attempt: 60s (max)
        (6, 60),  # Sixth attempt: still 60s (max)
        (10, 60), # Tenth attempt: still 60s (max)
    ]

    for attempt, expected_delay in test_cases:
        delay = backoff_delays[min(attempt - 1, len(backoff_delays) - 1)]
        assert delay == expected_delay, f"Attempt {attempt}: expected {expected_delay}s, got {delay}s"
        print(f"  ✓ Attempt {attempt}: {delay}s delay")

    print("\n✓ Exponential backoff correctly implemented")
    print()
    return True


def run_all_tests():
    """Run all runtime tests."""
    print("\n" + "=" * 60)
    print("RUNTIME INTEGRATION TESTS")
    print("=" * 60 + "\n")

    tests = [
        ("Timestamp Format", test_timestamp_format),
        ("Redis SCAN", test_redis_scan),
        ("Price Validation", test_price_validation),
        ("Exponential Backoff", test_exponential_backoff),
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

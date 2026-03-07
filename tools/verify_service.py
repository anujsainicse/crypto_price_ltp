#!/usr/bin/env python3
"""Dry-run service verifier — no Redis required.

Connects to the exchange WebSocket, parses data using the real service code,
and prints what would have been written to Redis. Use this to verify that a
service's WebSocket connection and data parsing are working correctly without
needing Redis to be running.

Usage:
    python scripts/verify_service.py <service_name> [--count N] [--timeout T]

Examples:
    python scripts/verify_service.py binance_spot
    python scripts/verify_service.py bybit_spot --timeout 20
    python scripts/verify_service.py --list
"""

import argparse
import asyncio
import importlib
import sys
import time
import os

# Add project root to path so we can import service modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Service registry: maps CLI name → (module, class, exchange_key, service_key)
# ---------------------------------------------------------------------------
SERVICE_REGISTRY = {
    'binance_spot': {
        'module': 'services.binance_s',
        'class': 'BinanceSpotService',
        'exchange': 'binance',
        'service_key': 'spot',
    },
    'bybit_spot': {
        'module': 'services.bybit_s',
        'class': 'BybitSpotService',
        'exchange': 'bybit',
        'service_key': 'spot',
    },
    'bybit_spot_testnet': {
        'module': 'services.bybit_spot_testnet',
        'class': 'BybitSpotTestnetService',
        'exchange': 'bybit_spot_testnet',
        'service_key': 'spot',
    },
    'bybit_futures_orderbook': {
        'module': 'services.bybit_f',
        'class': 'BybitFuturesOrderbookService',
        'exchange': 'bybit',
        'service_key': 'futures_orderbook',
    },
    'bybit_options': {
        'module': 'services.bybit_o',
        'class': 'BybitOptionsService',
        'exchange': 'bybit',
        'service_key': 'options',
    },
    'delta_spot': {
        'module': 'services.delta_s',
        'class': 'DeltaSpotService',
        'exchange': 'delta',
        'service_key': 'spot',
    },
    'delta_futures_ltp': {
        'module': 'services.delta_f',
        'class': 'DeltaFuturesLTPService',
        'exchange': 'delta',
        'service_key': 'futures_ltp',
    },
    'delta_options': {
        'module': 'services.delta_o',
        'class': 'DeltaOptionsService',
        'exchange': 'delta',
        'service_key': 'options',
    },
    'hyperliquid_spot': {
        'module': 'services.hyperliquid_s',
        'class': 'HyperLiquidSpotService',
        'exchange': 'hyperliquid',
        'service_key': 'spot',
    },
    'hyperliquid_perpetual': {
        'module': 'services.hyperliquid_p',
        'class': 'HyperLiquidPerpetualService',
        'exchange': 'hyperliquid',
        'service_key': 'perpetual',
    },
    'coindcx_futures_rest': {
        'module': 'services.coindcx_f',
        'class': 'CoinDCXFuturesRESTService',
        'exchange': 'coindcx',
        'service_key': 'futures_rest',
    },
    # coindcx_spot uses Socket.IO — not supported in dry-run mode
}


# ---------------------------------------------------------------------------
# Mock Redis client — prints instead of writing
# ---------------------------------------------------------------------------
class DryRunRedisClient:
    """Intercepts all Redis writes and pretty-prints them to stdout."""

    def __init__(self, max_writes: int, stop_event: asyncio.Event):
        self.max_writes = max_writes
        self.stop_event = stop_event
        self.write_count = 0
        self.keys_touched: list = []

    def _record(self, key: str):
        self.write_count += 1
        if key not in self.keys_touched:
            self.keys_touched.append(key)
        if self.write_count >= self.max_writes:
            self.stop_event.set()

    def set_price_data(self, key, price, symbol, additional_data=None, ttl=60):
        print(f"\n  [LTP]  {key}")
        print(f"         ltp: {price}")
        print(f"         original_symbol: {symbol}")
        for field, value in (additional_data or {}).items():
            print(f"         {field}: {value}")
        self._record(key)
        return True

    def set_orderbook_data(self, key, bids, asks, spread, mid_price,
                           update_id=0, original_symbol='', ttl=60):
        best_bid = bids[0] if bids else 'N/A'
        best_ask = asks[0] if asks else 'N/A'
        print(f"\n  [OB]   {key}")
        print(f"         best_bid: {best_bid}  best_ask: {best_ask}")
        print(f"         spread: {spread:.6f}  mid_price: {mid_price:.4f}")
        print(f"         levels: {len(bids)} bids / {len(asks)} asks")
        self._record(key)
        return True

    def set_trades_data(self, key, trades, original_symbol='', ttl=60):
        latest = trades[-1] if trades else None
        print(f"\n  [TRD]  {key}")
        print(f"         count: {len(trades)}")
        if latest:
            print(f"         latest: price={latest.get('p')}  qty={latest.get('q')}  side={latest.get('s')}")
        self._record(key)
        return True

    def delete_key(self, key):
        print(f"\n  [DEL]  {key}  <-- crossed orderbook detected, key deleted")
        return True

    # Stubs for any other calls from base class / control interface
    def is_connected(self):
        return True

    def get_all_keys(self, pattern='*'):
        return []

    def ping(self):
        return True


# ---------------------------------------------------------------------------
# Main verification runner
# ---------------------------------------------------------------------------
async def run_verification(service_name: str, count: int, timeout: int):
    info = SERVICE_REGISTRY[service_name]

    # Load service config from exchanges.yaml (no Redis needed)
    from config.settings import Settings
    exchange_cfg = Settings.load_exchange_config(info['exchange'])
    service_cfg = exchange_cfg.get('services', {}).get(info['service_key'], {})

    if not service_cfg:
        print(f"ERROR: No config found for exchange='{info['exchange']}' key='{info['service_key']}'")
        sys.exit(1)

    ws_url = service_cfg.get('websocket_url', service_cfg.get('ltp_api_url', 'N/A'))
    symbols = service_cfg.get('symbols', [])

    print(f"\n{'='*62}")
    print(f"  DRY-RUN: {service_name}")
    print(f"  Class:   {info['class']}")
    print(f"  URL:     {ws_url}")
    print(f"  Symbols: {symbols}")
    print(f"  Stopping after {count} Redis writes or {timeout}s")
    print(f"{'='*62}")

    stop_event = asyncio.Event()
    dry_run_client = DryRunRedisClient(max_writes=count, stop_event=stop_event)

    # --- Inject mock BEFORE service instantiation ---
    # BaseService.__init__ calls self.redis_client = RedisClient() which pings Redis.
    # Patching at module level avoids the Redis connection entirely.
    import core.base_service as base_module
    original_redis_class = base_module.RedisClient
    base_module.RedisClient = lambda: dry_run_client
    try:
        mod = importlib.import_module(info['module'])
        ServiceClass = getattr(mod, info['class'])
        service = ServiceClass(service_cfg)
    finally:
        base_module.RedisClient = original_redis_class  # always restore

    start_time = time.monotonic()

    service_task = asyncio.create_task(service.start())
    stop_task = asyncio.create_task(stop_event.wait())

    done, pending = await asyncio.wait(
        [service_task, stop_task],
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Shutdown: stop reconnect loop and close WebSocket if open
    service.running = False
    if hasattr(service, 'websocket') and service.websocket:
        try:
            await service.websocket.close()
        except Exception:
            pass

    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    elapsed = time.monotonic() - start_time

    # --- Summary ---
    print(f"\n{'='*62}")
    if dry_run_client.write_count == 0:
        timed_out = service_task not in done and stop_task not in done
        reason = "timeout" if timed_out else "no data received"
        print(f"  RESULT: 0 writes in {elapsed:.1f}s ({reason})")
        print()
        print("  Possible causes:")
        print("    • WebSocket connection failed (check URL / internet)")
        print("    • Service connected but exchange sends no ticker data")
        print("    • orderbook_enabled/trades_enabled is false in config")
        print("    • Exchange response fields don't match expected names")
        print("    • symbols list is empty or wrong format in exchanges.yaml")
        print()
        print("  Try running with longer timeout: --timeout 60")
    else:
        status = "parsing is working correctly" if dry_run_client.write_count >= count else f"only {dry_run_client.write_count} writes before timeout"
        print(f"  RESULT: {dry_run_client.write_count} writes in {elapsed:.1f}s — {status}")
        print(f"  Keys:   {dry_run_client.keys_touched}")
    print(f"{'='*62}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Verify service data parsing without Redis.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        'service',
        nargs='?',
        help='Service name to test (use --list to see options)'
    )
    parser.add_argument(
        '--list', action='store_true',
        help='List all available services and exit'
    )
    parser.add_argument(
        '--count', type=int, default=10,
        help='Stop after N Redis writes (default: 10)'
    )
    parser.add_argument(
        '--timeout', type=int, default=30,
        help='Hard timeout in seconds (default: 30)'
    )
    args = parser.parse_args()

    if args.list:
        print("\nAvailable services:")
        for name, info in sorted(SERVICE_REGISTRY.items()):
            print(f"  {name:<30} ({info['class']})")
        print("\n  Note: coindcx_spot uses Socket.IO and is not supported in dry-run mode.")
        print()
        return

    if not args.service:
        parser.print_help()
        sys.exit(1)

    if args.service not in SERVICE_REGISTRY:
        print(f"Unknown service: '{args.service}'")
        print(f"Run with --list to see available services.")
        sys.exit(1)

    asyncio.run(run_verification(args.service, args.count, args.timeout))


if __name__ == '__main__':
    main()

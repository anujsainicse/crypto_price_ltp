"""Web Dashboard for Crypto Price LTP System."""

import asyncio
import os
import re
import tempfile
import uvicorn
import signal
import subprocess
import time
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict
from pathlib import Path

from core.control_interface import ControlInterface
from core.logging import setup_logger
from version import get_version, get_version_info


app = FastAPI(
    title="Crypto Price LTP Dashboard",
    description="Control panel for managing cryptocurrency price data collection services",
    version="1.0.0"
)

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize
control = ControlInterface()
logger = setup_logger('WebDashboard', log_file='web_dashboard.log')

# Mount static files
static_path = Path(__file__).parent / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


# ==================== Web Routes ====================

@app.get("/")
async def index():
    """Serve the dashboard homepage."""
    return FileResponse(
        str(static_path / "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


# ==================== Service Metadata ====================

def _get_services_info() -> Dict:
    """Service metadata - single source of truth for all 15 services."""
    return {
        'bybit_spot': {
            'name': 'Bybit Spot',
            'exchange': 'bybit',
            'type': 'spot',
            'redis_prefix': 'bybit_spot',
            'data_types': ['ltp', 'orderbook', 'trades']
        },
        'bybit_futures_orderbook': {
            'name': 'Bybit Futures Orderbook',
            'exchange': 'bybit',
            'type': 'futures',
            'redis_prefix': 'bybit_futures_ob',
            'data_types': ['orderbook'],
            'ob_is_base_key': True
        },
        'bybit_options': {
            'name': 'Bybit Options',
            'exchange': 'bybit',
            'type': 'options',
            'redis_prefix': 'bybit_options',
            'data_types': ['ltp']
        },
        'coindcx_spot': {
            'name': 'CoinDCX Spot',
            'exchange': 'coindcx',
            'type': 'spot',
            'redis_prefix': 'coindcx_spot',
            'data_types': ['orderbook', 'trades']
        },
        'coindcx_futures_rest': {
            'name': 'CoinDCX Futures REST',
            'exchange': 'coindcx',
            'type': 'futures',
            'redis_prefix': 'coindcx_futures',
            'data_types': ['ltp', 'orderbook', 'trades', 'funding']
        },
        'delta_spot': {
            'name': 'Delta Spot',
            'exchange': 'delta',
            'type': 'spot',
            'redis_prefix': 'delta_spot',
            'data_types': ['orderbook', 'trades']
        },
        'delta_futures_ltp': {
            'name': 'Delta Futures LTP',
            'exchange': 'delta',
            'type': 'futures',
            'redis_prefix': 'delta_futures',
            'data_types': ['ltp', 'orderbook', 'trades', 'funding']
        },
        'delta_options': {
            'name': 'Delta Options',
            'exchange': 'delta',
            'type': 'options',
            'redis_prefix': 'delta_options',
            'data_types': ['ltp', 'orderbook', 'trades']
        },
        'hyperliquid_spot': {
            'name': 'HyperLiquid Spot',
            'exchange': 'hyperliquid',
            'type': 'spot',
            'redis_prefix': 'hyperliquid_spot',
            'data_types': ['ltp', 'orderbook', 'trades']
        },
        'hyperliquid_perpetual': {
            'name': 'HyperLiquid Perpetual',
            'exchange': 'hyperliquid',
            'type': 'perpetual',
            'redis_prefix': 'hyperliquid_futures',
            'data_types': ['ltp', 'orderbook', 'trades']
        },
        'bybit_spot_testnet_spot': {
            'name': 'Bybit Spot TestNet',
            'exchange': 'bybit_spot_testnet',
            'type': 'spot',
            'redis_prefix': 'bybit_spot_testnet',
            'data_types': ['ltp', 'orderbook', 'trades']
        },
        'binance_spot': {
            'name': 'Binance Spot',
            'exchange': 'binance',
            'type': 'spot',
            'redis_prefix': 'binance_spot',
            'data_types': ['ltp', 'orderbook', 'trades']
        },
        'binance_futures': {
            'name': 'Binance Futures',
            'exchange': 'binance',
            'type': 'futures',
            'redis_prefix': 'binance_futures',
            'data_types': ['ltp', 'orderbook', 'trades', 'funding']
        },
        'binance_options': {
            'name': 'Binance Options',
            'exchange': 'binance',
            'type': 'options',
            'redis_prefix': 'binance_options',
            'data_types': ['ltp']
        },
        'polymarket_market': {
            'name': 'Polymarket Market',
            'exchange': 'polymarket',
            'type': 'market',
            'redis_prefix': 'polymarket',
            'data_types': ['ltp', 'orderbook']
        }
    }


# ==================== auto_start config (exchanges.yaml) ====================

CONFIG_YAML_PATH = Path(__file__).parent / "config" / "exchanges.yaml"

# Maps dashboard service_id -> (exchange_key, service_key) in exchanges.yaml.
# Mirrors the registration in manager._load_exchange_services().
SERVICE_YAML_PATH = {
    'bybit_spot': ('bybit', 'spot'),
    'bybit_futures_orderbook': ('bybit', 'futures_orderbook'),
    'bybit_options': ('bybit', 'options'),
    'coindcx_spot': ('coindcx', 'spot'),
    'coindcx_futures_rest': ('coindcx', 'futures_rest'),
    'delta_spot': ('delta', 'spot'),
    'delta_futures_ltp': ('delta', 'futures_ltp'),
    'delta_options': ('delta', 'options'),
    'hyperliquid_spot': ('hyperliquid', 'spot'),
    'hyperliquid_perpetual': ('hyperliquid', 'perpetual'),
    'bybit_spot_testnet_spot': ('bybit_spot_testnet', 'spot'),
    'binance_spot': ('binance', 'spot'),
    'binance_options': ('binance', 'options'),
    'binance_futures': ('binance', 'futures'),
    'polymarket_market': ('polymarket', 'market'),
}


def _read_auto_start_flags() -> Dict[str, bool]:
    """Read the persisted auto_start flag for every known service from exchanges.yaml."""
    import yaml
    try:
        data = yaml.safe_load(CONFIG_YAML_PATH.read_text()) or {}
    except Exception as e:
        logger.error(f"Failed to read auto_start flags from {CONFIG_YAML_PATH}: {e}")
        return {}

    flags: Dict[str, bool] = {}
    for service_id, (exch, svc) in SERVICE_YAML_PATH.items():
        try:
            svc_cfg = data.get(exch, {}).get('services', {}).get(svc, {})
            flags[service_id] = bool(svc_cfg.get('auto_start', False))
        except AttributeError:
            flags[service_id] = False
    return flags


def _set_auto_start_in_yaml(exchange_key: str, service_key: str, value: bool) -> bool:
    """Surgically set auto_start for one service block in exchanges.yaml.

    Edits only the single `auto_start:` line (or inserts one) so all comments and
    formatting are preserved. The file is rewritten atomically via a temp file +
    os.replace so a concurrent /api/status read never sees a half-written file.
    Returns True if the value was written, False if the block was not found.
    """
    val_str = "true" if value else "false"
    lines = CONFIG_YAML_PATH.read_text().splitlines(keepends=True)
    n = len(lines)

    def is_top_key(line: str) -> bool:
        return bool(line.strip()) and not line[0].isspace() and not line.lstrip().startswith('#')

    # Locate the exchange block (top-level key at indent 0).
    exch_re = re.compile(rf"^{re.escape(exchange_key)}:\s*(#.*)?$")
    exch_start = next((i for i in range(n) if exch_re.match(lines[i])), None)
    if exch_start is None:
        return False
    exch_end = next((j for j in range(exch_start + 1, n) if is_top_key(lines[j])), n)

    # Locate the service block (key at indent 4 under `services:`).
    svc_re = re.compile(rf"^    {re.escape(service_key)}:\s*(#.*)?$")
    svc_start = next((k for k in range(exch_start, exch_end) if svc_re.match(lines[k])), None)
    if svc_start is None:
        return False

    svc_end = exch_end
    for k in range(svc_start + 1, exch_end):
        line = lines[k]
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip(' '))
        if indent <= 4:  # next sibling service or dedent
            svc_end = k
            break

    # Replace existing auto_start line if present.
    as_re = re.compile(r"^(\s*)auto_start:\s*\S+.*$")
    target = next((k for k in range(svc_start, svc_end) if as_re.match(lines[k])), None)
    if target is not None:
        indent = as_re.match(lines[target]).group(1)
        lines[target] = f"{indent}auto_start: {val_str}\n"
    else:
        # Insert after the `enabled:` line, else right after the service key line.
        enabled_re = re.compile(r"^\s*enabled:\s*\S+.*$")
        insert_at = next((k + 1 for k in range(svc_start, svc_end) if enabled_re.match(lines[k])), svc_start + 1)
        lines.insert(insert_at, f"      auto_start: {val_str}\n")

    fd, tmp_path = tempfile.mkstemp(dir=str(CONFIG_YAML_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("".join(lines))
        os.replace(tmp_path, CONFIG_YAML_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return True


class AutoStartRequest(BaseModel):
    enabled: bool


# ==================== API Endpoints ====================

@app.get("/health")
async def health_check():
    """Health check endpoint for deployment verification."""
    try:
        # Run sync Redis ping in thread pool to avoid blocking the event loop
        redis_status = await asyncio.to_thread(control.is_redis_connected)

        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "version": get_version(),
                "version_info": get_version_info(),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "redis_connected": redis_status,
            }
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "version": get_version(),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "error": str(e)
            }
        )

@app.get("/api/status")
async def get_status() -> Dict:
    """Get status of all services."""
    try:
        # Get service statuses
        statuses = control.get_all_services_status()

        # Get per-type data counts breakdown
        data_breakdown = control.get_all_data_counts_breakdown()

        # Service metadata from single source of truth
        services_info = _get_services_info()

        # Persisted auto_start flags (from exchanges.yaml). The file is tiny, so a
        # synchronous read is negligible and avoids asyncio.to_thread (Python 3.9+).
        auto_start_flags = _read_auto_start_flags()

        # Build response
        services = []
        for service_id, info in services_info.items():
            status_data = statuses.get(service_id, {})
            prefix = info['redis_prefix']
            counts = data_breakdown.get(prefix, {'ltp': 0, 'orderbook': 0, 'trades': 0})

            # For bybit_futures_orderbook, base keys ARE orderbook data
            if info.get('ob_is_base_key'):
                data_counts_detail = {
                    'ltp': 0,
                    'orderbook': counts['ltp'],  # base keys are orderbook
                    'trades': 0,
                }
            else:
                data_counts_detail = {
                    'ltp': counts['ltp'],
                    'orderbook': counts['orderbook'],
                    'trades': counts['trades'],
                }

            # Funding is a field within LTP hash, active when LTP data exists
            if 'funding' in info.get('data_types', []):
                data_counts_detail['funding'] = data_counts_detail['ltp']

            total = sum(data_counts_detail.values())

            service = {
                'id': service_id,
                'name': info['name'],
                'exchange': info['exchange'],
                'type': info['type'],
                'status': status_data.get('status', 'unknown'),
                'last_update': status_data.get('last_update'),
                'data_count': total,
                'data_counts': data_counts_detail,
                'data_types': info.get('data_types', []),
                'auto_start': auto_start_flags.get(service_id, False)
            }
            services.append(service)

        # Group by exchange
        exchanges = {}
        for service in services:
            exchange = service['exchange']
            if exchange not in exchanges:
                exchanges[exchange] = {
                    'name': exchange.replace('_', ' ').title(),
                    'services': [],
                    'total_data_points': 0
                }
            exchanges[exchange]['services'].append(service)
            exchanges[exchange]['total_data_points'] += service['data_count']

        return {
            'success': True,
            'exchanges': exchanges,
            'services': services,
            'total_services': len(services),
            'running_services': len([s for s in services if s['status'] == 'running'])
        }

    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/service/{service_id}/start")
async def start_service(service_id: str) -> Dict:
    """Start a service."""
    try:
        logger.info(f"Received start command for service: {service_id}")
        success = control.send_start_command(service_id)

        if success:
            return {
                'success': True,
                'message': f'Start command sent for {service_id}',
                'service_id': service_id
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to send start command")

    except Exception as e:
        logger.error(f"Error starting service {service_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/service/{service_id}/stop")
async def stop_service(service_id: str) -> Dict:
    """Stop a service."""
    try:
        logger.info(f"Received stop command for service: {service_id}")
        success = control.send_stop_command(service_id)

        if success:
            return {
                'success': True,
                'message': f'Stop command sent for {service_id}',
                'service_id': service_id
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to send stop command")

    except Exception as e:
        logger.error(f"Error stopping service {service_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/service/{service_id}/auto-start")
async def set_service_auto_start(service_id: str, req: AutoStartRequest) -> Dict:
    """Persist a service's auto_start flag and immediately start/stop it to match."""
    yaml_path = SERVICE_YAML_PATH.get(service_id)
    if not yaml_path:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")

    exchange_key, service_key = yaml_path
    try:
        written = _set_auto_start_in_yaml(exchange_key, service_key, req.enabled)
    except Exception as e:
        logger.error(f"Error writing auto_start for {service_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update config: {e}")

    if not written:
        raise HTTPException(
            status_code=500,
            detail=f"Could not locate {exchange_key}.services.{service_key} in config"
        )

    # Act now: bring the running state in line with the new preference.
    if req.enabled:
        control.send_start_command(service_id)
    else:
        control.send_stop_command(service_id)

    logger.info(f"Set auto_start={req.enabled} for {service_id} and sent "
                f"{'start' if req.enabled else 'stop'} command")
    return {
        'success': True,
        'service_id': service_id,
        'auto_start': req.enabled
    }


@app.post("/api/services/start-all")
async def start_all_services() -> Dict:
    """Start all services."""
    results = {}
    for service_id in _get_services_info().keys():
        results[service_id] = control.send_start_command(service_id)
    return {'success': True, 'message': f'Start commands sent for {len(results)} services', 'results': results}


@app.post("/api/services/stop-all")
async def stop_all_services() -> Dict:
    """Stop all services."""
    results = {}
    for service_id in _get_services_info().keys():
        results[service_id] = control.send_stop_command(service_id)
    return {'success': True, 'message': f'Stop commands sent for {len(results)} services', 'results': results}


@app.post("/api/exchange/{exchange_id}/start")
async def start_exchange_services(exchange_id: str) -> Dict:
    """Start all services for an exchange."""
    matching = {sid: info for sid, info in _get_services_info().items() if info['exchange'] == exchange_id}
    if not matching:
        raise HTTPException(status_code=404, detail=f"Exchange '{exchange_id}' not found")
    results = {}
    for service_id in matching.keys():
        results[service_id] = control.send_start_command(service_id)
    return {'success': True, 'results': results}


@app.post("/api/exchange/{exchange_id}/stop")
async def stop_exchange_services(exchange_id: str) -> Dict:
    """Stop all services for an exchange."""
    matching = {sid: info for sid, info in _get_services_info().items() if info['exchange'] == exchange_id}
    if not matching:
        raise HTTPException(status_code=404, detail=f"Exchange '{exchange_id}' not found")
    results = {}
    for service_id in matching.keys():
        results[service_id] = control.send_stop_command(service_id)
    return {'success': True, 'results': results}


@app.get("/api/health")
async def api_health_check() -> Dict:
    """Health check endpoint."""
    return {
        'status': 'healthy',
        'service': 'crypto_price_ltp_dashboard'
    }


# ==================== Main ====================

def kill_port_process(port: int):
    """Kill process using the specified port, only if it's a dashboard process.

    Args:
        port: Port number to free up
    """
    try:
        # Use lsof to find process using the port with command info
        result = subprocess.run(
            ['lsof', '-ti', f':{port}'],
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                if not pid:
                    continue

                # Verify process name before killing
                ps_result = subprocess.run(
                    ['ps', '-p', pid, '-o', 'comm='],
                    capture_output=True,
                    text=True
                )

                if ps_result.returncode == 0:
                    process_name = ps_result.stdout.strip().lower()
                    # Only kill if it's a Python/uvicorn process (likely our dashboard)
                    if 'python' in process_name or 'uvicorn' in process_name:
                        logger.info(f"Killing existing dashboard process on port {port} (PID: {pid}, Process: {process_name})")
                        try:
                            subprocess.run(['kill', '-9', pid], check=False)
                        except Exception as e:
                            logger.warning(f"Failed to kill PID {pid}: {e}")
                    else:
                        logger.warning(
                            f"Port {port} is used by non-dashboard process '{process_name}' (PID: {pid}). "
                            f"Not killing. Please free the port manually."
                        )
                        return  # Don't proceed if we can't free the port safely

            # Wait for port to be freed
            time.sleep(1)
            logger.info(f"Port {port} freed successfully")
        else:
            logger.info(f"Port {port} is available")

    except FileNotFoundError:
        # lsof not available, skip port checking
        logger.warning("lsof command not found, skipping port conflict check")
    except Exception as e:
        logger.error(f"Error checking port {port}: {e}")

def main():
    """Run the web dashboard."""
    PORT = 8080

    logger.info("=" * 80)
    logger.info("Starting Crypto Price LTP Web Dashboard")
    logger.info("=" * 80)

    # Check and free up port if needed
    kill_port_process(PORT)

    logger.info(f"Dashboard URL: http://localhost:{PORT}")
    logger.info(f"API Docs: http://localhost:{PORT}/docs")
    logger.info("=" * 80)

    uvicorn.run(
        "web_dashboard:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
        reload=False
    )


if __name__ == "__main__":
    main()

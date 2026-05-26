"""Web Dashboard for Crypto Price LTP System."""

import asyncio
import uvicorn
import signal
import subprocess
import time
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
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
                'data_types': info.get('data_types', [])
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

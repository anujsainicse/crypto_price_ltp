"""Web Dashboard for Crypto Price LTP System."""

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
    return FileResponse(str(static_path / "index.html"))


# ==================== API Endpoints ====================

@app.get("/health")
async def health_check():
    """Health check endpoint for deployment verification."""
    try:
        # Check Redis connectivity
        redis_status = control.is_redis_connected()

        # Get service statuses
        services = control.get_all_services_status()
        active_services = sum(1 for s in services.values() if s.get('status') == 'running')

        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "version": get_version(),
                "version_info": get_version_info(),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "redis_connected": redis_status,
                "active_services": active_services,
                "total_services": len(services)
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

        # Get data counts
        data_counts = control.get_all_data_counts()

        # Define service metadata
        services_info = {
            'bybit_spot': {
                'name': 'Bybit Spot',
                'exchange': 'bybit',
                'type': 'spot',
                'redis_prefix': 'bybit_spot'
            },
            'coindcx_futures_ltp': {
                'name': 'CoinDCX Futures LTP',
                'exchange': 'coindcx',
                'type': 'futures',
                'redis_prefix': 'coindcx_futures'
            },
            'coindcx_funding_rate': {
                'name': 'CoinDCX Funding Rate',
                'exchange': 'coindcx',
                'type': 'funding',
                'redis_prefix': 'coindcx_futures'
            },
            'delta_futures_ltp': {
                'name': 'Delta Futures LTP',
                'exchange': 'delta',
                'type': 'futures',
                'redis_prefix': 'delta_futures'
            },
            'delta_options': {
                'name': 'Delta Options',
                'exchange': 'delta',
                'type': 'options',
                'redis_prefix': 'delta_options'
            },
            'hyperliquid_spot': {
                'name': 'HyperLiquid Spot',
                'exchange': 'hyperliquid',
                'type': 'spot',
                'redis_prefix': 'hyperliquid_spot'
            },
            'hyperliquid_perpetual': {
                'name': 'HyperLiquid Perpetual',
                'exchange': 'hyperliquid',
                'type': 'perpetual',
                'redis_prefix': 'hyperliquid_perp'
            },
            'bybit_spot_testnet_spot': {
                'name': 'Bybit Spot TestNet',
                'exchange': 'bybit_spot_testnet',
                'type': 'spot',
                'redis_prefix': 'bybit_spot_testnet'
            }
        }

        # Build response
        services = []
        for service_id, info in services_info.items():
            status_data = statuses.get(service_id, {})
            service = {
                'id': service_id,
                'name': info['name'],
                'exchange': info['exchange'],
                'type': info['type'],
                'status': status_data.get('status', 'unknown'),
                'last_update': status_data.get('last_update'),
                'data_count': data_counts.get(info['redis_prefix'], 0)
            }
            services.append(service)

        # Group by exchange
        exchanges = {}
        for service in services:
            exchange = service['exchange']
            if exchange not in exchanges:
                exchanges[exchange] = {
                    'name': exchange.title(),
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


@app.get("/api/health")
async def health_check() -> Dict:
    """Health check endpoint."""
    return {
        'status': 'healthy',
        'service': 'crypto_price_ltp_dashboard'
    }


# ==================== Main ====================

def kill_port_process(port: int):
    """Kill any process using the specified port.

    Args:
        port: Port number to free up
    """
    try:
        # Use lsof to find process using the port
        result = subprocess.run(
            ['lsof', '-ti', f':{port}'],
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                if pid:
                    logger.info(f"Killing existing process on port {port} (PID: {pid})")
                    try:
                        subprocess.run(['kill', '-9', pid], check=False)
                    except Exception as e:
                        logger.warning(f"Failed to kill PID {pid}: {e}")

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

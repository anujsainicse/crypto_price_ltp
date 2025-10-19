"""Web Dashboard for Crypto Price LTP System."""

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict
from pathlib import Path

from core.control_interface import ControlInterface
from core.logging import setup_logger


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

def main():
    """Run the web dashboard."""
    logger.info("=" * 80)
    logger.info("Starting Crypto Price LTP Web Dashboard")
    logger.info("=" * 80)
    logger.info("Dashboard URL: http://localhost:8080")
    logger.info("API Docs: http://localhost:8080/docs")
    logger.info("=" * 80)

    uvicorn.run(
        "web_dashboard:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        reload=False
    )


if __name__ == "__main__":
    main()

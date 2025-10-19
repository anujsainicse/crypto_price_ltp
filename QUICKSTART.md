# Quick Start Guide - Price LTP

## Installation (1 minute)

```bash
cd price_ltp
pip install -r requirements.txt
cp .env.example .env
```

## Start System (1 command)

```bash
python manager.py
```

## Verify Data

```bash
# Check BTC price from Bybit
redis-cli HGETALL bybit_spot:BTC

# Check all available data
redis-cli KEYS "*"
```

## Expected Output

```
2025-10-18 22:13:09 | ServiceManager | INFO | ✓ Redis connection successful
2025-10-18 22:13:09 | ServiceManager | INFO | Initialized 3 services
2025-10-18 22:13:09 | ServiceManager | INFO | 1. Bybit-Spot
2025-10-18 22:13:09 | ServiceManager | INFO | 2. CoinDCX-Futures-LTP
2025-10-18 22:13:09 | ServiceManager | INFO | 3. CoinDCX-Funding-Rate
2025-10-18 22:13:10 | Bybit-Spot | INFO | WebSocket connected successfully
2025-10-18 22:13:10 | Bybit-Spot | INFO | Subscribed to tickers.BTCUSDT
```

## Access Data in Python

```python
import redis

r = redis.Redis(decode_responses=True)

# Get BTC price
btc_data = r.hgetall('bybit_spot:BTC')
print(f"BTC: ${btc_data['ltp']}")
print(f"24h Change: {float(btc_data['price_change_percent'])*100:.2f}%")
```

## Stop System

Press `Ctrl+C` in the terminal running `manager.py`

## Configuration

Edit `config/exchanges.yaml` to:
- Add/remove exchanges
- Enable/disable services
- Add/remove trading pairs
- Adjust update intervals

## Logs

Check logs in `logs/` directory:
- `service_manager.log` - Main system logs
- `bybit-spot.log` - Bybit service logs
- `coindcx-futures-ltp.log` - CoinDCX LTP logs
- `coindcx-funding-rate.log` - CoinDCX funding rate logs

## Run Individual Services

```bash
# Run only Bybit
python -m services.bybit.spot_service

# Run only CoinDCX LTP
python -m services.coindcx.futures_ltp_service

# Run only CoinDCX Funding Rate
python -m services.coindcx.funding_rate_service
```

## Troubleshooting

### Redis not running
```bash
redis-server
```

### Check service status
```bash
# Check logs
tail -f logs/service_manager.log

# Check Redis data
redis-cli KEYS "*"
```

### WebSocket connection issues
- Check internet connection
- Check exchange status
- Review service logs

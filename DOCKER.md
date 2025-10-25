# Docker Deployment Guide

This guide explains how to run the Crypto Price LTP system using Docker.

## Prerequisites

- Docker (version 20.10+)
- Docker Compose (version 2.0+)

## Quick Start

### 1. Build and Start Services

```bash
# Build and start all services (Redis + Application)
docker-compose up -d

# View logs
docker-compose logs -f

# View logs for specific service
docker-compose logs -f app
docker-compose logs -f redis
```

### 2. Access the Dashboard

Open your browser and navigate to:
- **Dashboard**: http://localhost:8080
- **API Docs**: http://localhost:8080/docs
- **Health Check**: http://localhost:8080/api/health

### 3. Stop Services

```bash
# Stop all services
docker-compose down

# Stop and remove volumes (deletes Redis data)
docker-compose down -v
```

## Configuration

### Environment Variables

The application uses environment variables for configuration. You can modify them in:

1. **docker-compose.yml** - Edit the `environment` section under the `app` service
2. **.env.docker** - Copy to `.env` and Docker Compose will automatically load it

```bash
cp .env.docker .env
# Edit .env with your preferences
```

Key environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| REDIS_HOST | redis | Redis hostname (use 'redis' for Docker) |
| REDIS_PORT | 6379 | Redis port |
| REDIS_PASSWORD | (empty) | Redis password (if required) |
| REDIS_DB | 0 | Redis database number |
| LOG_LEVEL | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |

### Exchange Configuration

To customize which exchanges and symbols to monitor, edit:

```bash
# Edit the configuration file
vi config/exchanges.yaml
```

After modifying, restart the services:

```bash
docker-compose restart app
```

## Docker Commands

### View Running Containers

```bash
docker-compose ps
```

### View Real-time Logs

```bash
# All services
docker-compose logs -f

# Only application
docker-compose logs -f app

# Last 100 lines
docker-compose logs --tail=100 app
```

### Restart Services

```bash
# Restart all services
docker-compose restart

# Restart only the app
docker-compose restart app
```

### Execute Commands in Container

```bash
# Open shell in the app container
docker-compose exec app bash

# Check Redis connection from app container
docker-compose exec app python -c "import redis; r = redis.Redis(host='redis'); print(r.ping())"

# View logs directory
docker-compose exec app ls -la logs/
```

### Access Redis CLI

```bash
# Connect to Redis CLI
docker-compose exec redis redis-cli

# Inside Redis CLI:
# KEYS *                    # List all keys
# HGETALL bybit_spot:BTCUSDT  # Get BTC spot price
# MONITOR                   # Watch real-time operations
```

### View Container Stats

```bash
# Real-time resource usage
docker stats crypto_ltp_app crypto_ltp_redis
```

## Data Persistence

### Redis Data

Redis data is persisted in a Docker volume named `redis_data`. This ensures data survives container restarts.

```bash
# List volumes
docker volume ls

# Inspect the Redis volume
docker volume inspect crypto_price_ltp_redis_data

# Backup Redis data
docker run --rm -v crypto_price_ltp_redis_data:/data -v $(pwd):/backup alpine tar czf /backup/redis-backup.tar.gz /data
```

### Application Logs

Logs are mounted from the host `./logs` directory, so they persist even when containers are removed.

```bash
# View logs on host
ls -la logs/
tail -f logs/service_manager.log
tail -f logs/web_dashboard.log
```

## Troubleshooting

### Container Won't Start

```bash
# Check container status
docker-compose ps

# View detailed logs
docker-compose logs app

# Check if ports are already in use
lsof -i :8080  # Dashboard port
lsof -i :6379  # Redis port
```

### Redis Connection Issues

```bash
# Test Redis connectivity
docker-compose exec app python -c "import redis; r = redis.Redis(host='redis', port=6379); print('Connected!' if r.ping() else 'Failed')"

# Check Redis logs
docker-compose logs redis

# Verify Redis is healthy
docker-compose exec redis redis-cli ping
```

### Application Not Collecting Data

```bash
# Check application logs
docker-compose logs -f app

# Verify services are running inside container
docker-compose exec app ps aux

# Check network connectivity from container
docker-compose exec app ping -c 3 google.com
```

### Dashboard Not Loading

```bash
# Check if web dashboard process is running
docker-compose exec app ps aux | grep web_dashboard

# Check port mapping
docker-compose port app 8080

# Test dashboard from inside container
docker-compose exec app curl -s http://localhost:8080/api/health
```

### Rebuild After Code Changes

```bash
# Rebuild and restart
docker-compose up -d --build

# Force rebuild without cache
docker-compose build --no-cache
docker-compose up -d
```

## Production Deployment

### Using Custom Network

```yaml
# In docker-compose.yml, modify networks section:
networks:
  crypto_network:
    external: true
    name: your_existing_network
```

### Adding Authentication

For production, add a reverse proxy with authentication:

```yaml
# Add nginx service to docker-compose.yml
nginx:
  image: nginx:alpine
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ./nginx.conf:/etc/nginx/nginx.conf
    - ./ssl:/etc/nginx/ssl
  depends_on:
    - app
  networks:
    - crypto_network
```

### Resource Limits

Add resource constraints in docker-compose.yml:

```yaml
services:
  app:
    # ... existing config ...
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 1G
        reservations:
          cpus: '0.5'
          memory: 512M
```

### Health Checks

The compose file includes health checks for Redis. Monitor health status:

```bash
docker-compose ps
```

### Automatic Restart Policies

Both services are configured with `restart: unless-stopped`, which means:
- Containers restart automatically on failure
- Containers restart after host reboot
- Containers stay stopped if manually stopped

## Scaling (Future)

If you need to scale services in the future:

```bash
# Scale the app service to 3 instances
docker-compose up -d --scale app=3
```

Note: Currently, the application runs both manager and dashboard in the same container. For true scaling, you'd need to separate these into different services.

## Monitoring

### View Resource Usage

```bash
# Real-time stats
docker stats

# Container inspection
docker inspect crypto_ltp_app
docker inspect crypto_ltp_redis
```

### Export Metrics

```bash
# Export container logs
docker-compose logs --no-color > system_logs.txt

# Export specific time range
docker-compose logs --since 2h app > recent_app_logs.txt
```

## Cleanup

### Remove Stopped Containers

```bash
docker-compose down
```

### Remove Everything (Including Volumes)

```bash
# WARNING: This deletes all Redis data
docker-compose down -v

# Remove images as well
docker-compose down -v --rmi all
```

### Prune Unused Docker Resources

```bash
# Remove unused containers, networks, images
docker system prune -a

# Remove unused volumes
docker volume prune
```

## Backup and Restore

### Backup

```bash
# Backup Redis data
docker-compose exec redis redis-cli BGSAVE

# Copy Redis dump
docker cp crypto_ltp_redis:/data/dump.rdb ./backup/

# Backup logs
tar czf backup/logs-$(date +%Y%m%d).tar.gz logs/
```

### Restore

```bash
# Stop services
docker-compose down

# Restore Redis dump
docker run --rm -v crypto_price_ltp_redis_data:/data -v $(pwd)/backup:/backup alpine sh -c "cp /backup/dump.rdb /data/"

# Start services
docker-compose up -d
```

## Development

### Live Code Updates

For development, mount the code directory as a volume:

```yaml
services:
  app:
    volumes:
      - .:/app  # Mount current directory
      - ./logs:/app/logs
```

Then restart the container after code changes:

```bash
docker-compose restart app
```

### Debug Mode

Run with debug logging:

```bash
# Set LOG_LEVEL to DEBUG in docker-compose.yml
docker-compose up -d
docker-compose logs -f app
```

---

## Summary of Commands

```bash
# Start system
docker-compose up -d

# View logs
docker-compose logs -f

# Stop system
docker-compose down

# Restart after changes
docker-compose up -d --build

# Access Redis
docker-compose exec redis redis-cli

# Access application shell
docker-compose exec app bash

# View status
docker-compose ps

# View resource usage
docker stats
```

For more information, see the main [README.md](README.md).

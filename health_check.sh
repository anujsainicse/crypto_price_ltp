#!/bin/bash
# Crypto Price LTP - Health Check Script
# Usage: ./health_check.sh
#
# This script checks the health of all components:
# - Docker containers
# - Redis connection
# - Dashboard API
# - Data collection
# - Resource usage
# - Disk usage

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Load Redis password from .env.production.local if exists
if [ -f ".env.production.local" ]; then
    export $(grep -v '^#' .env.production.local | xargs)
elif [ -f ".env.docker" ]; then
    export $(grep -v '^#' .env.docker | xargs)
fi

# Print functions
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_info() { echo -e "${BLUE}ℹ${NC} $1"; }
print_header() { echo -e "${BLUE}$1${NC}"; }

echo "=========================================="
echo "  Crypto Price LTP - Health Check"
echo "=========================================="
echo ""

# 1. Check Docker Containers
print_header "1. Docker Containers Status:"
if docker ps --filter name=crypto_ltp --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -q "crypto_ltp"; then
    docker ps --filter name=crypto_ltp --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

    # Count running containers
    RUNNING=$(docker ps --filter name=crypto_ltp --filter status=running | grep -c crypto_ltp || true)
    if [ "$RUNNING" -eq 2 ]; then
        print_success "All containers running (2/2)"
    else
        print_warning "Expected 2 containers, found $RUNNING running"
    fi
else
    print_error "No containers found! System may not be running."
    echo ""
    echo "To start the system:"
    echo "  docker-compose --env-file .env.production.local up -d"
    exit 1
fi
echo ""

# 2. Check Redis Connection
print_header "2. Redis Connection:"
if [ -z "$REDIS_PASSWORD" ]; then
    # Try without password
    if docker exec crypto_ltp_redis redis-cli ping > /dev/null 2>&1; then
        print_success "Redis: Connected (no password)"
    else
        print_error "Redis: Connection failed"
    fi
else
    # Try with password
    if docker exec crypto_ltp_redis redis-cli -a "$REDIS_PASSWORD" ping > /dev/null 2>&1; then
        print_success "Redis: Connected (authenticated)"
    else
        print_error "Redis: Authentication failed"
        print_info "Check REDIS_PASSWORD in .env.production.local"
    fi
fi
echo ""

# 3. Check Dashboard API
print_header "3. Dashboard API Health:"
if curl -s http://localhost:8080/api/health | grep -q "healthy"; then
    print_success "Dashboard: Healthy and responding"

    # Get version info if available
    VERSION=$(curl -s http://localhost:8080/api/health | grep -o '"service":"[^"]*"' | cut -d'"' -f4)
    if [ -n "$VERSION" ]; then
        print_info "Service: $VERSION"
    fi
else
    print_error "Dashboard: Not responding or unhealthy"
    print_info "Check logs: docker compose logs app"
fi
echo ""

# 4. Check Data Collection
print_header "4. Data Collection Status:"

if [ -z "$REDIS_PASSWORD" ]; then
    DATA_KEYS=$(docker exec crypto_ltp_redis redis-cli KEYS "*:*" 2>/dev/null | wc -l)
else
    DATA_KEYS=$(docker exec crypto_ltp_redis redis-cli -a "$REDIS_PASSWORD" KEYS "*:*" 2>/dev/null | wc -l)
fi

if [ "$DATA_KEYS" -gt 0 ]; then
    print_success "Total data keys in Redis: $DATA_KEYS"

    # Check each exchange
    for prefix in "bybit_spot" "coindcx_futures" "delta_futures" "delta_options"; do
        if [ -z "$REDIS_PASSWORD" ]; then
            COUNT=$(docker exec crypto_ltp_redis redis-cli KEYS "$prefix:*" 2>/dev/null | wc -l)
        else
            COUNT=$(docker exec crypto_ltp_redis redis-cli -a "$REDIS_PASSWORD" KEYS "$prefix:*" 2>/dev/null | wc -l)
        fi

        if [ "$COUNT" -gt 0 ]; then
            print_success "  └─ $prefix: $COUNT symbols"
        else
            print_warning "  └─ $prefix: 0 symbols (service may be stopped)"
        fi
    done
else
    print_warning "No data found in Redis"
    print_info "Services may still be starting up (wait 1-2 minutes)"
    print_info "Or check if services are enabled in config/exchanges.yaml"
fi
echo ""

# 5. Check Service Status via API
print_header "5. Service Status:"
if curl -s http://localhost:8080/api/status > /dev/null 2>&1; then
    STATUS_JSON=$(curl -s http://localhost:8080/api/status)

    # Parse and display service statuses
    if command -v jq > /dev/null 2>&1; then
        # If jq is available, use it for better formatting
        echo "$STATUS_JSON" | jq -r '.services[] | "\(.name): \(.status)"' | while read line; do
            if echo "$line" | grep -q "running"; then
                print_success "  $line"
            elif echo "$line" | grep -q "stopped"; then
                print_warning "  $line"
            else
                print_info "  $line"
            fi
        done
    else
        # Fallback without jq
        RUNNING_COUNT=$(echo "$STATUS_JSON" | grep -o '"status":"running"' | wc -l)
        STOPPED_COUNT=$(echo "$STATUS_JSON" | grep -o '"status":"stopped"' | wc -l)
        print_info "  Running: $RUNNING_COUNT | Stopped: $STOPPED_COUNT"
        print_info "  Install 'jq' for detailed service list: sudo apt install jq"
    fi
else
    print_warning "Could not fetch service status from API"
fi
echo ""

# 6. Check Disk Usage
print_header "6. Disk Usage:"

# Check logs directory
if [ -d "logs" ]; then
    LOG_SIZE=$(du -sh logs 2>/dev/null | cut -f1)
    LOG_SIZE_MB=$(du -sm logs 2>/dev/null | cut -f1)

    if [ "$LOG_SIZE_MB" -lt 500 ]; then
        print_success "Logs directory: $LOG_SIZE (healthy)"
    elif [ "$LOG_SIZE_MB" -lt 1000 ]; then
        print_warning "Logs directory: $LOG_SIZE (consider cleanup)"
    else
        print_error "Logs directory: $LOG_SIZE (cleanup recommended!)"
        print_info "  Run: find logs/ -name '*.log.*' -mtime +7 -delete"
    fi
else
    print_warning "Logs directory not found"
fi

# Check Redis data volume
REDIS_VOLUME_SIZE=$(docker exec crypto_ltp_redis du -sh /data 2>/dev/null | cut -f1 || echo "N/A")
print_info "Redis data volume: $REDIS_VOLUME_SIZE"

# Check available disk space
DISK_AVAILABLE=$(df -h . | tail -1 | awk '{print $4}')
print_info "Available disk space: $DISK_AVAILABLE"
echo ""

# 7. Check Resource Usage
print_header "7. Resource Usage:"
if docker stats --no-stream crypto_ltp_redis crypto_ltp_app 2>/dev/null; then
    echo ""

    # Get CPU and memory percentages
    REDIS_MEM=$(docker stats --no-stream --format "{{.MemPerc}}" crypto_ltp_redis 2>/dev/null | sed 's/%//')
    APP_MEM=$(docker stats --no-stream --format "{{.MemPerc}}" crypto_ltp_app 2>/dev/null | sed 's/%//')

    # Check if memory usage is high
    if command -v bc > /dev/null 2>&1; then
        if [ $(echo "$REDIS_MEM > 80" | bc) -eq 1 ]; then
            print_warning "Redis memory usage high (${REDIS_MEM}%)"
        fi
        if [ $(echo "$APP_MEM > 80" | bc) -eq 1 ]; then
            print_warning "App memory usage high (${APP_MEM}%)"
        fi
    fi
else
    print_warning "Could not fetch resource usage"
fi
echo ""

# 8. Check Recent Errors in Logs
print_header "8. Recent Errors (last 10 minutes):"
if [ -d "logs" ]; then
    ERROR_COUNT=$(find logs/ -name "*.log" -mmin -10 -exec grep -i "error\|exception\|failed" {} \; 2>/dev/null | wc -l)

    if [ "$ERROR_COUNT" -eq 0 ]; then
        print_success "No errors found in recent logs"
    elif [ "$ERROR_COUNT" -lt 10 ]; then
        print_warning "Found $ERROR_COUNT error(s) in recent logs"
        print_info "  Check: tail -f logs/*.log | grep -i error"
    else
        print_error "Found $ERROR_COUNT error(s) in recent logs"
        print_info "  Check: tail -f logs/*.log | grep -i error"
    fi
else
    print_info "Logs directory not found"
fi
echo ""

# Summary
echo "=========================================="
echo "  Health Check Complete"
echo "=========================================="
echo ""

# Overall status
ALL_HEALTHY=true

# Check critical components
if [ "$RUNNING" -ne 2 ]; then ALL_HEALTHY=false; fi
if ! docker exec crypto_ltp_redis redis-cli $([ -n "$REDIS_PASSWORD" ] && echo "-a $REDIS_PASSWORD") ping > /dev/null 2>&1; then ALL_HEALTHY=false; fi
if ! curl -s http://localhost:8080/api/health | grep -q "healthy"; then ALL_HEALTHY=false; fi

if [ "$ALL_HEALTHY" = true ]; then
    print_success "Overall Status: HEALTHY"
    echo ""
    echo "Dashboard: http://localhost:8080"
    echo "Redis: localhost:6379"
    echo ""
    echo "For remote access, use SSH tunnel:"
    echo "  ssh -L 8080:localhost:8080 user@vps-ip"
else
    print_warning "Overall Status: ISSUES DETECTED"
    echo ""
    echo "Check the sections above for details."
    echo "Common fixes:"
    echo "  - Restart: docker compose restart"
    echo "  - View logs: docker compose logs -f"
    echo "  - Check config: cat .env.production.local"
fi

echo ""
echo "For more info: docker compose logs -f"
echo "=========================================="

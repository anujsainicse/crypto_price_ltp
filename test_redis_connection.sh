#!/bin/bash

# Redis Connection Test Script
# Use this to verify Redis is working before connecting with GUI tools

echo "=========================================="
echo "Redis Connection Test"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if Docker is running
echo "1. Checking Docker status..."
if docker ps &> /dev/null; then
    echo -e "${GREEN}✓${NC} Docker is running"
else
    echo -e "${RED}✗${NC} Docker is not running"
    exit 1
fi
echo ""

# Check if Redis container is running
echo "2. Checking Redis container..."
if docker ps --format '{{.Names}}' | grep -q "crypto_ltp_redis"; then
    STATUS=$(docker inspect crypto_ltp_redis --format='{{.State.Status}}')
    HEALTH=$(docker inspect crypto_ltp_redis --format='{{.State.Health.Status}}' 2>/dev/null || echo "no healthcheck")
    echo -e "${GREEN}✓${NC} Redis container is running"
    echo "   Status: $STATUS"
    echo "   Health: $HEALTH"
else
    echo -e "${RED}✗${NC} Redis container is not running"
    echo "   Run: docker-compose up -d redis"
    exit 1
fi
echo ""

# Test Redis connection
echo "3. Testing Redis connection..."
if docker exec crypto_ltp_redis redis-cli PING &> /dev/null; then
    RESPONSE=$(docker exec crypto_ltp_redis redis-cli PING)
    echo -e "${GREEN}✓${NC} Redis connection successful (Response: $RESPONSE)"
else
    echo -e "${RED}✗${NC} Cannot connect to Redis"
    exit 1
fi
echo ""

# Check key count
echo "4. Checking data in Redis..."
KEY_COUNT=$(docker exec crypto_ltp_redis redis-cli DBSIZE)
echo "   Total keys in DB 0: $KEY_COUNT"

if [ "$KEY_COUNT" -gt 0 ]; then
    echo -e "${GREEN}✓${NC} Data exists in Redis"
else
    echo -e "${YELLOW}⚠${NC} No keys found in Redis"
    echo "   This might mean services aren't running yet"
fi
echo ""

# List all keys
if [ "$KEY_COUNT" -gt 0 ]; then
    echo "5. Current keys in database:"
    docker exec crypto_ltp_redis redis-cli KEYS "*" | while read -r key; do
        if [ -n "$key" ]; then
            TTL=$(docker exec crypto_ltp_redis redis-cli TTL "$key")
            echo "   - $key (TTL: ${TTL}s)"
        fi
    done
    echo ""
fi

# Show sample data
echo "6. Sample data (BTC from Bybit):"
BTC_DATA=$(docker exec crypto_ltp_redis redis-cli HGETALL "bybit_spot:BTC" 2>/dev/null)
if [ -n "$BTC_DATA" ]; then
    echo "$BTC_DATA" | awk 'NR%2{printf "   %s: ", $0; next} {print $0}'
    echo -e "${GREEN}✓${NC} Price data is being updated"
else
    echo -e "${YELLOW}⚠${NC} No BTC data found"
    echo "   Check if Bybit service is running"
fi
echo ""

# Check Redis stats
echo "7. Redis statistics:"
TOTAL_COMMANDS=$(docker exec crypto_ltp_redis redis-cli INFO stats | grep total_commands_processed | cut -d: -f2 | tr -d '\r')
OPS_PER_SEC=$(docker exec crypto_ltp_redis redis-cli INFO stats | grep instantaneous_ops_per_sec | cut -d: -f2 | tr -d '\r')
echo "   Total commands processed: $TOTAL_COMMANDS"
echo "   Current ops/second: $OPS_PER_SEC"
if [ "$OPS_PER_SEC" -gt 0 ]; then
    echo -e "${GREEN}✓${NC} Redis is actively receiving updates"
else
    echo -e "${YELLOW}⚠${NC} Redis is idle (no active updates)"
fi
echo ""

# Display connection settings
echo "=========================================="
echo "GUI Connection Settings:"
echo "=========================================="
echo "Host:     localhost (or 127.0.0.1)"
echo "Port:     6379"
echo "Password: [leave empty/blank]"
echo "Database: 0"
echo "TLS/SSL:  Disabled"
echo ""

# Final summary
echo "=========================================="
echo "Summary:"
echo "=========================================="
if [ "$KEY_COUNT" -gt 0 ] && [ "$OPS_PER_SEC" -gt 0 ]; then
    echo -e "${GREEN}✓ Redis is working perfectly!${NC}"
    echo ""
    echo "Your Redis database has $KEY_COUNT keys and is"
    echo "processing $OPS_PER_SEC operations per second."
    echo ""
    echo "If your GUI tool shows no data:"
    echo "1. Ensure you're connected to database 0 (not 1)"
    echo "2. Leave the password field completely empty"
    echo "3. Try refreshing or reconnecting"
    echo "4. Use host 'localhost' or '127.0.0.1'"
elif [ "$KEY_COUNT" -gt 0 ]; then
    echo -e "${YELLOW}⚠ Redis has data but no active updates${NC}"
    echo ""
    echo "Check if the application is running:"
    echo "  docker logs crypto_ltp_app --tail 50"
elif [ "$OPS_PER_SEC" -gt 0 ]; then
    echo -e "${YELLOW}⚠ Redis is active but has no keys${NC}"
    echo ""
    echo "Data might be expiring. Wait a few seconds and re-run this script."
else
    echo -e "${RED}⚠ Redis is running but appears idle${NC}"
    echo ""
    echo "Check application status:"
    echo "  docker-compose ps"
    echo "  docker logs crypto_ltp_app"
fi

echo ""
echo "For detailed connection guide, see:"
echo "  REDIS_CONNECTION_GUIDE.md"
echo ""

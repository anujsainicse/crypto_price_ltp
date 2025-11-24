#!/bin/bash

# Rollback script for Crypto Price LTP deployment
# Usage: ./rollback.sh [version]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
DEPLOY_PATH="/opt/crypto_price_ltp"

echo -e "${YELLOW}==================== Rollback Started ====================${NC}"

# Check if running on VPS
if [[ $(hostname) != *"vps"* ]] && [[ $(whoami) != "anuj" ]]; then
    echo -e "${RED}This script should be run on the VPS server${NC}"
    echo "Connecting to VPS..."
    ssh anuj@139.84.173.21 "cd $DEPLOY_PATH && bash scripts/rollback.sh $1"
    exit $?
fi

# Navigate to deployment directory
cd $DEPLOY_PATH

# Get current version
CURRENT_VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "unknown")
echo -e "Current version: ${YELLOW}$CURRENT_VERSION${NC}"

# If no version specified, show available versions
if [ -z "$1" ]; then
    echo -e "\n${YELLOW}Available versions:${NC}"
    git tag -l | tail -10
    echo -e "\n${YELLOW}Usage:${NC} $0 <version>"
    echo -e "${YELLOW}Example:${NC} $0 v1.0.0"
    exit 1
fi

TARGET_VERSION=$1

# Confirm rollback
echo -e "\n${YELLOW}Are you sure you want to rollback from $CURRENT_VERSION to $TARGET_VERSION?${NC}"
read -p "Type 'yes' to confirm: " confirmation

if [ "$confirmation" != "yes" ]; then
    echo -e "${RED}Rollback cancelled${NC}"
    exit 1
fi

# Perform rollback
echo -e "\n${GREEN}Rolling back to version $TARGET_VERSION...${NC}"

# Backup current configs
cp .env .env.rollback.$(date +%Y%m%d_%H%M%S)

# Checkout target version
git checkout $TARGET_VERSION

# Restore production configs
if [ -f .env.production.local ]; then
    cp .env.production.local .env
fi

if [ -f docker-compose.prod.yml ]; then
    cp docker-compose.prod.yml docker-compose.yml
fi

# Rebuild and restart containers
echo -e "\n${GREEN}Rebuilding containers...${NC}"
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Wait for services to start
echo -e "\n${GREEN}Waiting for services to start...${NC}"
sleep 10

# Check status
echo -e "\n${GREEN}Checking service status...${NC}"
docker-compose ps

# Check logs
echo -e "\n${GREEN}Recent logs:${NC}"
docker logs crypto-price-ltp --tail=20

echo -e "\n${GREEN}==================== Rollback Complete ====================${NC}"
echo -e "${GREEN}Successfully rolled back to version $TARGET_VERSION${NC}"
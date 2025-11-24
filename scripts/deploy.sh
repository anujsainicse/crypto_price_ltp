#!/bin/bash

# Deployment script for Crypto Price LTP
# Usage: ./deploy.sh <version> [message]
# Example: ./deploy.sh 1.0.1 "Added health check endpoint"

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}==================== Crypto Price LTP Deployment ====================${NC}"

# Check arguments
if [ -z "$1" ]; then
    echo -e "${RED}Error: Version number required${NC}"
    echo -e "${YELLOW}Usage:${NC} $0 <version> [message]"
    echo -e "${YELLOW}Example:${NC} $0 1.0.1 \"Added new feature\""
    exit 1
fi

VERSION=$1
MESSAGE=${2:-"Release v$VERSION"}

# Validate version format
if ! [[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo -e "${RED}Error: Invalid version format. Use semantic versioning (e.g., 1.0.1)${NC}"
    exit 1
fi

echo -e "${GREEN}Preparing to deploy version v$VERSION${NC}"
echo -e "Message: $MESSAGE"

# Update version.py
echo -e "\n${YELLOW}Updating version.py...${NC}"
sed -i.bak "s/VERSION = \"[^\"]*\"/VERSION = \"$VERSION\"/" version.py
sed -i.bak "s/BUILD_DATE = \"[^\"]*\"/BUILD_DATE = \"$(date +%Y-%m-%d)\"/" version.py
rm -f version.py.bak

# Check for uncommitted changes
if [[ -n $(git status --porcelain) ]]; then
    echo -e "\n${YELLOW}Committing changes...${NC}"
    git add .
    git commit -m "chore: bump version to v$VERSION

$MESSAGE"
fi

# Create and push tag
echo -e "\n${YELLOW}Creating tag v$VERSION...${NC}"
git tag -a "v$VERSION" -m "$MESSAGE"

echo -e "\n${YELLOW}Pushing to GitHub...${NC}"
git push origin main
git push origin "v$VERSION"

echo -e "\n${GREEN}==================== Deployment Triggered ====================${NC}"
echo -e "${GREEN}✅ Version v$VERSION has been tagged and pushed${NC}"
echo -e "${GREEN}✅ GitHub Actions will now deploy to VPS automatically${NC}"
echo -e "\n${YELLOW}Monitor deployment at:${NC}"
echo -e "  https://github.com/anujsainicse/crypto_price_ltp/actions"
echo -e "\n${YELLOW}After deployment completes, verify at:${NC}"
echo -e "  http://139.84.173.21:8080/health"
echo -e "\n${YELLOW}Dashboard:${NC}"
echo -e "  http://139.84.173.21:8080"
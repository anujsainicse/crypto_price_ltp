# 🚀 Crypto Price LTP - Deployment Guide

## Overview

This project uses **GitHub Actions** for automated deployment to VPS, similar to the Scalper Bot project. Deployments are triggered by version tags.

## 🔧 Setup Instructions

### 1. Configure GitHub Repository Secret

Add your VPS SSH private key to GitHub:

1. Go to your repository on GitHub
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Name: `VPS_SSH_KEY`
5. Value: Paste your SSH private key (the content of `~/.ssh/id_rsa`)

```bash
# Get your SSH private key
cat ~/.ssh/id_rsa
```

### 2. Initial VPS Setup (Already Completed)

✅ Git repository initialized at `/opt/crypto_price_ltp`
✅ Production configs saved as `.env.production.local`
✅ Docker Compose configured

## 📦 Deployment Methods

### Method 1: Automated Deployment (Recommended)

Use the deployment script:

```bash
cd /path/to/crypto_price_ltp
./scripts/deploy.sh 1.0.1 "Description of changes"
```

This will:
- Update version.py
- Commit changes
- Create a git tag
- Push to GitHub
- Trigger GitHub Actions deployment

### Method 2: Manual Tag Deployment

```bash
# Update version.py manually
vim version.py  # Change VERSION = "1.0.1"

# Commit changes
git add .
git commit -m "feat: your feature description"

# Create and push tag
git tag -a v1.0.1 -m "Release v1.0.1: Description"
git push origin main --tags
```

### Method 3: GitHub UI Deployment

1. Go to **Actions** tab on GitHub
2. Select **Deploy to VPS** workflow
3. Click **Run workflow**
4. Enter version tag (e.g., v1.0.1)
5. Click **Run workflow**

## 🔄 Rollback Procedure

If a deployment fails or introduces issues:

### Quick Rollback

```bash
ssh anuj@139.84.173.21
cd /opt/crypto_price_ltp
./scripts/rollback.sh v1.0.0  # Previous stable version
```

### Manual Rollback

```bash
ssh anuj@139.84.173.21
cd /opt/crypto_price_ltp

# Check available versions
git tag -l

# Rollback to specific version
git checkout v1.0.0

# Restore production configs
cp .env.production.local .env
cp docker-compose.prod.yml docker-compose.yml

# Rebuild containers
docker-compose down
docker-compose up -d --build
```

## 🌐 Production Configuration

### Files Preserved During Deployment

These files are NOT overwritten during deployment:
- `.env.production.local` - Production environment variables
- `docker-compose.prod.yml` - Production Docker configuration
- `logs/` - Application logs

### Environment Variables

Production environment is stored in `.env.production.local`:

```env
REDIS_HOST=scalper-redis-prod
REDIS_PORT=6379
REDIS_PASSWORD=YOUR_REDIS_PASSWORD
REDIS_DB=0
REDIS_TTL=3600
LOG_LEVEL=INFO
```

## 📊 Monitoring

### Health Check

```bash
# Check deployment health
curl http://139.84.173.21:8080/health

# Expected response:
{
  "status": "healthy",
  "version": "1.0.1",
  "redis_connected": true,
  "active_services": 3,
  "total_services": 5
}
```

### View Logs

```bash
# SSH to VPS
ssh anuj@139.84.173.21

# View container logs
docker logs crypto-price-ltp --tail 100 -f

# Check service status
docker-compose ps
```

### Dashboard

Access the web dashboard at: http://139.84.173.21:8080

## 🔍 Troubleshooting

### Container Won't Start

```bash
# Check logs
docker logs crypto-price-ltp

# Check Docker status
docker-compose ps

# Rebuild from scratch
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Redis Connection Issues

```bash
# Test Redis connection
docker exec crypto-price-ltp python -c "
import redis
r = redis.Redis(
    host='scalper-redis-prod',
    port=6379,
    password='YOUR_PASSWORD'
)
print('Connected:', r.ping())
"
```

### Port Already in Use

```bash
# Find process using port 8080
sudo lsof -i :8080

# Kill process if needed
sudo kill -9 <PID>
```

## 📈 Version History

Track deployments in GitHub:
- **Releases**: https://github.com/anujsainicse/crypto_price_ltp/releases
- **Actions**: https://github.com/anujsainicse/crypto_price_ltp/actions

## 🛡️ Security Notes

- Never commit `.env.production.local` or production passwords
- Keep `VPS_SSH_KEY` secret secure
- Use strong Redis passwords
- Regularly update dependencies

## 📝 Deployment Checklist

Before deploying:
- [ ] Test locally with Docker
- [ ] Update version.py
- [ ] Update CHANGELOG if exists
- [ ] Verify health check endpoint works
- [ ] Check Redis connectivity
- [ ] Ensure no sensitive data in commits

After deploying:
- [ ] Verify health check passes
- [ ] Check all services are running
- [ ] Monitor logs for errors
- [ ] Test critical functionality
- [ ] Update deployment documentation if needed

## 🤝 Support

For issues or questions:
1. Check logs: `docker logs crypto-price-ltp`
2. Check GitHub Actions logs
3. Verify Redis connectivity
4. Ensure all environment variables are set

---

**Last Updated**: November 2024
**Maintainer**: Anuj Saini
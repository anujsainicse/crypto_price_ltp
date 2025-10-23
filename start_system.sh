#!/bin/bash
# Crypto Price LTP System Startup Script

echo "=========================================="
echo "Starting Crypto Price LTP System"
echo "=========================================="

# Change to script directory
cd "$(dirname "$0")"

# Check if Redis is running
if ! redis-cli ping > /dev/null 2>&1; then
    echo "ERROR: Redis is not running!"
    echo "Please start Redis first: redis-server"
    exit 1
fi

echo "✓ Redis is running"

# Kill any existing processes
echo "Checking for existing processes..."
pkill -f "web_dashboard.py" 2>/dev/null
pkill -f "manager.py" 2>/dev/null
sleep 1

# Start web dashboard
echo "Starting Web Dashboard on port 8080..."
nohup python web_dashboard.py > logs/web_dashboard.log 2>&1 &
WEB_PID=$!
sleep 2

# Verify web dashboard started
if ! ps -p $WEB_PID > /dev/null; then
    echo "ERROR: Failed to start web dashboard"
    cat logs/web_dashboard.log
    exit 1
fi

echo "✓ Web Dashboard started (PID: $WEB_PID)"

# Start service manager
echo "Starting Service Manager..."
nohup python manager.py > logs/service_manager.log 2>&1 &
MANAGER_PID=$!
sleep 2

# Verify manager started
if ! ps -p $MANAGER_PID > /dev/null; then
    echo "ERROR: Failed to start service manager"
    cat logs/service_manager.log
    exit 1
fi

echo "✓ Service Manager started (PID: $MANAGER_PID)"

echo ""
echo "=========================================="
echo "System Started Successfully!"
echo "=========================================="
echo "Web Dashboard: http://localhost:8080"
echo "API Docs: http://localhost:8080/docs"
echo ""
echo "All services are in STOPPED state by default."
echo "Use the web dashboard to start/stop individual services."
echo ""
echo "To stop the system, run: ./stop_system.sh"
echo "=========================================="

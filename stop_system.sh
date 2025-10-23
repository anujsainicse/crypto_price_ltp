#!/bin/bash
# Crypto Price LTP System Shutdown Script

echo "=========================================="
echo "Stopping Crypto Price LTP System"
echo "=========================================="

# Kill web dashboard
echo "Stopping Web Dashboard..."
pkill -f "web_dashboard.py"

# Kill service manager
echo "Stopping Service Manager..."
pkill -f "manager.py"

sleep 2

# Verify all stopped
if pgrep -f "web_dashboard.py" > /dev/null || pgrep -f "manager.py" > /dev/null; then
    echo "WARNING: Some processes still running, forcing shutdown..."
    pkill -9 -f "web_dashboard.py"
    pkill -9 -f "manager.py"
    sleep 1
fi

echo ""
echo "=========================================="
echo "System Stopped Successfully!"
echo "=========================================="

#!/bin/bash
set -e

echo "Starting Crypto Price LTP System..."

# Function to handle shutdown
shutdown() {
    echo "Shutting down services..."
    kill -TERM "$manager_pid" "$dashboard_pid" 2>/dev/null || true
    wait "$manager_pid" "$dashboard_pid" 2>/dev/null || true
    echo "Services stopped gracefully"
    exit 0
}

# Trap SIGTERM and SIGINT
trap shutdown SIGTERM SIGINT

# Wait for Redis to be ready
echo "Waiting for Redis to be ready..."
until python -c "import redis; r = redis.Redis(host='${REDIS_HOST:-redis}', port=${REDIS_PORT:-6379}); r.ping()" 2>/dev/null; do
    echo "Redis is unavailable - sleeping"
    sleep 2
done
echo "Redis is ready!"

# Start the manager in the background
echo "Starting Service Manager..."
python manager.py &
manager_pid=$!

# Give manager a moment to initialize
sleep 3

# Start the web dashboard in the background
echo "Starting Web Dashboard..."
python web_dashboard.py &
dashboard_pid=$!

echo "All services started successfully!"
echo "Manager PID: $manager_pid"
echo "Dashboard PID: $dashboard_pid"
echo "Web Dashboard available at http://localhost:8080"

# Wait for both processes
wait "$manager_pid" "$dashboard_pid"

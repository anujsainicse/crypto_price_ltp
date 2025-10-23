╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║         CRYPTO PRICE LTP - SUPER SIMPLE GUIDE               ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝


🚀 HOW TO START:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   $ ./run.sh

   Then open: http://localhost:8080


🛑 HOW TO STOP:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   Press Ctrl+C in the terminal where run.sh is running


📋 THAT'S IT!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   • One command to start: ./run.sh
   • One key to stop: Ctrl+C
   • Everything happens automatically!


📍 BEFORE YOU START:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   Make sure Redis is running:
   
   $ redis-cli ping
   
   Should return: PONG


🎮 WHAT HAPPENS WHEN YOU RUN ./run.sh:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   1. Checks Redis is running
   2. Cleans up old processes
   3. Starts Web Dashboard (port 8080)
   4. Starts Service Manager
   5. Shows you logs in real-time
   6. Waits for Ctrl+C to stop


🌐 WEB DASHBOARD:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   http://localhost:8080
   
   • All services start STOPPED
   • Click START to activate services
   • Click STOP to deactivate
   • Dashboard auto-refreshes


📦 AVAILABLE SERVICES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   • Bybit Spot          → Real-time crypto prices
   • CoinDCX Futures     → Futures prices
   • CoinDCX Funding     → Funding rates
   • Delta Futures       → Delta futures prices
   • Delta Options       → Options with Greeks


💾 ACCESS DATA:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   $ redis-cli HGETALL bybit_spot:BTCUSDT
   $ redis-cli KEYS "*"


✨ FEATURES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   ✓ Single command start/stop
   ✓ Automatic port conflict resolution
   ✓ Graceful shutdown on Ctrl+C
   ✓ All services start STOPPED
   ✓ Live log streaming
   ✓ Process cleanup on exit


📚 MORE INFO:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   See START_HERE.md for full guide


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

           Ready to go? Just type: ./run.sh

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


#!/bin/bash

# Stop existing processes
echo "Terminating existing process on port 5050..."
sudo fuser -k 5050/tcp

# Wait briefly to ensure process is terminated
sleep 2

# Kill any residual uvicorn processes for KAIROS
echo "Killing residual uvicorn processes for KAIROS..."
ps aux | grep '[u]vicorn.*server' | awk '{print $2}' | xargs -r sudo kill -9
sleep 2

# Clear Python cache
echo "Clearing Python cache..."
find /home/ubuntu/corporate-KAIROS -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find /home/ubuntu/corporate-KAIROS -name "*.pyc" -delete 2>/dev/null
find /home/ubuntu/corporate-KAIROS -name "*.pyo" -delete 2>/dev/null
echo "Cache cleared."

# Start KAIROS server
echo "Starting KAIROS server on port 5050..."
nohup /home/ubuntu/anaconda3/envs/kairos/bin/uvicorn \
    server:app \
    --host 0.0.0.0 \
    --port 5050 \
    --workers 2 \
    --loop uvloop \
    --http httptools \
    > kairos.log 2>&1 &

# Wait briefly to allow service to start
sleep 2

# Verification
echo ""
echo "Running Processes:"
ps aux | grep '[u]vicorn.*server'

echo ""
echo "Port in use:"
netstat -tuln | grep 5050

echo ""
echo "Service log (last 10 lines):"
tail -n 10 kairos.log

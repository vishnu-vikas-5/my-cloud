#!/bin/bash
# Move to the directory containing this script
cd "$(dirname "$0")"

echo "==========================================="
echo "☁️ Starting MyCloud private server..."
echo "==========================================="

# Check if port 5001 is already in use and free it if necessary
PID=$(lsof -t -i tcp:5001)
if [ ! -z "$PID" ]; then
    echo "Port 5001 is in use. Releasing port..."
    kill -9 $PID
fi

# Run the Flask app in the background
python3 app.py &
FLASK_PID=$!

# Wait for Flask to boot up
sleep 1.5

# Open the website in the default browser
open "http://localhost:5001"

# Handle graceful shutdown on terminal close
cleanup() {
    echo ""
    echo "Stopping MyCloud server..."
    kill $FLASK_PID
    exit
}

trap cleanup SIGINT SIGTERM EXIT

# Keep the terminal window active
wait $FLASK_PID

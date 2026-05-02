"""
Civic Code - Local Runner
Run with: python run.py
Then open: http://localhost:5000
"""
import os
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

# Set default port for local dev
os.environ.setdefault("PORT", "5000")

# Import and start the app
from app import socketio, app
from database import init_db

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    print(f"\n✅ Civic Code is running at http://localhost:{port}\n")
    socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)

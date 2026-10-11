"""
WSGI Production Entry Point for Smart Maintenance Tracker
Runs the application using Waitress (Production WSGI Server for Windows & Linux).
No development server warnings.
"""
from app import app
from scheduler import start_scheduler
from waitress import serve

if __name__ == '__main__':
    start_scheduler()
    print("=" * 60)
    print(" Smart Maintenance Tracker - Production WSGI Server")
    print(" Server running on http://127.0.0.1:5000")
    print("=" * 60)
    serve(app, host='0.0.0.0', port=5000, threads=6)

from main import app
from waitress import serve
import logging
import os

# Ensure we are in the correct directory for relative paths in main.py
os.chdir(os.path.dirname(os.path.abspath(__file__)))

if __name__ == '__main__':
    # Configure logging for production
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        handlers=[
            logging.FileHandler("server.log"),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger('waitress')
    logger.setLevel(logging.INFO)

    print("========================================")
    print("   NSP COSMETIC POS - PRODUCTION SERVER   ")
    print("========================================")
    print("Status: Running via Waitress WSGI")
    print("Address: http://0.0.0.0:5000")
    print("Log: server.log")
    print("----------------------------------------")
    
    # Start Waitress production server
    serve(app, host='0.0.0.0', port=5000, threads=10)

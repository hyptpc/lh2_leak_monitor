"""Local credential loading; no credentials are stored in the source tree."""

import os
from pathlib import Path


def load_environment():
    """Load the local .env if python-dotenv is available; environment wins."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
    return True


def caen_credentials():
    username = os.environ.get('CAEN_USERNAME', '').strip()
    password = os.environ.get('CAEN_PASSWORD', '')
    if not username or not password:
        raise RuntimeError('Set CAEN_USERNAME and CAEN_PASSWORD in the environment or .env')
    return username, password

"""
backend/__main__.py
─────────────────────────────────────────────────────────────────────────────
Allows running the backend as a module from the project root:
  python -m backend

Equivalent to:
  cd backend && python main.py
─────────────────────────────────────────────────────────────────────────────
"""

import os
import sys

# Ensure the backend directory is on sys.path when invoked as a module
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from main import main

if __name__ == "__main__":
    main()

"""Enables `python -m mdflow`."""
import os
import sys

try:
    from .mdflow import main
except ImportError:  # running the file directly: python mdflow/__main__.py
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mdflow import main

sys.exit(main())

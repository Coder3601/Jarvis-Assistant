"""JARVIS — futuristic personal AI assistant.

Run:  python main.py      (or double-click run.bat on Windows)
"""
import sys

if sys.version_info < (3, 10):
    sys.exit("JARVIS requires Python 3.10+ (3.12 recommended).")

from jarvis.app import main

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audio_analyzer.py (Wrapper)

This is a backward-compatibility wrapper after the project restructuring.
The actual implementation is now located in `src/ytbnotes/analyzer/`.
"""

import sys
from pathlib import Path

# Add project root to sys.path to ensure 'src' is discoverable
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ytbnotes.analyzer.main import main

if __name__ == "__main__":
    main()

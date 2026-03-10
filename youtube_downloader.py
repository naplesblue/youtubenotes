#!/usr/bin/env python3
"""
兼容包裹脚本：为了不破坏现有的执行习惯和外部调用，
将调用重定向到新的 src/ytbnotes/downloader/downloader.py 模块。
"""

import sys
from pathlib import Path

# 将 src 加入 sys.path 确保包能被找到
PROJECT_ROOT = Path(__file__).resolve().parent
src_path = str(PROJECT_ROOT / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

if __name__ == "__main__":
    from ytbnotes.downloader.downloader import main
    main()

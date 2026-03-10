#!/usr/bin/env python3
"""
run_pipeline.py

YoutubeNotes 端到端流水线：下载 → 分析 → 同步
支持跳步、超时控制，执行完输出 JSON 摘要。

用法:
    python run_pipeline.py                  # 完整流程
    python run_pipeline.py --skip-download  # 跳过下载
    python run_pipeline.py --only-sync      # 只同步
    python run_pipeline.py --dry-run        # 预览执行计划
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 配置 ────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable  # 使用当前 Python 解释器（与虚拟环境一致）

STEPS = [
    {
        "name": "download",
        "label": "📥 下载",
        "script": "youtube_downloader.py",
        "timeout": 600,       # 10 分钟
        "description": "RSS 拉取 + yt-dlp 下载最新音频",
    },
    {
        "name": "analyze",
        "label": "🧠 分析",
        "script": "audio_analyzer.py",
        "timeout": 1800,      # 30 分钟（ASR + LLM 可能较慢）
        "description": "字幕/ASR 转录 + Qwen 文本分析",
    },
    {
        "name": "sync",
        "label": "📋 同步",
        "script": "obsidian_sync.py",
        "timeout": 120,       # 2 分钟
        "description": "分析结果同步到 Obsidian Vault",
    },
]

# ── 执行引擎 ────────────────────────────────────────────────────────────────

def run_step(step: dict, env: dict) -> dict:
    """执行单个步骤，返回结果字典。"""
    script = PROJECT_DIR / step["script"]
    if not script.exists():
        return {
            "name": step["name"],
            "status": "error",
            "duration": 0,
            "error": f"脚本不存在: {script}",
        }

    start = time.time()
    try:
        result = subprocess.run(
            [PYTHON, str(script)],
            cwd=str(PROJECT_DIR),
            env=env,
            timeout=step["timeout"],
            capture_output=False,  # 直接输出到终端
        )
        duration = round(time.time() - start, 2)
        return {
            "name": step["name"],
            "status": "success" if result.returncode == 0 else "failure",
            "duration": duration,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        duration = round(time.time() - start, 2)
        return {
            "name": step["name"],
            "status": "timeout",
            "duration": duration,
            "error": f"超时 ({step['timeout']}s)",
        }
    except Exception as exc:
        duration = round(time.time() - start, 2)
        return {
            "name": step["name"],
            "status": "error",
            "duration": duration,
            "error": str(exc),
        }


def main():
    parser = argparse.ArgumentParser(
        description="YoutubeNotes 端到端流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_pipeline.py                  完整流程
  python run_pipeline.py --skip-download  跳过下载，只分析+同步
  python run_pipeline.py --only-sync      只同步到 Obsidian
  python run_pipeline.py --dry-run        预览执行计划
        """,
    )
    parser.add_argument("--skip-download", action="store_true", help="跳过下载步骤")
    parser.add_argument("--skip-analyze", action="store_true", help="跳过分析步骤")
    parser.add_argument("--only-sync", action="store_true", help="只执行同步步骤")
    parser.add_argument("--only-download", action="store_true", help="只执行下载步骤")
    parser.add_argument("--dry-run", action="store_true", help="预览执行计划，不实际运行")
    parser.add_argument("--no-stop-on-error", action="store_true",
                        help="某步失败后继续执行后续步骤（默认失败即停）")
    args = parser.parse_args()

    # 确定要执行的步骤
    steps_to_run = []
    for step in STEPS:
        if args.only_sync and step["name"] != "sync":
            continue
        if args.only_download and step["name"] != "download":
            continue
        if args.skip_download and step["name"] == "download":
            continue
        if args.skip_analyze and step["name"] == "analyze":
            continue
        steps_to_run.append(step)

    if not steps_to_run:
        print("⚠️  没有要执行的步骤（检查参数组合）")
        sys.exit(1)

    # 打印执行计划
    print("=" * 60)
    print("🚀 YoutubeNotes Pipeline")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   项目: {PROJECT_DIR}")
    print(f"   Python: {PYTHON}")
    print(f"   步骤: {' → '.join(s['label'] for s in steps_to_run)}")
    print("=" * 60)

    if args.dry_run:
        print("\n📋 执行计划（dry-run 模式，不实际运行）:\n")
        for i, step in enumerate(steps_to_run, 1):
            print(f"  {i}. {step['label']}  {step['description']}")
            print(f"     脚本: {step['script']}  超时: {step['timeout']}s\n")
        sys.exit(0)

    # 继承当前环境变量（包括 .env 中的设置）
    env = os.environ.copy()

    # 执行
    pipeline_start = time.time()
    results = []
    failed = False

    for i, step in enumerate(steps_to_run, 1):
        if failed and not args.no_stop_on_error:
            results.append({
                "name": step["name"],
                "status": "skipped",
                "duration": 0,
                "reason": "前序步骤失败",
            })
            continue

        print(f"\n{'─' * 60}")
        print(f"  {step['label']}  ({i}/{len(steps_to_run)})  {step['description']}")
        print(f"{'─' * 60}\n")

        result = run_step(step, env)
        results.append(result)

        if result["status"] == "success":
            print(f"\n  ✅ {step['label']} 完成 ({result['duration']}s)")
        else:
            print(f"\n  ❌ {step['label']} 失败: {result.get('error', result['status'])} ({result['duration']}s)")
            failed = True

    # 汇总
    pipeline_duration = round(time.time() - pipeline_start, 2)
    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_duration": pipeline_duration,
        "success": all(r["status"] == "success" for r in results if r["status"] != "skipped"),
        "steps": results,
    }

    print(f"\n{'=' * 60}")
    print("📊 执行摘要")
    print(f"{'=' * 60}")
    for r in results:
        icon = {"success": "✅", "failure": "❌", "timeout": "⏰", "error": "💥", "skipped": "⏭️"}.get(r["status"], "?")
        print(f"  {icon} {r['name']:12s}  {r['status']:8s}  {r['duration']}s")
    print(f"\n  总耗时: {pipeline_duration}s")
    print(f"  结果: {'✅ 全部成功' if summary['success'] else '❌ 有步骤失败'}")
    print(f"{'=' * 60}\n")

    # 写入 JSON 摘要（供 Agent 或脚本读取）
    summary_path = PROJECT_DIR / "pipeline_summary.json"
    try:
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"📄 执行摘要已保存: {summary_path}")
    except Exception as exc:
        print(f"⚠️  保存摘要失败: {exc}")

    sys.exit(0 if summary["success"] else 1)


if __name__ == "__main__":
    main()

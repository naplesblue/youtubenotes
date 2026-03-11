#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
博主预测追踪 CLI — 观点提取 + 行情回验 + 胜率评估

用法:
  # 从现有分析结果提取观点（调用 Cerebras 精标注）
  python run_tracker.py extract

  # 对已提取的观点做行情回验
  python run_tracker.py verify

  # 显示博主胜率排行 + 个股共识
  python run_tracker.py report

  # 一次性全流程：提取 → 回验 → 报告
  python run_tracker.py all
"""

import sys
import json
import logging
import argparse
import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

PROJECT_DIR = Path(__file__).resolve().parent


def cmd_extract(args):
    """从分析结果中提取观点。"""
    from src.ytbnotes.tracker.opinion_extractor import backfill_all_opinions

    print("=" * 60)
    print("📋 Phase 4: 观点提取 (Cerebras 精标注)")
    print("=" * 60)

    stats = backfill_all_opinions()
    print(f"\n✅ 完成: 处理 {stats['files_processed']} 个文件, "
          f"新增 {stats['total_opinions']} 条观点, "
          f"错误 {stats['errors']} 个")


def cmd_verify(args):
    """对已提取的观点做行情回验。"""
    from src.ytbnotes.tracker.opinion_store import load_opinions, save_opinions
    from src.ytbnotes.verifier.evaluator import verify_opinion

    print("=" * 60)
    print("📊 Phase 5: 行情回验")
    print("=" * 60)

    opinions = load_opinions()
    if not opinions:
        print("⚠️ 暂无观点数据，请先运行 extract")
        return

    today = datetime.date.today()
    verified = 0
    for op in opinions:
        verify_opinion(op, today)
        verified += 1

    save_opinions(opinions)
    print(f"\n✅ 完成: 回验 {verified} 条观点")


def cmd_report(args):
    """显示博主胜率排行 + 个股共识 + 写入 Obsidian 仪表盘。"""
    from src.ytbnotes.tracker.opinion_store import load_opinions
    from src.ytbnotes.verifier.scorer import compute_blogger_profiles, compute_ticker_consensus, print_summary
    from src.ytbnotes.verifier.dashboard import write_dashboard_to_vault

    opinions = load_opinions()
    if not opinions:
        print("⚠️ 暂无观点数据，请先运行 extract")
        return

    profiles = compute_blogger_profiles(opinions)
    consensus = compute_ticker_consensus(opinions)
    print_summary(profiles, consensus)

    # 保存报告 JSON
    report_dir = PROJECT_DIR / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    (report_dir / "blogger_profiles.json").write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (report_dir / "ticker_consensus.json").write_text(
        json.dumps(consensus, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"📁 报告已保存到 {report_dir}/")

    # 写入 Obsidian 仪表盘
    dashboard_path = write_dashboard_to_vault(
        opinions=opinions,
        profiles=profiles,
        consensus=consensus,
        config_path=PROJECT_DIR / "config.yaml",
    )
    if dashboard_path:
        print(f"📓 Obsidian 仪表盘已更新: {dashboard_path}")
    else:
        print("⚠️ Obsidian 仪表盘写入失败（请检查 config.yaml 中的 vault_root 和 folders.index 配置）")



def cmd_all(args):
    """全流程：提取 → 回验 → 报告。"""
    cmd_extract(args)
    print()
    cmd_verify(args)
    print()
    cmd_report(args)


def main():
    parser = argparse.ArgumentParser(
        description="博主预测追踪 — 观点提取 + 行情回验 + 胜率评估",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    sub.add_parser("extract", help="从分析结果提取观点（Cerebras 精标注）")
    sub.add_parser("verify",  help="用行情数据回验观点胜率")
    sub.add_parser("report",  help="生成博主排行 + 个股共识报告")
    sub.add_parser("all",     help="全流程：extract → verify → report")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "extract": cmd_extract,
        "verify": cmd_verify,
        "report": cmd_report,
        "all": cmd_all,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()

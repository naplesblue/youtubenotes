#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 1 冒烟测试脚本
验证 audio_analyzer.py 的核心功能是否正常
"""

import sys
import os
import json
import tempfile
import shutil
from pathlib import Path

# 确保可以导入 audio_analyzer 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audio_analyzer import parse_gemini_output, process_and_save_results

def test_parse_gemini_output():
    """测试解析 Gemini 输出"""
    print("=" * 60)
    print("测试 1: parse_gemini_output 函数")
    print("=" * 60)

    # 模拟 Gemini 输出
    mock_gemini_output = """
【精炼文本】
美股市场情绪改善与个股分析 (2026年03月03日)

## 主要主题和重要观点
今日美股市场整体表现强劲，科技股领涨。市场参与者对即将发布的经济数据持谨慎乐观态度。

## 市场整体情绪与走势
当前股市情绪偏向积极，[[VIX]] 指数回落至 20 以下。大盘呈现反弹迹象，但需警惕是否是"假突破"。

## 重要个股分析
- [[NVDA]]：股价在 145.50 美元获得强支撑，上方阻力位看 150 美元。建议关注是否能突破前期高点。
- [[AAPL]]：分析师给出目标价 220 美元，当前处于上升通道。
- [[SPX]]：标普指数在 5200 点遇到阻力，需要观察是否能有效突破。

## 总结
市场整体向好，但需关注关键阻力位的突破情况。

【关键信息摘要（含时间戳）】
- [00:15:30] [[NVDA]] 关键支撑位在 145.50 美元 [支撑位]
- [00:23:45] [[NVDA]] 上方阻力位看 150 美元 [阻力位]
- [00:45:12] [[AAPL]] 分析师给出目标价 220 美元 [目标价]
- [01:10:25] [[SPX]] 标普指数在 5200 点遇到阻力 [阻力位]
- [01:30:00] [[VIX]] 指数回落至 20 以下 [观察位]

【原子化点位数据 (JSON)】
```json
[
  {
    "ticker": "NVDA",
    "price": 145.50,
    "type": "support",
    "context": "年线支撑",
    "timestamp": "00:15:30"
  },
  {
    "ticker": "NVDA",
    "price": 150.00,
    "type": "resistance",
    "context": "前期高点",
    "timestamp": "00:23:45"
  },
  {
    "ticker": "AAPL",
    "price": 220.00,
    "type": "target",
    "context": "分析师目标价",
    "timestamp": "00:45:12"
  },
  {
    "ticker": "SPX",
    "price": 5200.00,
    "type": "resistance",
    "context": "整数关口阻力",
    "timestamp": "01:10:25"
  },
  {
    "ticker": "VIX",
    "price": 20.00,
    "type": "observation",
    "context": "情绪指标观察",
    "timestamp": "01:30:00"
  }
]
```
"""

    result = parse_gemini_output(mock_gemini_output)

    # 验证结果
    assert "refined_text" in result, "缺少 refined_text 字段"
    assert "summary_lines" in result, "缺少 summary_lines 字段"
    assert "price_levels_json" in result, "缺少 price_levels_json 字段"

    # 验证精炼文本
    assert "美股市场情绪改善" in result["refined_text"], "精炼文本解析错误"
    assert "[[NVDA]]" in result["refined_text"], "精炼文本中缺少双链格式"
    print("✓ 精炼文本解析正确")

    # 验证摘要行
    assert len(result["summary_lines"]) > 0, "摘要行为空"
    assert "[00:15:30]" in result["summary_lines"][0], "摘要行时间戳格式错误"
    assert "[[NVDA]]" in result["summary_lines"][0], "摘要行缺少双链格式"
    print(f"✓ 摘要行解析正确，共 {len(result['summary_lines'])} 条")

    # 验证 JSON 点位数据
    assert result["price_levels_json"] is not None, "JSON 点位数据为空"
    assert len(result["price_levels_json"]) == 5, f"JSON 点位数据数量错误，期望 5，实际 {len(result['price_levels_json'])}"

    # 验证 JSON 字段完整性
    first_level = result["price_levels_json"][0]
    assert "ticker" in first_level, "JSON 缺少 ticker 字段"
    assert "price" in first_level, "JSON 缺少 price 字段"
    assert "type" in first_level, "JSON 缺少 type 字段"
    assert "context" in first_level, "JSON 缺少 context 字段"
    assert "timestamp" in first_level, "JSON 缺少 timestamp 字段"
    print("✓ JSON 点位数据结构正确")

    print("\n✅ 测试 1 通过: parse_gemini_output 函数工作正常\n")
    return result

def test_process_and_save_results(mock_data):
    """测试处理和保存结果"""
    print("=" * 60)
    print("测试 2: process_and_save_results 函数")
    print("=" * 60)

    # 创建临时目录
    temp_dir = tempfile.mkdtemp(prefix="test_analysis_")
    print(f"临时测试目录: {temp_dir}")

    try:
        # 创建一个模拟的视频文件（仅用于路径存在性检查）
        mock_video_path = Path(temp_dir) / "20260303 - Test Video [test123].mp4"
        mock_video_path.write_text("dummy video content")

        # 创建模拟的历史数据
        mock_history_data = {
            "https://www.youtube.com/channel/test": {
                "test123": {
                    "file_path": str(mock_video_path),
                    "channel_name": "测试频道",
                    "video_id": "test123",
                    "title": "测试视频",
                    "upload_date": "2026-03-03"
                }
            }
        }

        # 调用被测函数
        markdown_path, json_path = process_and_save_results(
            raw_transcript="[00:00:00] 这是原始转录文本",
            refined_text=mock_data["refined_text"],
            summary_lines=mock_data["summary_lines"],
            price_levels_json=mock_data["price_levels_json"],
            video_path=str(mock_video_path),
            history_data=mock_history_data
        )

        print(f"生成的 Markdown 路径: {markdown_path}")
        print(f"生成的 JSON 路径: {json_path}")

        # 验证文件生成
        assert markdown_path is not None, "Markdown 文件路径为空"
        assert Path(markdown_path).exists(), f"Markdown 文件不存在: {markdown_path}"
        print("✓ Markdown 文件已生成")

        assert json_path is not None, "JSON 文件路径为空"
        assert Path(json_path).exists(), f"JSON 文件不存在: {json_path}"
        print("✓ JSON 文件已生成")

        # 验证 Markdown 内容
        md_content = Path(markdown_path).read_text(encoding='utf-8')

        # 验证 YAML Front Matter
        assert "---" in md_content, "缺少 YAML Front Matter 分隔符"
        assert "tags: [finance, youtube-notes]" in md_content, "缺少 tags 字段"
        assert "status: processed" in md_content, "缺少 status 字段"
        print("✓ YAML Front Matter 包含正确的 tags 和 status")

        # 验证双链格式
        assert "[[NVDA]]" in md_content, "Markdown 中缺少 [[NVDA]] 双链"
        assert "[[AAPL]]" in md_content, "Markdown 中缺少 [[AAPL]] 双链"
        assert "[[SPX]]" in md_content, "Markdown 中缺少 [[SPX]] 双链"
        print("✓ Markdown 中包含正确的双链格式")

        # 验证点位表格
        assert "【原子化点位概览】" in md_content, "缺少【原子化点位概览】章节"
        assert "| 股票 | 价格 | 类型 | 上下文 | 时间戳 |" in md_content, "点位表格格式错误"
        assert "| :--- | :--- | :--- | :--- | :--- |" in md_content, "点位表格分隔行缺失"
        print("✓ 点位数据表格已生成")

        # 验证精炼文本章节
        assert "# 【精炼文本】" in md_content, "缺少【精炼文本】章节"
        print("✓ 精炼文本章节已生成")

        # 验证关键信息摘要章节
        assert "# 【关键信息摘要（含时间戳）】" in md_content, "缺少【关键信息摘要】章节"
        print("✓ 关键信息摘要章节已生成")

        # 验证 JSON 文件内容
        json_content = json.loads(Path(json_path).read_text(encoding='utf-8'))
        assert isinstance(json_content, list), "JSON 内容不是数组"
        assert len(json_content) == 5, f"JSON 点位数量错误: {len(json_content)}"

        # 验证 JSON 字段
        for item in json_content:
            assert "ticker" in item, "JSON 条目缺少 ticker"
            assert "price" in item, "JSON 条目缺少 price"
            assert "type" in item, "JSON 条目缺少 type"
            assert "context" in item, "JSON 条目缺少 context"
            assert "timestamp" in item, "JSON 条目缺少 timestamp"
        print("✓ JSON 文件内容结构正确")

        print("\n✅ 测试 2 通过: process_and_save_results 函数工作正常\n")

        # 返回生成的文件路径供查看
        return {
            "markdown_path": markdown_path,
            "json_path": json_path,
            "temp_dir": temp_dir
        }

    except Exception as e:
        # 清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise e

def main():
    """主测试函数"""
    print("\n" + "=" * 60)
    print("Phase 1 冒烟测试开始")
    print("=" * 60 + "\n")

    try:
        # 测试 1: 解析 Gemini 输出
        mock_data = test_parse_gemini_output()

        # 测试 2: 处理和保存结果
        result = test_process_and_save_results(mock_data)

        # 显示生成的文件示例
        print("=" * 60)
        print("生成的文件示例")
        print("=" * 60)

        md_content = Path(result["markdown_path"]).read_text(encoding='utf-8')
        print("\nMarkdown 文件前 100 行预览:")
        print("-" * 40)
        lines = md_content.split('\n')
        for i, line in enumerate(lines[:100], 1):
            print(f"{i:3d}: {line}")
        if len(lines) > 100:
            print(f"... ({len(lines) - 100} more lines)")

        json_content = json.loads(Path(result["json_path"]).read_text(encoding='utf-8'))
        print("\nJSON 文件内容:")
        print("-" * 40)
        print(json.dumps(json_content, indent=2, ensure_ascii=False))

        print("\n" + "=" * 60)
        print("✅ 所有测试通过！Phase 1 集成验证成功！")
        print("=" * 60)
        print(f"\n临时文件保留在: {result['temp_dir']}")
        print("可以手动检查生成的文件")

        return 0

    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

    except Exception as e:
        print(f"\n❌ 测试发生异常: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)

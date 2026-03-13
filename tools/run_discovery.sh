#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 频道发现 & 筛选工作流
#
# 用法：
#   bash tools/run_discovery.sh              # 完整流程：发现 → 筛选 → 报告
#   bash tools/run_discovery.sh discover     # 仅发现候选
#   bash tools/run_discovery.sh screen       # 仅筛选已发现的候选
#   bash tools/run_discovery.sh report       # 仅输出当前状态报告
#
# 设计目标：可交给 AI 定时运行，输出结构化报告供人工审阅。
# ---------------------------------------------------------------------------

set -euo pipefail
cd "$(dirname "$0")/.."

DISCOVERED="brain/candidates_discovered.yaml"
REJECTED="brain/candidates_rejected.yaml"
WATCHLIST="brain/candidates_watchlist.yaml"
CHANNELS="channels.yaml"
REPORT_FILE="brain/discovery_report.md"

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

count_entries() {
    local file="$1"
    if [[ ! -f "$file" ]]; then
        echo 0
        return
    fi
    # 统计 YAML 列表中 "- name:" 或 "- channel_id:" 的条目数
    grep -c "^- " "$file" 2>/dev/null || echo 0
}

timestamp() {
    date "+%Y-%m-%d %H:%M:%S"
}

# ---------------------------------------------------------------------------
# Step 1: 发现候选
# ---------------------------------------------------------------------------

step_discover() {
    echo "========================================"
    echo "[$(timestamp)] Step 1: 发现候选频道"
    echo "========================================"

    local before
    before=$(count_entries "$DISCOVERED")

    python tools/discover_channels.py --source all

    local after
    after=$(count_entries "$DISCOVERED")
    local new_count=$((after - before))

    echo ""
    echo "发现结果: 新增 ${new_count} 个候选（总计 ${after} 个待筛选）"
    echo ""
}

# ---------------------------------------------------------------------------
# Step 2: 筛选候选
# ---------------------------------------------------------------------------

step_screen() {
    echo "========================================"
    echo "[$(timestamp)] Step 2: 筛选候选频道"
    echo "========================================"

    if [[ ! -f "$DISCOVERED" ]]; then
        echo "没有待筛选的候选（${DISCOVERED} 不存在）"
        return
    fi

    local count
    count=$(count_entries "$DISCOVERED")
    if [[ "$count" -eq 0 ]]; then
        echo "没有待筛选的候选"
        return
    fi

    echo "待筛选: ${count} 个候选频道"
    echo ""

    # 从 discovered YAML 提取 channel_id 列表，逐个筛选
    # 使用 Python 解析 YAML 以确保正确性
    python -c "
import sys
sys.path.insert(0, '.')
from ruamel.yaml import YAML
from pathlib import Path

yaml = YAML()
discovered = Path('$DISCOVERED')
if not discovered.exists():
    sys.exit(0)

with discovered.open('r', encoding='utf-8') as f:
    data = yaml.load(f) or []

for item in data:
    if isinstance(item, dict) and item.get('channel_id', '').startswith('UC'):
        print(item['channel_id'])
" | while IFS= read -r channel_id; do
        echo "--- 筛选: ${channel_id} ---"
        python tools/channel_screen.py --videos 3 "${channel_id}" 2>&1 || true
        echo ""
    done

    # 筛选完成后清空 discovered（已筛选的会进入 channels/rejected/watchlist）
    echo "清理已筛选的候选..."
    python -c "
import sys
sys.path.insert(0, '.')
from ruamel.yaml import YAML
from pathlib import Path
import re

discovered = Path('$DISCOVERED')
channels = Path('$CHANNELS')
rejected = Path('$REJECTED')
watchlist = Path('$WATCHLIST')

yaml = YAML()
if not discovered.exists():
    sys.exit(0)

with discovered.open('r', encoding='utf-8') as f:
    data = yaml.load(f) or []

# 收集所有已处理的 channel_id（出现在 channels/rejected/watchlist 中的）
processed = set()
for p in [channels, rejected, watchlist]:
    if not p.exists():
        continue
    with p.open('r', encoding='utf-8') as f:
        items = yaml.load(f) or []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get('url', ''))
        m = re.search(r'channel_id=(UC[\w-]{22})', url)
        if m:
            processed.add(m.group(1))
        cid = str(item.get('channel_id', ''))
        if cid.startswith('UC'):
            processed.add(cid)

# 保留未处理的
remaining = [d for d in data if isinstance(d, dict) and d.get('channel_id', '') not in processed]

if remaining:
    with discovered.open('w', encoding='utf-8') as f:
        yaml.dump(remaining, f)
    print(f'剩余 {len(remaining)} 个未处理候选')
else:
    discovered.unlink(missing_ok=True)
    print('所有候选已处理完毕，已清理 discovered 文件')
"
}

# ---------------------------------------------------------------------------
# Step 3: 状态报告
# ---------------------------------------------------------------------------

step_report() {
    echo "========================================"
    echo "[$(timestamp)] 频道发现 & 筛选报告"
    echo "========================================"

    local tracked discovered_count rejected_count watchlist_count
    tracked=$(count_entries "$CHANNELS")
    discovered_count=$(count_entries "$DISCOVERED")
    rejected_count=$(count_entries "$REJECTED")
    watchlist_count=$(count_entries "$WATCHLIST")

    echo ""
    echo "当前状态:"
    echo "  正在追踪:  ${tracked} 个频道"
    echo "  待筛选:    ${discovered_count} 个候选"
    echo "  观察列表:  ${watchlist_count} 个频道"
    echo "  已淘汰:    ${rejected_count} 个频道"
    echo ""

    # 生成 Markdown 报告
    mkdir -p brain
    cat > "$REPORT_FILE" << REPORT_EOF
# 频道发现报告

> 生成时间: $(timestamp)

## 概览

| 指标 | 数量 |
|------|------|
| 正在追踪 | ${tracked} |
| 待筛选 | ${discovered_count} |
| 观察列表 | ${watchlist_count} |
| 已淘汰 | ${rejected_count} |

## 正在追踪的频道

$(python -c "
from ruamel.yaml import YAML
from pathlib import Path

yaml = YAML()
p = Path('$CHANNELS')
if not p.exists():
    print('（无）')
else:
    with p.open('r', encoding='utf-8') as f:
        data = yaml.load(f) or []
    for item in data:
        if isinstance(item, dict):
            name = item.get('name', '?')
            host = item.get('host', '?')
            print(f'- **{name}** ({host})')
" 2>/dev/null || echo '（读取失败）')

## 观察列表

$(python -c "
from ruamel.yaml import YAML
from pathlib import Path

yaml = YAML()
p = Path('$WATCHLIST')
if not p.exists():
    print('（空）')
else:
    with p.open('r', encoding='utf-8') as f:
        data = yaml.load(f) or []
    if not data:
        print('（空）')
    else:
        for item in data:
            if isinstance(item, dict):
                name = item.get('name', '?')
                score = item.get('score', '?')
                reason = item.get('reason', '')
                print(f'- **{name}** (评分: {score}) {reason}')
" 2>/dev/null || echo '（读取失败）')

REPORT_EOF

    echo "报告已写入: ${REPORT_FILE}"
    echo ""
    cat "$REPORT_FILE"
}

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

ACTION="${1:-all}"

case "$ACTION" in
    discover)
        step_discover
        ;;
    screen)
        step_screen
        ;;
    report)
        step_report
        ;;
    all)
        step_discover
        step_screen
        step_report
        ;;
    *)
        echo "用法: $0 [discover|screen|report|all]"
        exit 1
        ;;
esac

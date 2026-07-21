#!/usr/bin/env bash
# codex_review.sh — 让本机 Codex CLI 独立审查指定文件夹的代码（通用版）
# 用法: bash codex_review.sh <目标文件夹> [输出文件] [--tui|--watch]
#   --tui    弹出 Codex 原生对话框(TUI)跑任务：全程原生渲染，跑完可直接在框里追问。
#            脚本立即返回并打印会话文件路径；用 codex_last_report.py --wait 提取报告。
#   --watch  弹出终端窗口 tail 日志（exec 模式，纯旁观）
#   （不带旗标 = exec 模式前台/后台跑，输出 tee 到日志）
# 产物: exec 模式 → <输出文件>.md + .log；TUI 模式 → 会话文件路径（stdout 最后一行）
set -euo pipefail

TARGET="${1:?用法: codex_review.sh <目标文件夹> [输出文件] [--tui|--watch]}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${2:-$(pwd)/codex_review_${STAMP}.md}"
case "${2:-}" in --tui|--watch) OUT="$(pwd)/codex_review_${STAMP}.md";; esac
LOG="${OUT%.md}.log"
MODE="exec"
for a in "$@"; do
  [ "$a" = "--watch" ] && MODE="watch"
  [ "$a" = "--tui" ] && MODE="tui"
done
[ "${WATCH_CODEX:-0}" = "1" ] && [ "$MODE" = "exec" ] && MODE="watch"

if [ ! -d "$TARGET" ]; then
  echo "[错误] 目标文件夹不存在: $TARGET" >&2
  exit 1
fi
mkdir -p "$(dirname "$OUT")"

PROMPT=$(cat <<'EOF'
你是一名独立的代码审查员。请递归审查当前工作目录下的所有源代码文件（忽略 __pycache__、venv、node_modules、.egg-info、二进制与数据文件）。只审查，不要修改任何文件。

输出要求（用中文、Markdown 格式）：
1. 按【高】【中】【低】三档列出问题：
   - 高：会导致错误结果、崩溃、数据损坏或安全漏洞
   - 中：潜在 bug、明显的健壮性或性能缺陷
   - 低：可维护性、风格与小改进
2. 每条问题必须包含：文件相对路径、行号、问题描述、判定理由（为什么这是问题）。
3. 只报告有把握的问题，不要猜测；某档位没有问题就写"无"。
4. 严格使用如下格式：

## 高
- `文件路径:行号` 问题描述 —— 理由

## 中
- `文件路径:行号` 问题描述 —— 理由

## 低
- `文件路径:行号` 问题描述 —— 理由
EOF
)

MINTTY="/c/Program Files/Git/usr/bin/mintty.exe"
SESS_GLOB="$HOME/.codex/sessions/*/*/*/*.jsonl"

# ============ TUI 模式：在 Codex 原生对话框里跑 ============
if [ "$MODE" = "tui" ]; then
  if [ ! -x "$MINTTY" ]; then
    echo "[错误] 未找到 mintty（$MINTTY），无法弹出 TUI 窗口" >&2
    exit 1
  fi
  # 提示词经临时文件传递，避免多行中文引号问题
  PF="$(mktemp --suffix=.codex_prompt.txt)"
  printf '%s' "$PROMPT" > "$PF"
  # 快照启动前最新会话，用于识别新会话文件
  BEFORE="$(ls -t $SESS_GLOB 2>/dev/null | head -1 || true)"

  "$MINTTY" --title "Codex 对话框 - 任务自动进行中，可随时在框内输入追问" \
    -o Columns=150 -o Rows=45 -e bash -lc \
    "codex -s read-only -a never -C '$TARGET' \"\$(cat '$PF')\"" &
  # 注: TUI 不支持 --skip-git-repo-check（exec 专属）；非 git 目录会在对话框内弹确认，选择继续即可

  echo "[codex_review] 已弹出 Codex 原生对话框（read-only + 全自动，无需审批）"
  echo "[codex_review] 等待新会话文件出现..."
  NEW=""
  for _ in $(seq 1 30); do
    sleep 2
    NEW="$(ls -t $SESS_GLOB 2>/dev/null | head -1 || true)"
    [ -n "$NEW" ] && [ "$NEW" != "$BEFORE" ] && break
    NEW=""
  done
  if [ -z "$NEW" ]; then
    echo "[错误] 60 秒内未见新会话文件，TUI 可能启动失败" >&2
    exit 1
  fi
  echo "[codex_review] 会话文件: $NEW"
  echo "[codex_review] 提取报告: python \"$(dirname "$0")/codex_last_report.py\" \"$NEW\" --wait 1800 -o \"$OUT\""
  echo "$NEW"
  exit 0
fi

# ============ exec / watch 模式 ============
: > "$LOG"
if [ "$MODE" = "watch" ]; then
  if [ -x "$MINTTY" ]; then
    "$MINTTY" --title "Codex 实时交互 - 任务结束后可关闭本窗口" \
      -o Columns=140 -o Rows=40 -e bash -c "tail -f '$LOG'" &
    echo "[codex_review] 已弹出实时观看窗口"
  else
    echo "[codex_review] 未找到 mintty，手动观看: tail -f \"$LOG\""
  fi
fi

echo "[codex_review] 目标: $TARGET"
echo "[codex_review] 报告: $OUT"
echo "[codex_review] 实时日志: $LOG   （观看: tail -f \"$LOG\"）"

set +e
codex exec \
  --skip-git-repo-check \
  -s read-only \
  -C "$TARGET" \
  -o "$OUT" \
  "$PROMPT" 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
set -e

{
  echo ""
  echo "==================== 任务已结束 (exit=$RC)，本窗口可关闭 ===================="
} >> "$LOG"

echo ""
if [ "$RC" -eq 0 ]; then
  echo "[codex_review] 完成，报告已写入: $OUT"
else
  echo "[codex_review] codex 退出码 $RC，检查日志: $LOG" >&2
fi
exit "$RC"

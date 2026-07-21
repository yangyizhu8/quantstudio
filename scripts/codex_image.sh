#!/usr/bin/env bash
# codex_image.sh — 让本机 Codex CLI 用内置图片生成工具出图（通用版）
# 用法: bash codex_image.sh "<画面描述>" [宽x高] [输出文件夹] [--watch]
#   --watch  弹出独立终端窗口实时显示 Codex 交互过程（后台代跑场景用）
# 前提: codex 使用 OpenAI 官方后端（内置 image_gen 工具仅官方后端加载）
set -euo pipefail

DESC="${1:?用法: codex_image.sh \"<画面描述>\" [宽x高] [输出文件夹] [--watch]}"
SIZE="${2:-1920x1080}"
[ "${SIZE}" = "--watch" ] && SIZE="1920x1080"
OUTDIR="${3:-$(pwd)/codex_images}"
[ "${OUTDIR}" = "--watch" ] && OUTDIR="$(pwd)/codex_images"
STAMP="$(date +%Y%m%d_%H%M%S)"
FNAME="codex_img_${STAMP}.png"
MSGFILE="$OUTDIR/.last_msg_${STAMP}.txt"
LOG="$OUTDIR/.codex_image_${STAMP}.log"
WATCH=0
for a in "$@"; do [ "$a" = "--watch" ] && WATCH=1; done
[ "${WATCH_CODEX:-0}" = "1" ] && WATCH=1

mkdir -p "$OUTDIR"
: > "$LOG"

if [ "$WATCH" = "1" ]; then
  MINTTY="/c/Program Files/Git/usr/bin/mintty.exe"
  if [ -x "$MINTTY" ]; then
    "$MINTTY" --title "Codex 实时交互 - 任务结束后可关闭本窗口" \
      -o Columns=140 -o Rows=40 -e bash -c "tail -f '$LOG'" &
    echo "[codex_image] 已弹出实时观看窗口"
  else
    echo "[codex_image] 未找到 mintty，手动观看: tail -f \"$LOG\""
  fi
fi

PROMPT="调用你内置的图片生成工具（image generation）生成一张图片。
画面内容：${DESC}
分辨率：${SIZE}（按此像素比例输出，尽可能接近该分辨率）
把生成的图片保存到当前工作目录，文件名：${FNAME}
完成后只回复图片文件的绝对路径这一行文字，不要任何其他内容。"

echo "[codex_image] 描述: $DESC"
echo "[codex_image] 分辨率: $SIZE"
echo "[codex_image] 输出目录: $OUTDIR"
echo "[codex_image] 实时日志: $LOG   （观看: tail -f \"$LOG\"）"

set +e
codex exec \
  --skip-git-repo-check \
  -s workspace-write \
  -C "$OUTDIR" \
  -o "$MSGFILE" \
  "$PROMPT" 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
set -e

{
  echo ""
  echo "==================== 任务已结束 (exit=$RC)，本窗口可关闭 ===================="
} >> "$LOG"

echo ""
if [ -f "$OUTDIR/$FNAME" ]; then
  echo "[codex_image] 生成成功: $OUTDIR/$FNAME"
else
  echo "[codex_image] 未在预期路径发现图片，Codex 回复如下:" >&2
  cat "$MSGFILE" >&2 2>/dev/null || true
  exit 1
fi

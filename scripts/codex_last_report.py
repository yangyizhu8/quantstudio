#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""codex_last_report.py — 从 Codex 会话存档(JSONL)提取最终回复

用法:
  python codex_last_report.py                     # 最新会话，报告打印到 stdout
  python codex_last_report.py <session.jsonl>     # 指定会话
  python codex_last_report.py <session.jsonl> -o report.md --wait 1800
    --wait N   轮询等待 task_complete 事件出现（秒），用于 TUI 模式下等任务跑完
    -o FILE    报告写入文件（UTF-8）

设计场景: codex 以 TUI 模式在独立窗口跑任务（用户在原生对话框观看/干预），
本脚本从 ~/.codex/sessions/ 的会话存档中提取最终报告给调用方（Claude）。
"""
import argparse
import glob
import json
import os
import sys
import time

SESS_ROOT = os.path.expanduser("~/.codex/sessions")


def latest_session():
    files = glob.glob(os.path.join(SESS_ROOT, "**", "*.jsonl"), recursive=True)
    if not files:
        sys.exit("[错误] 未找到任何会话文件: " + SESS_ROOT)
    return max(files, key=os.path.getmtime)


def scan(path):
    """返回 (最后一条 assistant 文本, 是否出现 task_complete)"""
    last_text, complete = None, False
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            p = rec.get("payload") or {}
            if p.get("type") == "task_complete":
                complete = True
            if p.get("type") == "message" and p.get("role") == "assistant":
                texts = [c.get("text", "") for c in p.get("content", [])
                         if isinstance(c, dict) and c.get("type") == "output_text"]
                if texts:
                    last_text = "\n".join(texts)
    return last_text, complete


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", help="会话 jsonl 路径（缺省=最新）")
    ap.add_argument("-o", "--output", help="报告输出文件")
    ap.add_argument("--wait", type=int, default=0,
                    help="等待 task_complete 的秒数（0=不等待，直接取当前内容）")
    args = ap.parse_args()

    path = args.session or latest_session()
    if not os.path.isfile(path):
        sys.exit("[错误] 会话文件不存在: " + path)
    print("[codex_last_report] 会话: " + path, file=sys.stderr)

    deadline = time.time() + args.wait
    text, complete = scan(path)
    while args.wait and not complete and time.time() < deadline:
        time.sleep(10)
        text, complete = scan(path)

    if args.wait and not complete:
        print("[警告] 等待 %ds 后仍未见 task_complete，输出当前已有内容" % args.wait,
              file=sys.stderr)
    if not text:
        sys.exit("[错误] 会话中尚无 assistant 回复（任务可能刚启动或已失败）")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print("[codex_last_report] 报告已写入: " + args.output, file=sys.stderr)
        print(args.output)
    else:
        # stdout 可能是 GBK 控制台，按其编码安全输出
        enc = sys.stdout.encoding or "utf-8"
        sys.stdout.write(text.encode(enc, "replace").decode(enc))
        sys.stdout.write("\n")
    print("[codex_last_report] task_complete=%s" % complete, file=sys.stderr)


if __name__ == "__main__":
    main()

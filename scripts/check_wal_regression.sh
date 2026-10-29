#!/bin/bash
# check_wal_regression.sh — check_wal.sh 回归矩阵 T1-T9（macOS / Linux / Git Bash 通用）
#
# 用法：bash scripts/check_wal_regression.sh [--keep]
#       环境变量 PY：解释器（默认 python3；需 duckdb 1.4.x）
# 行为：在临时目录构建合成 fixture 并逐用例运行 check_wal.sh，PASS/FAIL/SKIP 汇总；
#       任一 FAIL → exit 1。全程只操作临时目录，绝不触碰生产库。
# 平台：macOS 使用真实 stat/lsof/cp；其他平台注入 PATH shim（stat -f%z / lsof / cp -c）。
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$REPO/check_wal.sh"
PY="${PY:-python3}"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

PASS=0; FAIL=0; SKIP=0
TMP="$(mktemp -d)"
cleanup() {
  if [ "$KEEP" -eq 1 ]; then echo "保留临时目录: $TMP"; else rm -rf "$TMP"; fi
}
trap cleanup EXIT

# 非 Darwin：注入命令 shim（让 macOS 语法的 check_wal.sh 可在 Linux/Git Bash 跑通）
# 关键：shim 内部必须用「真实二进制绝对路径」，否则 exec stat/cp 会递归解析回 shim 自身。
SHIM="$TMP/shim"; mkdir -p "$SHIM"
if [ "$(uname -s)" != "Darwin" ]; then
  REAL_STAT="$(command -v stat)"
  REAL_CP="$(command -v cp)"
  printf '#!/bin/sh\nif [ "$1" = "-f%%z" ]; then exec %s -c%%s "$2"; fi\nexec %s "$@"\n' "$REAL_STAT" "$REAL_STAT" > "$SHIM/stat"
  printf '%s\n' '#!/bin/sh' 'exit 1' > "$SHIM/lsof"
  printf '#!/bin/sh\n[ "$1" = "-c" ] && shift\nexec %s "$@"\n' "$REAL_CP" > "$SHIM/cp"
  chmod +x "$SHIM/stat" "$SHIM/lsof" "$SHIM/cp"
  PATH="$SHIM:$PATH"
fi

# 构建 fixture（Windows Git Bash 下把 /tmp 路径转成 Windows 路径给原生 python）
if command -v cygpath >/dev/null 2>&1; then WIN_TMP="$(cygpath -w "$TMP")"; else WIN_TMP="$TMP"; fi
"$PY" "$REPO/scripts/check_wal_fixture.py" --out "$WIN_TMP/fixtures" || { echo "!! fixture 构建失败"; exit 1; }
FIX="$TMP/fixtures"

# 版本门伪造 shim：1.5.5（放行集合外，应拒跑）与 1.5.6（放行集合内，应放行）
mkdir -p "$TMP/fakeshim_bad/duckdb" "$TMP/fakeshim_ok/duckdb"
printf '%s\n' '__version__ = "1.5.5"' > "$TMP/fakeshim_bad/duckdb/__init__.py"
printf '%s\n' '__version__ = "1.5.6"' > "$TMP/fakeshim_ok/duckdb/__init__.py"

ok() { PASS=$((PASS+1)); echo "PASS  $1"; }
ng() { FAIL=$((FAIL+1)); echo "FAIL  $1"; }
sk() { SKIP=$((SKIP+1)); echo "SKIP  $1"; }

LAST_OUT=""; LAST_CODE=0; LAST_WORK=""
run_case() {
  # $1 name  $2 expect_code  $3 fixture  [extra args...]
  local name="$1" expect="$2" fixture="$3"; shift 3
  local work="$TMP/work_$name"
  rm -rf "$work"; mkdir -p "$work/data"
  cp -f "$TARGET" "$work/check_wal.sh"
  # 判定源随脚本一并复制（check_wal.sh 以相对路径单文件直调它）
  mkdir -p "$work/quantstudio/pipeline"
  cp -f "$REPO/quantstudio/pipeline/duckdb_version_policy.py" "$work/quantstudio/pipeline/"
  if [ -d "$FIX/$fixture/data" ]; then
    cp -f "$FIX/$fixture/data/"* "$work/data/" 2>/dev/null || true
  fi
  LAST_OUT="$( cd "$work" && PY="$PY" PYTHONPATH="${CASE_PYTHONPATH:-}" bash "$work/check_wal.sh" "$@" 2>&1 )"
  LAST_CODE=$?
  LAST_WORK="$work"
  echo "$LAST_OUT" > "$work/last_output.txt"
}

want() {  # want <case> <expect_code> <must_contain|-> <must_not_contain|->
  local name="$1" expect="$2" must="$3" not="$4"
  local bad=0
  [ "$LAST_CODE" -eq "$expect" ] || { echo "  期望 exit=$expect 实际=$LAST_CODE"; bad=1; }
  if [ "$must" != "-" ] && ! printf '%s' "$LAST_OUT" | grep -qF "$must"; then
    echo "  缺少输出片段: $must"; bad=1
  fi
  if [ "$not" != "-" ] && printf '%s' "$LAST_OUT" | grep -qF "$not"; then
    echo "  出现不应有的输出片段: $not"; bad=1
  fi
  if [ "$bad" -eq 0 ]; then ok "$name"; else ng "$name"; fi
}

wal_bytes() {  # $1 work dir → WAL 字节数（不存在=0）
  local w="$1/data/quantstudio.db.wal"
  if [ -f "$w" ]; then wc -c < "$w" | tr -d ' '; else echo 0; fi
}

# T1 版本门：伪造 duckdb 1.5.5（放行集合外）→ 拒跑
CASE_PYTHONPATH="$TMP/fakeshim_bad" run_case t1_version_gate 1 healthy
CASE_PYTHONPATH=""
want t1_version_gate 1 "不在项目放行集合" "CHECKPOINT"

# T1b 版本门：伪造 duckdb 1.5.6（放行集合内）→ 版本门**不得拦截**
#   注：伪造模块只有 __version__、没有 connect，故放行后必然在只读探测/CHECKPOINT 阶段失败
#   （exit 1）。本用例断言的是「版本门已放行」，不是「整流程成功」。
CASE_PYTHONPATH="$TMP/fakeshim_ok" run_case t1b_version_gate 1 healthy
CASE_PYTHONPATH=""
want t1b_version_gate 1 "duckdb = 1.5.6" "不在项目放行集合"

# T2 健康库（无 WAL）→ 早退 0
run_case t2_healthy 0 healthy
want t2_healthy 0 "无需处理" "WAL 仍残留"

# T3 合法小 WAL 残留 → 探测通过 + 早退 0 + 提示残留
run_case t3_legit_wal 0 legit_wal
want t3_legit_wal 0 "WAL 仍残留" "CHECKPOINT 完成"

# T4 外来/损坏 WAL：探测失败 → CHECKPOINT 失败 → 非零退出，不打印完成
run_case t4_foreign_default 1 foreign_wal
want t4_foreign_default 1 "CHECKPOINT 失败" "完成（WAL 已收敛"

# T5 --force 合法 WAL → 收敛成功，WAL 归零
run_case t5_force_legit 0 legit_wal --force
want t5_force_legit 0 "CHECKPOINT 完成" "CHECKPOINT 失败"
[ "$(wal_bytes "$LAST_WORK")" -eq 0 ] && ok t5_wal_zeroed || ng t5_wal_zeroed

# T6 --force 无 WAL（TS 未定义回归路径）→ 0，不崩
run_case t6_force_no_wal 0 healthy --force
want t6_force_no_wal 0 "已备份" "unbound variable"

# T7 未知参数 → 拒绝
run_case t7_bad_arg 1 healthy --bogus
want t7_bad_arg 1 "未知参数" "CHECKPOINT"

# T8 --recover-stale-wal：CHECKPOINT 失败后移走陈旧 WAL → 0，orphan 存在，修复闭环打印完成
run_case t8_recover 0 foreign_wal --recover-stale-wal
want t8_recover 0 "orphan" "-"
printf '%s' "$LAST_OUT" | grep -qF "完成（WAL 已收敛" && ok t8_completed || ng t8_completed
if ls "$LAST_WORK/data/"*.orphan.* >/dev/null 2>&1; then ok t8_orphan_created; else ng t8_orphan_created; fi

# T9 缺库目录 → 拒绝
run_case t9_missing_db 1 missing
want t9_missing_db 1 "未找到" "CHECKPOINT"

echo "------------------------------------------"
echo "结果: PASS=$PASS FAIL=$FAIL SKIP=$SKIP"
[ "$FAIL" -eq 0 ] || exit 1

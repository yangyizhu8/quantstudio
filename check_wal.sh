#!/bin/bash
# check_wal.sh — QuantStudio 数据库 WAL 巡检、探测与收敛（macOS，幂等）
# v3（2026-10-19 客户实弹修复）：
#   ① 探测优先：不论 WAL 大小，先尝试只读打开探测；打不开即判定"需收敛"，
#      256MB 仅作性能阈值（不再作为唯一门槛）；
#   ② duckdb 版本门：放行集合 = 1.4.5 ≤ x < 1.5 或 x == 1.5.6（判定源唯一：
#      quantstudio/pipeline/duckdb_version_policy.py，本脚本只调用不自持规则）；
#   ③ 日期列自动探测：stock_daily 实为 time 列（非 trade_date）；
#   ④ 失败即中断：CHECKPOINT 非零退出不打印"完成"，给出明确指引；
#   ⑤ bash 3.2 兼容：变量一律 ${VAR} 花括号界定（$VAR 后紧跟中文字符在
#      macOS 默认 bash 3.2 会误判变量名，set -u 下报 unbound variable）；
#   ⑥ 早退提示：探测通过且 WAL ≤ 阈值时打印"WAL 仍残留"提示（早退 ≠ 已收敛，
#      彻底收敛请加 --force）。
#
# 用法：
#   bash check_wal.sh                        # 放在项目根目录运行（脚本自行 cd 到自身目录）
#   PY=./.venv/bin/python bash check_wal.sh  # 指定项目 venv 解释器（默认 python3）
#   bash check_wal.sh --force                # 跳过"无需处理"早退，强制执行收敛
#   bash check_wal.sh --recover-stale-wal    # 库打不开且 CHECKPOINT 失败时：备份后移走陈旧 WAL
# 铁律：写者进程运行中禁止 CHECKPOINT；禁止手工删除 .wal（本脚本移走陈旧 WAL 前已克隆备份）。

set -u
cd "$(dirname "$0")" || exit 1
DB="data/quantstudio.db"
WAL="$DB.wal"
PY=${PY:-python3}
THRESHOLD=$((256*1024*1024))
FORCE=0
RECOVER=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    --recover-stale-wal) RECOVER=1 ;;
    *) echo "!! 未知参数: $a"; exit 1 ;;
  esac
done
fail() { echo "!! $*"; exit 1; }

[ -f "$DB" ] || fail "未找到 ${DB}，请把本脚本放在项目根目录后运行"
command -v "$PY" >/dev/null 2>&1 || fail "找不到解释器 ${PY}（可尝试 PY=./.venv/bin/python bash check_wal.sh）"

echo "== 1. duckdb 版本门（项目放行集合：1.4.5 ≤ x < 1.5 或 x == 1.5.6）=="
VER=$("$PY" -c "import duckdb; print(duckdb.__version__)" 2>/dev/null) || fail "无法导入 duckdb（用 PY=./.venv/bin/python 指定项目解释器）"
echo "duckdb = $VER"
GATE_SRC="quantstudio/pipeline/duckdb_version_policy.py"
[ -f "$GATE_SRC" ] || fail "找不到判定源 ${GATE_SRC}（请把本脚本与仓库一起放置）"
"$PY" "$GATE_SRC" --check "$VER" >/dev/null 2>&1
GATE_RC=$?
if [ "$GATE_RC" -ne 0 ]; then
  fail "duckdb ${VER} 不在项目放行集合（1.4.5 ≤ x < 1.5 或 x == 1.5.6；判定源 ${GATE_SRC}）。
   修复：pip install \"duckdb==1.5.6\"，或 PY=./.venv/bin/python bash check_wal.sh"
fi

echo "== 2. 写者进程检查 =="
if lsof "$(pwd)/$DB" >/dev/null 2>&1; then
  fail "有进程持有数据库（GUI/daemon 正在使用）——请先关闭 GUI 并停止采集进程，再重跑本脚本"
fi
echo "无进程持有 ✓"

echo "== 3. WAL 体积 =="
if [ -f "$WAL" ]; then
  SIZE=$(stat -f%z "$WAL")
  echo "WAL = $SIZE 字节（$((SIZE/1024/1024)) MB），性能阈值 256MB"
else
  SIZE=0
  echo "WAL 不存在"
fi

echo "== 4. 打开探测（只读，不改变数据）=="
PROBE=0
if "$PY" - <<'PYCODE' >/dev/null 2>&1
import duckdb
c = duckdb.connect("data/quantstudio.db", read_only=True)
c.execute("SELECT 1").fetchone()
c.close()
PYCODE
then
  PROBE=1
  echo "只读探测通过 ✓"
else
  echo "只读探测失败（WAL 残留不匹配或库状态异常，判定需收敛）"
fi

if [ "$PROBE" -eq 1 ] && [ "$SIZE" -le "$THRESHOLD" ] && [ "$FORCE" -eq 0 ]; then
  echo "库可正常打开且 WAL 低于阈值，无需处理 ✓"
  if [ -f "$WAL" ]; then
    echo "提示：WAL 仍残留（$(stat -f%z "$WAL") 字节），只读访问无法自愈；如需彻底收敛请加 --force 重跑"
  fi
  exit 0
fi

echo "== 5. APFS 克隆备份 =="
TS=$(date +%Y%m%d_%H%M%S)
cp -c "$DB" "$DB.bak.$TS" || fail "备份主库失败（磁盘空间不足？）"
if [ -f "$WAL" ]; then
  cp -c "$WAL" "$WAL.bak.$TS" || fail "备份 WAL 失败"
  echo "已备份: ${DB}.bak.${TS} / ${WAL}.bak.${TS}"
else
  echo "已备份: ${DB}.bak.${TS}（无 WAL）"
fi

echo "== 6. CHECKPOINT 收敛（大 WAL 时需数分钟，请耐心等待）=="
if ! "$PY" - <<'PYCODE'
import duckdb, time
t = time.time()
c = duckdb.connect("data/quantstudio.db")
c.execute("CHECKPOINT")
c.close()
print("CHECKPOINT 完成 %.1fs" % (time.time() - t))
PYCODE
then
  if [ "$RECOVER" -eq 1 ] && [ -f "$WAL" ]; then
    echo "!! CHECKPOINT 失败（WAL 与主库不匹配/损坏）。按 --recover-stale-wal 处理：备份已存在，移走陈旧 WAL 后重验"
    mv "$WAL" "${WAL}.orphan.${TS}" || fail "移走陈旧 WAL 失败"
    echo "陈旧 WAL 已移至 ${WAL}.orphan.${TS}（如怀疑 WAL 内有未合并写入，请保留该文件并联系技术支持）"
  else
    fail "CHECKPOINT 失败：疑似 WAL 陈旧/损坏（详见上方 Python 报错）。
   处置：确认无进程占用后，加 --recover-stale-wal 重跑（备份后移走陈旧 WAL）；或联系技术支持"
  fi
fi

echo "== 7. 验证 =="
NEWWAL=$(stat -f%z "$WAL" 2>/dev/null || echo 0)
echo "WAL 收敛后 = $NEWWAL 字节"
"$PY" - <<'PYCODE' || fail "验证阶段失败（详见上方报错）"
import time, duckdb
t = time.time()
c = duckdb.connect("data/quantstudio.db", read_only=True)
try:
    print("trade_calendar rows:", c.execute("SELECT COUNT(*) FROM trade_calendar").fetchone()[0])
except Exception as e:
    print("（trade_calendar 不可用，跳过）", type(e).__name__)
try:
    cols = [r[1] for r in c.execute("PRAGMA table_info('stock_daily')").fetchall()]
    date_col = next((x for x in ("trade_date", "time", "date") if x in cols), None)
    if date_col:
        q = 'SELECT MAX("%s") FROM stock_daily' % date_col
        print("stock_daily last %s:" % date_col, c.execute(q).fetchone()[0])
    else:
        print("（stock_daily 无日期列，跳过）")
except Exception as e:
    print("（stock_daily 抽查失败，跳过）", type(e).__name__)
c.close()
print("只读打开+查询 %.2fs" % (time.time() - t))
PYCODE

echo "== 完成（WAL 已收敛，库可正常打开）=="

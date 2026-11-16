# 合成验证申请单 · 空库判据（firefly 现场恢复流程 v14）· **rev4**

> 编号：**SYN-20261116-01 rev4**（设计审候选；取代 rev3——失败传播闭合 / 提取范围修正 / 辅助写面）
> 出具：2026-11-16 02:5x（Asia/Shanghai）　任务约定：**DRIFT-N17-r1**（阶段＝设计审）
> 依据判据：总调度 **N17-V1 / V2 / V3**（VER 须比较、B0 失败必停、逐例断言累计、端到端覆盖、写面与边界可核）
> ＋ **复核三条**：B0 **查询异常**亦须立即停（不得被通用异常处理记 UNVERIFIED 后继续跑）／环境命令须补 **WORK 建根 · PROBE 原样提取 · hash 门**／`Select-String` 的禁路径检查命令须**真能执行**（`-SimpleMatch` 配竖线正则串 OR 失效，恒 0 命中）；一并修正 `total=11` 的构成表述与 C7 口径
> ＋ **关闭判据三条**：①每个原生命令后**存并判 `$LASTEXITCODE`**，venv/pip/prepare/版本断言任一失败即停外层，**不进 matrix**；`Tee` 日志**不得遮盖** Python 退出码（交叉核对）／②**精确固定源 hash 与完整围栏范围**——D 只是完整 A—E 脚本的内部阶段，**不能只抽 D 当被测件**／③prepare 与自检临时文件**列入辅助写面**
> 对位流程件：`docs/handoff/firefly-site-recovery-procedure-20261114.md` **v14**
> **状态：未运行**。本单**不主张**任何读数已取得；`venv`/合成库/日志**均不存在**。

---

## 0. rev1 → rev2 → rev3 的整改对照（先看这个）

| 判据 | rev1 的缺陷 | rev2 的做法 |
|---|---|---|
| **N17-V1** 断言 | `show()` 打印 `expect=` 字符串；`run_probe()` 也只打印 `expect`；异常 `return` 后无计数；末尾恒 `0` | **断言引擎** `check()`：逐例比较 `rc` + 必含片段 + 禁含片段 → `MATCH/DIFF`；异常 → `UNVERIFIED`；**累计三类**；**DIFF 或 UNVERIFIED > 0 ⇒ 退出码 1**；`VER` 与目标**字符串比较**，不符即 `EXIT 4` |
| **N17-V1** 基线 | B0 只是「打印 total」 | **B0 非 0 ⇒ 立即 `EXIT 5`**，写 `b0_surface.json` 留证，**后续用例一个不跑** |
| **N17-V2** 端到端 | C5c/C5c2/C5c3/C5f/C5g 只跑 `surface()`（不做 `restore_probe`） | **10 个用例全部经真实 `restore_probe.py` 端到端**（B0 只做 `surface` 基线）；`surface()` 降级为**诊断输出**（`by_source` 明细），断言一律以 probe 输出为准 |
| **N17-V3** 写面安全 | `fresh()` 无条件 `rmtree`；无 run 隔离；失败可当场改过滤器重跑 | ①`sub()` 删除前**三段断言**（在 `RUN` 下、非 `RUN` 本身、非 `WORK`）；②**每次运行独立 `RUN` 目录**，旧 run **保留不删**；③**同一次运行内禁止自愈**（判据失败即记 DIFF 并继续/停止，改过滤器必须**新 run**）；④§3 **完整写面清单**；⑤零触碰改为**路径与调用链限定**（不靠 hash 推论） |
| **V1′** B0 异常 | rev2 的 B0 走通用 `case()`：`surface()` 抛错 ⇒ 记 `UNVERIFIED` ⇒ **后续用例照跑** | **B0 脱离通用 `case()`**：`mkdb/surface` 异常 ⇒ `b0_surface_error.json`（含 traceback）+ `summary.json(stop_reason=B0_UNPROBED_BASELINE)` ⇒ **`EXIT 7`，一个用例都不跑**；基线非 0 ⇒ `b0_surface.json` + `summary.json(stop_reason=B0_BASELINE_NONZERO)` ⇒ `EXIT 5` |
| **V3′** 准备链 | 环境命令缺 **WORK 建根**、**PROBE 原样提取**、**hash 门**；`Select-String` 用 `-SimpleMatch` 配 `'a|b'` 竖线串 ⇒ OR 语义失效 ⇒ **恒 0 命中（假绿）** | 新增 **§2.1 准确准备链**（建根 → venv/依赖 → **原样提取 + `PROBE_SHA256`** → 版本断言 → 运行）；§9 改为**数组 pattern**（默认真则 OR）＋**鉴别力自检**（先证明命令能命中）；矩阵脚本启动即打印同一 `PROBE_SHA256` 并写入 `summary.json` |
| **表述** | 「11 例全部端到端」「22 次 probe 调用」 | **`total=11` = B0（1 次 `surface` 基线）+ 10 个 `restore_probe` 端到端例**；C7 与 C2 **同口径**（同样核 `objects=0` 与 `by_source=`） |
| **失败传播** | 准备链命令未存/未判 `$LASTEXITCODE`；`Tee-Object` 之后可能「日志里有失败、外层当成功」 | **`Step` 包裹每步**（存 `$rc` → 判 → 非 0 即 `exit $rc`）：`venv`/`pip`/`prepare`/`version` 任一失败**绝不进入 matrix**；matrix 运行后**紧接**存 `$rc`，并与日志末行 `EXIT n` **交叉核对**，不等 ⇒ `EXIT 9` |
| **提取范围** | rev3 写「§2.4.1 **阶段 D** 的围栏」——**D 只是完整 A—E 脚本的内部阶段**（依赖 A/B/C 的解析与连接），抽 D 不是被测件 | 改为 **L115 ```python → L277 ``` 的整段 A—E 围栏（体 161 行）**，并冻结**源 sha256**（`7EBDE2EE…06702` / 90,003 B）+ 三道门（源 / 件 / 互核） |
| **辅助写面** | `_prepare_probe.py` 与 §9 自检临时文件未列入写面 | §3 补 **#6 prepare 脚本 · #7 prepare 日志 · #8 `%TEMP%` 自检临时文件**（成功即删、失败保留供复核） |

---

## 1. 目标（可断言，不靠自述）

在 **1.5.6 与 1.4.5 两个独立环境**内，对**合成最小空库**执行 **11 项**（**B0 一次 `surface` 基线 + 10 个 `restore_probe` 端到端例**），得到：

1. `VER actual == EXPECT_VER`（字符串相等，否则整轮中止）；
2. **B0 基线 = 0**（六源合计）；**非 0 ⇒ `EXIT 5` 立即停**，**查询异常 ⇒ `EXIT 7` 立即停**——两种情况都**不跑后续用例**，且都留 `summary.json`；
3. 每例 `rc` 与输出片段**逐条匹配**；
4. 终态：`diff=0 且 unverified=0 且 total=11` ⇒ **退出码 0**；否则 **退出码 1**（`total` 的 11 = B0 + 10 个 probe 例，B0 只做一次 `surface`，不经 probe）。

**本单不产出**「恢复流程已通过验证」这类结论——只产出上述四个可核事实。

---

## 2. 环境隔离与版本断言

| 项 | 1.5.6 | 1.4.5 |
|---|---|---|
| venv | `.specify/bugs/firefly-engine-defects-20261025/.venv_156` | `.../.venv_145` |
| 解释器 | `...\.venv_156\Scripts\python.exe` | `...\.venv_145\Scripts\python.exe` |
| 期望版本（argv[1]） | `1.5.6` | `1.4.5` |
| 写面根（argv[2]） | `.../_iso_empty_db_156` | `.../_iso_empty_db_145` |
| 日志 | `.../_iso_emptydb_1_5_6.txt` | `.../_iso_emptydb_1_4_5.txt` |

### 2.1 准确准备链（**失败传播闭合** · 五步）

> **铁律**：每个原生命令后**立即** `$rc = $LASTEXITCODE`（先存后判——任何后续原生命令都会覆盖它），`$rc -ne 0` 即 `exit $rc`。**venv / pip / prepare / 版本断言任一失败，绝不进入 matrix。** `Tee-Object` 是 cmdlet、不改 `$LASTEXITCODE`，但取值必须**紧接**在管道之后；并**交叉核对**日志末行 `EXIT n` 与 `$rc` 是否相等，不等即判「退出码被遮盖」。

```powershell
$ErrorActionPreference = 'Stop'
$D   = '.specify\bugs\firefly-engine-defects-20261025'
$PY  = "$D\.venv_156\Scripts\python.exe"
$LOG = "$D\_iso_emptydb_1_5_6.txt"

function Step($name, [scriptblock]$body) {
    & $body
    $rc = $LASTEXITCODE                 # ← 先存：任何后续原生命令都会覆盖它
    if ($rc -ne 0) { Write-Host "STEP_FAILED $name rc=$rc"; exit $rc }
    Write-Host "STEP_OK $name rc=0"
}

# ① 建根（cmdlet；$ErrorActionPreference=Stop 保证失败即抛并终止外层）
New-Item -ItemType Directory -Force "$D\_iso_empty_db_156","$D\_iso_empty_db_145" | Out-Null

# ② 独立环境 + 依赖（任一失败即停，不进后续）
Step 'venv' { python -m venv "$D\.venv_156" }
Step 'pip'  { & $PY -m pip install -q "duckdb==1.5.6" }

# ③ 原样提取 + 源 hash 门（失败即停）
Step 'prepare' { & $PY "$D\_prepare_probe.py" }

# ④ 版本断言（不满足即停，退出码 4）
Step 'version' { & $PY -c "import duckdb,sys;print('VER',duckdb.__version__);sys.exit(0 if duckdb.__version__=='1.5.6' else 4)" }

# ⑤ 运行（Tee 落日志；**紧接**存退出码，并与日志末行 EXIT 交叉核对）
& $PY "$D\_iso_empty_db_matrix.py" 1.5.6 "$D\_iso_empty_db_156" 2>&1 | Tee-Object -FilePath $LOG
$rc   = $LASTEXITCODE
$last = (Get-Content $LOG -Tail 1).Trim()
if ("EXIT $rc" -ne $last) { Write-Host "EXITCODE_MISMATCH rc=$rc log_tail=$last"; exit 9 }
exit $rc
```

要点：
- `Step` 把「存 `$rc` → 判 → 停」固化为**一个动作**，避免四步里漏判某一步；
- matrix 输出经 `Tee-Object` 落日志后**立刻**取 `$LASTEXITCODE`，中间**不得插入任何其它原生命令**；
- **交叉核对** `"EXIT $rc"` == 日志末行：matrix 自己会打印 `EXIT n`；两者不等 ⇒ `EXIT 9`（专治「日志显示失败、外层却判成功」的遮盖）。

（1.4.5 同构：`.venv_145` / `1.4.5` / `_iso_empty_db_145` / `_iso_emptydb_1_4_5.txt`。）

**③ 的提取范围与冻结值（rev4 修正：被测件**不是** D 段）**

| 项 | 值 | 依据 |
|---|---|---|
| 源文件 | `docs/handoff/firefly-site-recovery-procedure-20261114.md`（v14，964 行） | — |
| **源冻结 sha256** | `7EBDE2EE60C6D387F2C9B7D2F5FCAA138FAEDFFC959F2750822386888CB06702`（90,003 B） | 本单出具时实测 |
| **围栏范围** | **L115（```python）→ L277（```）**，体 **161 行**（围栏行本身不含） | 实测：§2.4.1 起于 L111，§2.4.2 起于 L348 |
| 围栏内阶段 | **A→E 完整五段**：A 体第 18 行（文字层）/ B 第 60（对象范围）/ C 第 66（副本可开）/ **D 第 74（空库五源取证）** / E 第 137（逐表核对与分级） | 实测阶段注释位置（相对围栏体） |
| 提取件 | `$D\restore_probe.py`，UTF-8 / LF / 无 BOM，**逐字**（不手改、不格式化、不补参数） | — |
| **门 1 源** | 源文件实测 sha256 ≠ 冻结值 ⇒ **`EXIT 8`**（流程件已改版，须重新审定） | — |
| **门 2 件** | `restore_probe.py` 已存在且 sha ≠ 本次提取 ⇒ **`EXIT 8`**（拒静默覆盖漂移副本） | — |
| **门 3 互核** | 打印 `PROBE_SHA256=<64hex>` / `PROBE_LINES=161` / `PROBE_SRC=<md>#L115-L277`；矩阵启动打印同一 sha 并写 `summary.json.probe_sha256` | — |

> **为什么不能只抽 D**：D 是**完整脚本内部的阶段**——它依赖 A 的标记解析结果、B 的对象清单、C 的副本连接与 `read_only` 打开。只抽 D 得到的不是「被测件」，而是「一段没有输入准备、也拿不到 `conn` 的函数体」。**被测件 = L115–L277 的整段 A—E 脚本。**
> **为什么冻结源 hash**：流程件仍在迭代（现为 v14）。若源文件在提取时已不是本单审定的那一版，抽出来的 probe 与文档断言就对不上；把**源 sha 写死**并让它成为门，是唯一能自动发现「审的版本 ≠ 跑的版本」的机制。

**禁止**：同一进程内连接两版、共用 `site-packages`、`pip install` 到系统环境。

---

## 3. 写面清单（完整，逐一可核）

| # | 类别 | 路径 | 说明 |
|---|---|---|---|
| 1 | 虚拟环境 | `.specify/bugs/firefly-engine-defects-20261025/.venv_156/`、`.venv_145/` | pip 安装目标（含 `duckdb` 二进制） |
| 2 | 脚本 | `.specify/bugs/firefly-engine-defects-20261025/_iso_empty_db_matrix.py` | 本单 §5 全文，授权后落盘 |
| 3 | 被测件副本 | `.specify/bugs/firefly-engine-defects-20261025/restore_probe.py` | **流程件 v14 `L115`–`L277` 整段 A—E 脚本**原样复制（**不是 D 段**；不改一字） |
| 4 | 合成库与 run | `.specify/bugs/firefly-engine-defects-20261025/_iso_empty_db_156/**`、`_iso_empty_db_145/**` | 库文件、CSV、`runs/<UTC>_<4hex>/` |
| 5 | 日志 | `.specify/bugs/firefly-engine-defects-20261025/_iso_emptydb_1_5_6.txt`、`_iso_emptydb_1_4_5.txt` | `Tee-Object` 全量输出（**外层仍以 `$LASTEXITCODE` 为准**，日志仅佐证） |
| **6** | **准备脚本** | `.specify/bugs/firefly-engine-defects-20261025/_prepare_probe.py` | 提取 + 源 hash 门（§2.1 ③）；授权后落盘 |
| **7** | **准备日志** | `.specify/bugs/firefly-engine-defects-20261025/_iso_prepare_probe.txt` | `_prepare_probe.py` 输出（`PROBE_SHA256` / `PROBE_LINES` / `PROBE_SRC` / 各门裁决） |
| **8** | **自检临时文件（辅助写面）** | `%TEMP%\gateprobe_<8hex>.txt` | §9 鉴别力自检用；**成功即 `Remove-Item`**，失败**保留**供复核（路径随日志一并回传） |
| — | **系统侧（非我方写面，需声明）** | `%LOCALAPPDATA%\pip\Cache` | pip 下载缓存可能被写；不清除、不视为污染 |
| — | **拒绝写面** | `data/**`、`docs/**`、`.git/**`、任何 `*backup*` / `ref_copy*` / `probe_copy*` | 脚本静态不含这些路径常量（§9 可机检） |

---

## 4. 运行布局与安全边界（V3）

```
_iso_empty_db_156/
└── runs/
    ├── 20261116T031500_a1b2/        ← 本次 RUN（唯一写入对象）
    │   ├── B0/case.db, C5c/case.db, …
    │   ├── C1.csv, C2.csv, …
    │   └── summary.json, b0_surface.json（仅在基线失败时）
    └── 20261115T221000_9f3c/        ← 历史 RUN：**保留不删**（失败现场）
```

**删除边界（三段断言，任一不成立即 `EXIT 6`）**
1. 目标绝对路径必须以 `<WORK>/runs/<RUN>/` 为前缀；
2. 目标不得等于 `RUN` 本身；
3. 目标不得等于 `WORK` 或 `WORK` 的任何祖先。

**同一运行内禁止自愈**：脚本**不含**「发现 B0 非 0 就调过滤条件重试」的路径。过滤器变更只能是**人工改脚本 + 新一次 run**（旧 run 目录原样保留以便对比）。

---

## 5. 脚本全文（v2 · 授权后落盘，本轮仅以文本形式存在）

```python
# -*- coding: utf-8 -*-
"""SYN-20261116-01 rev2 · 空库判据合成验证矩阵（断言式，端到端）
用法: python _iso_empty_db_matrix.py <EXPECT_VER> <WORK_ROOT>
退出码: 0=全 MATCH / 1=存在 DIFF 或 UNVERIFIED / 2=参数或前置缺失
       4=版本不符 / 5=B0 基线非 0（立即停止）/ 6=删除边界越界
       7=B0 基线不可探测（查询异常，立即停止）/ 8=PROBE hash 门拒绝（由 _prepare_probe.py 抛出）
写面: 仅 <WORK_ROOT>/runs/<RUN>/**（venv/脚本/日志见申请单 §3）
"""
import hashlib, json, os, shutil, subprocess, sys, time, uuid
import duckdb

EXPECT_VER = sys.argv[1] if len(sys.argv) > 1 else ""
WORK = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else ""
HERE = os.path.abspath(os.path.dirname(__file__))
PROBE = os.path.join(HERE, "restore_probe.py")

def die(tag, msg, code):
    print(f"{tag}: {msg}"); print(f"EXIT {code}"); sys.exit(code)

# ---------- 前置与版本断言（V1）----------
if not EXPECT_VER: die("ARG", "缺少 EXPECT_VER(argv[1])", 2)
if not WORK or not os.path.isdir(WORK): die("ARG", f"WORK 根不存在: {WORK}", 2)
actual = duckdb.__version__
print(f"VER actual={actual} expect={EXPECT_VER}")
if actual != EXPECT_VER:
    die("VER_MISMATCH", "版本与目标不符，整轮中止（不出任何用例结论）", 4)
if not os.path.isfile(PROBE): die("PROBE", f"被测脚本缺失: {PROBE}", 2)

RUN = os.path.abspath(os.path.join(WORK, "runs",
        time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "_" + uuid.uuid4().hex[:4]))
os.makedirs(RUN, exist_ok=True)
PROBE_SHA = hashlib.sha256(open(PROBE, "rb").read()).hexdigest()
print(f"PROBE {PROBE} sha256={PROBE_SHA}")
print(f"RUN {RUN}")

# ---------- 子目标（V3：删除前核边界）----------
def sub(name):
    d = os.path.abspath(os.path.join(RUN, name))
    if not d.startswith(RUN + os.sep):                      # ①必须在 RUN 下
        die("SCOPE", f"子目标越界: {d}", 6)
    if d in (RUN, WORK):                                    # ②不得是 RUN / WORK
        die("SCOPE", f"拒绝删除根: {d}", 6)
    if os.path.isdir(d):
        shutil.rmtree(d)                                    # 仅删本 RUN 内的用例目录
    os.makedirs(d)
    return d

def mkdb(name, ddl=()):
    d = sub(name); p = os.path.join(d, "case.db")
    con = duckdb.connect(p)                                  # 只连本 RUN 下的新库
    try:
        for s in ddl: con.execute(s)
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return p

def surface(p):
    """诊断用：六源计数（断言不以它为准）。任一源异常向上抛 → 由 case() 记 UNVERIFIED。"""
    con = duckdb.connect(p, read_only=True)
    try:
        SYS = ('information_schema', 'pg_catalog')
        def q(sql): return [tuple(str(x) for x in r) for r in con.execute(sql).fetchall()]
        return {
            "tables_views": q("SELECT table_schema, table_name, table_type FROM information_schema.tables "
                              f"WHERE table_schema NOT IN {SYS}"),
            "sequences": q("SELECT schema_name, sequence_name FROM duckdb_sequences() "
                           f"WHERE schema_name NOT IN {SYS}"),
            "extra_schemas": q("SELECT schema_name FROM duckdb_schemas() "
                               "WHERE schema_name NOT IN ('main','information_schema','pg_catalog')"),
            "user_types": q("SELECT schema_name, type_name FROM duckdb_types() "
                            f"WHERE schema_name NOT IN {SYS} AND NOT internal"),
            "macros_scalar": q("SELECT schema_name, function_name FROM duckdb_functions() "
                               "WHERE function_type = 'macro' AND schema_name NOT IN "
                               "('information_schema','pg_catalog')"),
            "macros_table": q("SELECT schema_name, function_name FROM duckdb_functions() "
                              "WHERE function_type = 'table_macro' AND schema_name NOT IN "
                              "('information_schema','pg_catalog')"),
        }
    finally:
        con.close()

# ---------- 断言引擎（V1）----------
C = {"MATCH": 0, "DIFF": 0, "UNVERIFIED": 0}
R = []

def check(case, rc, out, expect_rc, must=(), must_not=()):
    missing = [m for m in must if m not in out]
    forbid = [m for m in must_not if m in out]
    if rc == expect_rc and not missing and not forbid:
        C["MATCH"] += 1; v = "MATCH"; det = ""
    else:
        C["DIFF"] += 1; v = "DIFF"
        det = f"rc={rc}(expect {expect_rc}) missing={missing} forbidden={forbid}"
    R.append({"case": case, "rc": rc, "expect_rc": expect_rc, "verdict": v, "detail": det})
    print(f"{case}: rc={rc} expect_rc={expect_rc} verdict={v}" + (f" {det}" if det else ""))
    return v == "MATCH"

def run_probe(case, dbp, csv_text, expect_rc, must=(), must_not=()):
    csv_p = os.path.join(os.path.dirname(dbp), case + ".csv")
    with open(csv_p, "w", encoding="utf-8", newline="\n") as f:
        f.write(csv_text)
    r = subprocess.run([sys.executable, PROBE, dbp, csv_p],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    return check(case, r.returncode, out, expect_rc, must, must_not)

def case(name, fn):
    try:
        fn()
    except SystemExit:
        raise
    except Exception as e:
        C["UNVERIFIED"] += 1
        R.append({"case": name, "verdict": "UNVERIFIED", "detail": f"{type(e).__name__}: {e}"})
        print(f"{name}: verdict=UNVERIFIED ({type(e).__name__}: {e})")

HDR = "table,rows,fp\n"

# ---------- B0 基线：非 0 或不可探测，都立即停本轮（V1′）----------
# 注意：B0 **不经过 case()** —— 通用 case() 会把 surface() 异常吞成 UNVERIFIED 并继续跑后续例，
#       而基线不成立时后续例无意义；故此处异常与非零**同权**，一律停并留 summary。
def b0_stop(reason, code, payload):
    with open(os.path.join(RUN, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"actual_ver": actual, "expect_ver": EXPECT_VER, "run": RUN,
                   "probe_sha256": PROBE_SHA, "stop_reason": reason,
                   "counts": C, "records": R, "b0": payload},
                  f, ensure_ascii=False, indent=2)
    print(f"SUMMARY_STOPPED reason={reason} match={C['MATCH']} "
          f"diff={C['DIFF']} unverified={C['UNVERIFIED']}")
    print(f"RUN {RUN}")
    print(f"EXIT {code}")
    sys.exit(code)

try:
    p_b0 = mkdb("B0")
    s_b0 = surface(p_b0)
except Exception as e:
    import traceback
    payload = {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
    with open(os.path.join(RUN, "b0_surface_error.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"B0_UNPROBED {payload['error']}")
    print("B0_UNPROBED_BASELINE ⇒ 立即停止：基线不可探测时，后续用例的 PASS/FAIL 都不成立")
    b0_stop("B0_UNPROBED_BASELINE", 7, payload)

by = {k: len(v) for k, v in s_b0.items()}
total_b0 = sum(by.values())
print(f"B0_SURFACE by_source={by} total={total_b0}")
if total_b0 != 0:
    payload = {"by_source": by, "rows": {k: v[:50] for k, v in s_b0.items()}}
    with open(os.path.join(RUN, "b0_surface.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("B0_BASELINE_NONZERO ⇒ 立即停止：本次运行不产出用例结论，"
          "也不得在同一次运行内改过滤器重试（须人工改脚本 + 新 run）")
    b0_stop("B0_BASELINE_NONZERO", 5, payload)

C["MATCH"] += 1
R.append({"case": "B0", "verdict": "MATCH", "detail": "total=0"})
print("B0: total=0 verdict=MATCH")

# ---------- 10 个 probe 例：全部端到端（V2）；B0 已在上面单独完成，故合计 11 项 ----------
def c1():
    p = mkdb("C1"); run_probe("C1", p, "#EMPTY_DB\n" + HDR, 0,
        must=["PASS_EMPTY_DB", "scope=whole_db", "objects=0", "by_source=", "marker_line=1"],
        must_not=["FAIL("])
def c2():
    p = mkdb("C2"); run_probe("C2", p, HDR + "#EMPTY_DB\n", 0,
        must=["PASS_EMPTY_DB", "marker_line=2", "objects=0", "by_source="], must_not=["FAIL("])
def c7():
    # 与 C2 同口径：除「BOM 不产生假失败」外，还须核 objects=0 与 by_source（v14 rev2 只核了 marker_line）
    p = mkdb("C7"); run_probe("C7", p, "\ufeff" + HDR + "#EMPTY_DB\n", 0,
        must=["PASS_EMPTY_DB", "marker_line=2", "objects=0", "by_source="],
        must_not=["BAD_ROW", "FAIL("])
def c5c():
    p = mkdb("C5c", ["CREATE SEQUENCE s1"])
    run_probe("C5c", p, "#EMPTY_DB\n" + HDR, 2,
        must=["EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY", "objects=1", "'sequences': 1"])
def c5c2():
    p = mkdb("C5c2", ["CREATE SCHEMA sch2"])
    run_probe("C5c2", p, "#EMPTY_DB\n" + HDR, 2,
        must=["EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY", "objects=1", "'extra_schemas': 1"])
def c5c3():
    p = mkdb("C5c3", ["CREATE TYPE mood AS ENUM ('a','b')"])
    run_probe("C5c3", p, "#EMPTY_DB\n" + HDR, 2,
        must=["EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY", "objects=1", "'user_types': 1"])
def c5f():
    p = mkdb("C5f", ["CREATE MACRO m1(x) AS x + 1"])
    run_probe("C5f", p, "#EMPTY_DB\n" + HDR, 2,
        must=["EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY", "objects=1", "'macros_scalar': 1"])
def c5g():
    p = mkdb("C5g", ["CREATE MACRO tm1(x) AS TABLE SELECT x AS c"])
    run_probe("C5g", p, "#EMPTY_DB\n" + HDR, 2,
        must=["EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY", "objects=1", "'macros_table': 1"])
def c5e():
    p = mkdb("C5e", ["CREATE TABLE t1(i INT)"])
    run_probe("C5e", p, "#EMPTY_DB_MAIN_ONLY\n" + HDR, 2,
        must=["EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES", "main.t1"])
def c5b():
    p = mkdb("C5b", ["CREATE SEQUENCE s1"])
    run_probe("C5b", p, "#EMPTY_DB_MAIN_ONLY\n" + HDR, 3,
        must=["PARTIAL_EMPTY_MAIN_ONLY", "main_base=0", "other_objects=1", "'sequences': 1"])

for nm, fn in (("C1", c1), ("C2", c2), ("C7", c7), ("C5c", c5c), ("C5c2", c5c2),
               ("C5c3", c5c3), ("C5f", c5f), ("C5g", c5g), ("C5e", c5e), ("C5b", c5b)):
    case(nm, fn)

# ---------- 汇总与退出码（V1）----------
total = sum(C.values())
print(f"SUMMARY total={total} match={C['MATCH']} diff={C['DIFF']} unverified={C['UNVERIFIED']}")
print(f"RUN {RUN}")
with open(os.path.join(RUN, "summary.json"), "w", encoding="utf-8") as f:
    json.dump({"actual_ver": actual, "expect_ver": EXPECT_VER, "run": RUN,
               "probe_sha256": PROBE_SHA, "counts": C, "records": R},
              f, ensure_ascii=False, indent=2)
code = 0 if (C["DIFF"] == 0 and C["UNVERIFIED"] == 0 and total == 11) else 1
print(f"EXIT {code}")
sys.exit(code)
```

**注 1**：`total` 期望 **11 = B0（1，只做 `surface`）＋ 10 个 `restore_probe` 端到端例**（C1/C2/C7 + C5c/C5c2/C5c3/C5f/C5g + C5e + C5b）。**B0 不经 probe**，故不称「11 例全部经 probe」。
**注 2**：`case()` 捕获异常记 `UNVERIFIED`，**不再 `return` 了事**——它使终态失败（退出码 1）。**但 B0 例外**：B0 不走 `case()`，其异常走 `b0_stop(EXIT 7)`，因为基线不成立时后续例无意义。
**注 3**：`summary.json` 无论正常收尾还是 B0 中止都会写（`b0_stop` 亦写），且都带 `probe_sha256` 与 `stop_reason`（正常收尾无 `stop_reason`）。

---

## 6. 用例矩阵（**B0 基线 + 10 个端到端例**；`must`/`must_not` 即断言内容）

| # | 构造（合成库内） | CSV | 期望 `rc` | 必含片段 | 禁含 |
|---|---|---|---|---|---|
| **B0** | 空库 | — | — | `total=0`（六源） | 任何非 0 计数 |
| C1 | 空库 | `#EMPTY_DB` 首行 | 0 | `PASS_EMPTY_DB` `scope=whole_db` `objects=0` `by_source=` `marker_line=1` | `FAIL(` |
| C2 | 空库 | 标记在第 2 行 | 0 | `marker_line=2` `objects=0` `by_source=` | `FAIL(` |
| C7 | 空库 | BOM + 标记第 2 行 | 0 | `PASS_EMPTY_DB` **`objects=0`** **`by_source=`** `marker_line=2`（与 C2 同口径） | `BAD_ROW` `FAIL(` |
| C5c | `CREATE SEQUENCE s1` | `#EMPTY_DB` | 2 | `EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY` `objects=1` `'sequences': 1` | — |
| C5c2 | `CREATE SCHEMA sch2` | `#EMPTY_DB` | 2 | … `'extra_schemas': 1` | — |
| C5c3 | `CREATE TYPE mood AS ENUM ('a','b')` | `#EMPTY_DB` | 2 | … `'user_types': 1` | — |
| C5f | `CREATE MACRO m1(x) AS x + 1` | `#EMPTY_DB` | 2 | … `'macros_scalar': 1` | — |
| C5g | `CREATE MACRO tm1(x) AS TABLE SELECT x AS c` | `#EMPTY_DB` | 2 | … `'macros_table': 1` | — |
| C5e | `CREATE TABLE t1(i INT)` | `#EMPTY_DB_MAIN_ONLY` | 2 | `EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES` `main.t1` | — |
| C5b | `CREATE SEQUENCE s1` | `#EMPTY_DB_MAIN_ONLY` | 3 | `PARTIAL_EMPTY_MAIN_ONLY` `main_base=0` `other_objects=1` `'sequences': 1` | `PASS_EMPTY_DB` |

**构成**：`total=11` = **B0（1，`surface` 基线）** + **10 个 `restore_probe` 例**；B0 不调用 probe，故两版 probe 调用各 **10 次**（不是 11 次、也不是 22 次/版）。

**鉴别力要求**：C5c–C5g 的命中片段**必须落在对应源**（如 C5g 只认 `'macros_table': 1`；若打印 `'macros_scalar': 1` ⇒ 断言失败）。这是「源归属」断言，防「只要非空就算过」。

---

## 7. 失败处置（不得自愈）

| 现象 | 动作 | 禁止 |
|---|---|---|
| B0 非 0 | `EXIT 5` + `b0_surface.json` + `summary.json(stop_reason=B0_BASELINE_NONZERO)`，**立即停** | 同一次运行内改过滤器重跑 |
| **B0 查询异常** | `EXIT 7` + `b0_surface_error.json`（含 traceback）+ `summary.json(stop_reason=B0_UNPROBED_BASELINE)`，**立即停** | 记 UNVERIFIED 后继续跑后续用例（rev2 的缺陷） |
| 某例 DIFF | 记 DIFF，继续其余用例（跑完再判），终态 `EXIT 1` | 把 DIFF 重跑成 MATCH 再报送 |
| 某例异常 | 记 UNVERIFIED，终态 `EXIT 1` | 把异常 `return` 掉当通过 |
| 需改过滤器 | **人工改脚本 → 新 run**（旧 run 目录原样保留） | 删除旧 run |

---

## 8. 回传格式

```
VER actual=1.5.6 expect=1.5.6
PROBE ...\restore_probe.py sha256=<64hex>          ← 准备链 ③ 的 hash 门；两版必须与 summary.json 一致
RUN .../runs/20261116T031500_a1b2
B0_SURFACE by_source={'tables_views': 0, ...} total=0
B0: total=0 verdict=MATCH
C1: rc=0 expect_rc=0 verdict=MATCH
...
SUMMARY total=11 match=11 diff=0 unverified=0
EXIT 0
```

回传须附：①两版 `Tee-Object` 日志全文（sha256）；②`summary.json`（含 `probe_sha256`；B0 中止时含 `stop_reason`）；③`_prepare_probe.py` 的 `PROBE_SHA256` 输出；④**写面实际清单**（`Get-ChildItem` 两个 `_iso_empty_db_*` 与两个 venv 的顶层，证明只在 §3 列出的路径内）。

---

## 9. 零触碰：以路径与调用链限定（**不靠 hash 推论**）

> **撤回的说法**：rev1 §5「运行前后对生产库/备份做 `Get-FileHash` 记录即可（作零触碰旁证）」——**hash 未变不能证明从未打开**（本流程 §2.4.5 C 已实测：只读打开后字节可完全不变）。rev2 改为**结构性与可机检**的三条：

1. **静态不含路径常量**：两脚本中不得出现 `data/quantstudio.db`、备份目录、`*_backup*`、`ref_copy`、`probe_copy` 等字面量；
   ```powershell
   $D = '.specify\bugs\firefly-engine-defects-20261025'
   # 正查：数组 pattern + 默认正则模式（元素之间是 OR，这是 PS 的正确写法）
   Select-String -Path "$D\_iso_empty_db_matrix.py","$D\restore_probe.py" `
                 -Pattern 'quantstudio\.db','backup','ref_copy','probe_copy','ATTACH'
   ```
   预期：**0 命中**（脚本内 `ATTACH` 亦不出现）。

   **鉴别力自检（必做，防「命令本身失效 ⇒ 假绿」）**：先用一段**含关键词**的临时文本跑同一条命令，必须命中 ≥1；不命中就说明命令/参数有问题，此时「0 命中」不构成证据。
   ```powershell
   $t = Join-Path $env:TEMP ("gateprobe_" + [guid]::NewGuid().ToString('N').Substring(0,8) + ".txt")
   [System.IO.File]::WriteAllText($t, "x quantstudio.db ATTACH ref_copy y", (New-Object System.Text.UTF8Encoding($false)))
   Select-String -Path $t -Pattern 'quantstudio\.db','backup','ref_copy','probe_copy','ATTACH'
   Remove-Item $t -Force
   ```
   > 该临时文件属**辅助写面**（§3 #8）：自检成功即删；**自检失败时保留**（连同路径回传），否则「命令失效」这件事会随文件一起消失。

   **rev2 的错误写法（作废，勿再用）**：
   ```powershell
   # ✗ -SimpleMatch 把整个 'a|b|c' 当**一个字面量**去匹配，竖线不是 OR ⇒ 永远 0 命中
   Select-String -Path ... -Pattern 'quantstudio\.db|backup|ref_copy|probe_copy|ATTACH' -SimpleMatch
   ```
2. **连接目标受限于 RUN**：矩阵脚本的每次 `duckdb.connect(...)` 目标都由 `mkdb()` 生成（路径必在 `RUN` 下）；`restore_probe.py` 的目标来自命令行参数，而矩阵脚本只传 `RUN` 下的 `case.db`。审阅要点：`connect` 调用点在源码里可逐一数出（矩阵 3 处、probe 1 处）。
3. **hash 仅作旁证不做证明**：如需记录，仅列入日志附注，**不得**据此宣称「未打开」。

---

## 10. 待审点（设计审，逐条给「准 / 改」）

| # | 事项 | 我方拟值 |
|---|---|---|
| 1 | 断言粒度 | `rc` + 必含片段 + 禁含片段（不做全行等价，因路径/时间戳随运行变化） |
| 2 | 退出码语义 | 0=全 MATCH；1=有 DIFF/UNVERIFIED；2=参数；4=版本；5=基线非 0；6=边界；**7=基线不可探测**；**8=PROBE 提取/hash 门拒绝**；**9=退出码遮盖（外层与日志末行不一致）** |
| 3 | B0 停止策略 | **非 0 与查询异常都立即停**（`EXIT 5` / `EXIT 7`），都不跑后续用例，都留 `summary.json` |
| 4 | run 隔离 | `runs/<UTC>_<4hex>/`，旧 run 永久保留 |
| 5 | 端到端范围 | **B0（1，`surface`）+ 10 个 `restore_probe` 例 = 11 项**；`surface()` 仅作 B0 诊断 |
| 6 | 零触碰表述 | 以静态不含路径（**数组 pattern + 鉴别力自检**）+ connect 目标受限于 RUN 限定（不用 hash 推论） |
| 8 | 准备链 | `New-Item` 建根 → venv/依赖 → `_prepare_probe.py` 原样提取 + hash 门 → 版本断言 → 运行（§2.1） |
| 9 | **失败传播** | `Step` 包裹四步（存 `$rc` → 判 → 停）+ matrix 后紧接存 `$rc` + 与日志 `EXIT n` 交叉核对（不等 ⇒ `EXIT 9`） |
| 10 | **提取范围** | **L115–L277 整段 A—E（161 行）**；源 sha 冻结 `7EBDE2EE…06702`；**明确不是 D 段** |
| 7 | 工时 | 约 20–30 分钟（两版 venv + 每版 **10** 次 probe 调用） |

> **技术门未过不请批准**：本单在 V1–V3 未通过设计审前，不请求任何运行/创建授权。

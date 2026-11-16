# C4 解析状态可见化 —— **精确实施申请 v4**（待批 · 未实施）

> **版本**：**v4**（2026-11-16 R17 修正四项：**①代码块语法合法＋AST 静态解析自证 ②`errset.before` 真正置于实施之前 ③`--files` 改传冻结后的显式路径列表（单一展开源）④唯一排他入口 `run.sh` ＋ 撤销"档位 off 则错误消失"的错误推论**）
> **依据**：C4 候选方案 v10 `docs/handoff/kb-parse-status-detection-design-20261115.md` §16.1
> **状态**：**待批 · 未实施**（本件仅文档；未改 `kb_hook`／测试／路由／档位，未运行实验或回归，仅做静态解析自证）

---

## 1 候选身份

| 件 | commit | size | sha256 |
|---|---|---|---|
| **C4 方案 v10** | `7bdeffb` | 39,951 B | `ceb4c110b15f453bd3aa35e5dcc893aed1fab493fc5a4905e40bc132df5791f5` |
| 配套《兜底设计 v2》 | — | — | `0c84c89bf573de3bcee1090b17ac70bec2ad23bd7b56e9c306e0eeab66662322` |

## 2 实际文件清单（允许写入 · 精确路径 · 禁通配）

| # | 路径 | 动作 | 当前实测 |
|---|---|---|---|
| 1 | `scripts/kb_hook.py` | 改 | 18,568 B；sha256 `ce38ed739ab19504bd1066a6d3bca3f5d55b9b2c2c52aa33c87f2f102bca265f`；blob `90bd63232e3d` |
| 2 | `tests/test_kb_parse_status.py` | 新增 | **不存在** |

**清单外一律不动**。回归驱动（`run.sh`／`run.py`）**全部落 run 目录**，不进仓库。

---

## 3 输入：**单一展开源（显式路径列表）**

> **R17 判据**：不让"我的 glob"与"`kb_hook` 的 glob"两套并行——**glob 只用于第一次生成显式清单**；此后 `kb_hook --files` 一律吃**清单中的显式路径**，从根上消除"等数不同集合"。

### 3.1 生成基线清单（仅此一次，阶段 A）

```bash
python -c "import glob,io,os;ps=[];[ps.extend(sorted(glob.glob(p))) for p in ['knowledge/qmt/QD-*.md','knowledge/ptrade-deploy/PD-*.md','knowledge/db/DB-*.md','knowledge/ops/OPS-*.md']];ps=sorted({p.replace(chr(92),'/') for p in ps});io.open(os.path.join(os.environ['RUN_DIR'],'inputs.list'),'w',encoding='utf-8',newline='\n').write('\n'.join(ps)+'\n');print('inputs.list =',len(ps),'件')"
```

实测（2026-11-16 08:55）：qd 22 ＋ pd 22 ＋ db 44 ＋ ops 5 ＝ **93 件**。

### 3.2 `kb_hook` 调用形态（**全部显式路径**）

`_expand_files`（`kb_hook.py:73-103`）：入参**含 `*`／`?`／`[` 才做 glob，否则按路径处理** ⇒ 传显式路径时 `expanded` 恒等于该清单，无二次展开。

```python
def kb_hook(files, repo_root=".", kb_info_rel="knowledge/kb-info.yaml"):
    argv = [sys.executable, "scripts/kb_hook.py",
            "--repo-root", repo_root,
            "--kb-info", kb_info_rel,
            "--strict", "--json",
            "--files"] + files
    res = subprocess.run(argv, capture_output=True)
    return res.returncode, res.stdout
```

### 3.3 冻结三件（A／B 各一次，跨阶段 diff）

| 文件 | 内容 |
|---|---|
| `inputs.<tag>.txt` | 显式路径清单副本（阶段 B 重算，用于集合 diff） |
| `inputs.<tag>.sha256` | 逐件字节 hash（`<sha256>  <path>` 逐行） |
| `kb-info.<tag>.sha256` | `knowledge/kb-info.yaml` 全文 hash |

**同输入的判据**：`diff inputs.before.txt inputs.after.txt` **为空**（路径集合身份）**且** `diff inputs.before.sha256 inputs.after.sha256` **为空**（逐件内容身份）。**件数一致不作证明。**

---

## 4 配置基线（须保持）

```
qd=off  pd=off  db=off  ops=warn   ops.placeholder_gate_since=2026-11-13T20:56:02+08:00
```

---

## 5 回归：**唯一排他入口**（`run.sh` 两阶段）

> **R17 判据**：所有步骤只有一个入口 `run.sh`；`bash` 不会因 Python 非 0 自动停 ⇒ 首行 `set -euo pipefail`，`run.py` 内任何失败即 `sys.exit(9)`。
> **排他**：`mkdir "$RUN_DIR"` **不带 `-p`** ⇒ 已存在即失败（不换名重试、不写入既有目录）。
> **失败材料保留**：失败时**不清理** run 目录（禁 `trap ... EXIT` 内删除）。
> **两阶段的理由**：实施不可自动化；"实施前"取证（pin／inputs／**errset.before**／baseline）必须先于实施落盘 ⇒ 阶段 A 末尾停下等人实施，阶段 B 跑下半段。

### 5.1 `run.sh`（唯一入口）

```bash
#!/usr/bin/env bash
set -euo pipefail

PHASE="${1:-}"
case "$PHASE" in
  before|after) ;;
  *) echo "usage: run.sh before|after"; exit 2 ;;
esac

if [ "$PHASE" = "before" ]; then
  RUN=$(python -c "import datetime;print(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))")
  RUN_DIR="docs/evidence/runs/${RUN}-c4-impl"
  mkdir "$RUN_DIR"
  mkdir "$RUN_DIR/rollback"
  export RUN_DIR
  printf '%s\n' "$RUN_DIR" > .run-dir-pointer
  echo "RUN_DIR=$RUN_DIR"
else
  RUN_DIR=$(cat .run-dir-pointer)
  export RUN_DIR
  echo "RUN_DIR=$RUN_DIR (resume)"
fi

exec python "$RUN_DIR/run.py" "$PHASE"
```

### 5.2 `run.py`（阶段实现，落 `$RUN_DIR/`）

```python
#!/usr/bin/env python3
import glob
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

RUN_DIR = os.environ["RUN_DIR"]
NEWLINE = "\n"
KB_INFO = "knowledge/kb-info.yaml"
KEEP = ["scripts/kb_hook.py", "tests/test_kb_parse_status.py"]


def die(msg):
    print("STOP: " + msg)
    sys.exit(9)


def path(name):
    return os.path.join(RUN_DIR, name)


def sha(path_str):
    with open(path_str, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def write_text(name, text):
    with io.open(path(name), "w", encoding="utf-8", newline=NEWLINE) as fh:
        fh.write(text)


def read_text(name):
    with io.open(path(name), encoding="utf-8") as fh:
        return fh.read()


def load_inputs():
    items = [ln.strip() for ln in read_text("inputs.list").split("\n")]
    return [i for i in items if i]


def kb_hook(files, repo_root=".", kb_info_rel=KB_INFO):
    argv = [sys.executable, "scripts/kb_hook.py",
            "--repo-root", repo_root,
            "--kb-info", kb_info_rel,
            "--strict", "--json",
            "--files"] + files
    res = subprocess.run(argv, capture_output=True)
    return res.returncode, res.stdout


def freeze(tag):
    files = load_inputs()
    write_text("inputs." + tag + ".txt", NEWLINE.join(files) + NEWLINE)
    lines = []
    for rel in files:
        if not os.path.isfile(rel):
            die("输入文件缺失: " + rel)
        lines.append(sha(rel) + "  " + rel)
    write_text("inputs." + tag + ".sha256", NEWLINE.join(lines) + NEWLINE)
    write_text("kb-info." + tag + ".sha256", sha(KB_INFO) + NEWLINE)
    print("freeze(" + tag + "): " + str(len(files)) + " 件")


def capture(tag, files, repo_root="."):
    rc, out = kb_hook(files, repo_root=repo_root)
    with open(path(tag + ".json"), "wb") as fh:
        fh.write(out)
    write_text(tag + ".json.rc", str(rc) + NEWLINE)
    print(tag + ": rc=" + str(rc) + " bytes=" + str(len(out)))
    return rc


def sandbox():
    marker = path("errset.sandbox.path")
    if os.path.isfile(marker):
        return read_text("errset.sandbox.path").strip()
    sand = tempfile.mkdtemp(prefix="kb_err_")
    os.makedirs(os.path.join(sand, "knowledge", "ops"), exist_ok=True)
    shutil.copyfile(KB_INFO, os.path.join(sand, "knowledge", "kb-info.yaml"))
    body = NEWLINE.join(["---", "id: OPS-2026-9004", 'trigger: ""', "---", ""])
    target = os.path.join(sand, "knowledge", "ops", "OPS-2026-9004.md")
    with io.open(target, "w", encoding="utf-8", newline=NEWLINE) as fh:
        fh.write(body)
    write_text("errset.sandbox.path", sand + NEWLINE)
    return sand


def errset(tag):
    sand = sandbox()
    found = glob.glob(os.path.join(sand, "knowledge", "ops", "OPS-*.md"))
    rel = []
    for item in found:
        rel.append(os.path.relpath(item, sand).replace(os.sep, "/"))
    if not rel:
        die("errset 沙箱内无 OPS 文件")
    rc, out = kb_hook(rel, repo_root=sand, kb_info_rel=KB_INFO)
    with open(path("errset." + tag + ".json"), "wb") as fh:
        fh.write(out)
    write_text("errset." + tag + ".json.rc", str(rc) + NEWLINE)
    print("errset." + tag + ": rc=" + str(rc))
    return rc


def assert_gates():
    import yaml
    with io.open(KB_INFO, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh.read())
    got = {}
    for fam in doc["families"]:
        if "placeholder_gate" in fam:
            got[fam["id"]] = (fam.get("placeholder_gate"),
                              fam.get("placeholder_gate_since"))
    print("gates = " + str(got))
    expect = {"qd": "off", "pd": "off", "db": "off", "ops": "warn"}
    for key, val in expect.items():
        pair = got.get(key, (None, None))
        if pair[0] != val:
            die("档位不符: " + key)
    ops = got.get("ops", (None, None))
    if ops[1] != "2026-11-13T20:56:02+08:00":
        die("ops since 不符")


def pin():
    src = "scripts/kb_hook.py"
    dst = path("rollback/scripts__kb_hook.py.pre.bin")
    shutil.copyfile(src, dst)
    digest = sha(dst)
    meta = NEWLINE.join([digest, str(os.path.getsize(dst)), src, ""])
    write_text("rollback/scripts__kb_hook.py.pre.meta", meta)
    print("pin: " + digest)
    argv = ["git", "-c", "core.quotepath=false", "ls-files", "-s", "--"] + KEEP
    res = subprocess.run(argv, capture_output=True)
    with open(path("index.before.txt"), "wb") as fh:
        fh.write(res.stdout)


def diff_inputs():
    pairs = [("inputs.before.txt", "inputs.after.txt"),
             ("inputs.before.sha256", "inputs.after.sha256"),
             ("kb-info.before.sha256", "kb-info.after.sha256")]
    for left, right in pairs:
        if read_text(left) != read_text(right):
            die("输入失配: " + left + " vs " + right)
    print("INPUTS_FROZEN_OK")


def compare():
    with io.open(path("baseline.json"), encoding="utf-8") as fh:
        base = json.load(fh)
    with io.open(path("after.json"), encoding="utf-8") as fh:
        aft = json.load(fh)
    allow = set(["parse_stats", "parse_detail", "skip_detail"])
    fail = []
    for key in sorted(set(base) | set(aft)):
        if key in allow:
            continue
        if base.get(key) != aft.get(key):
            fail.append("既有键变化: " + key)

    def badmap(doc):
        out = {}
        for item in doc.get("bad", []):
            out[item.get("file")] = "|".join(item.get("errors", []))
        return out

    lhs, rhs = badmap(base), badmap(aft)
    for key in sorted(set(lhs) | set(rhs)):
        if lhs.get(key) != rhs.get(key):
            fail.append("逐件 bad 差异: " + str(key))
    if fail:
        for line in fail:
            print("  - " + line)
        die("JSON 对照 FAIL")
    print("COMPARE_PASS")


def rc_compare():
    b = read_text("baseline.json.rc").strip()
    a = read_text("after.json.rc").strip()
    eb = read_text("errset.before.json.rc").strip()
    ea = read_text("errset.after.json.rc").strip()
    print("整次 rc: baseline=" + b + " after=" + a)
    print("errset  : before=" + eb + " after=" + ea)
    if b != a:
        die("整次退出码不一致")
    if eb != "1":
        die("errset.before 非 1")
    if ea != "1":
        die("errset.after 非 1")


def index_compare():
    argv = ["git", "-c", "core.quotepath=false", "ls-files", "-s", "--"] + KEEP
    res = subprocess.run(argv, capture_output=True)
    with open(path("index.after.txt"), "wb") as fh:
        fh.write(res.stdout)
    if read_text("index.before.txt") != read_text("index.after.txt"):
        die("index 条目（mode/blob）变化")
    print("INDEX_UNCHANGED_OK")


def phase_before():
    assert_gates()
    pin()
    freeze("before")
    rc = errset("before")
    if rc != 1:
        die("errset.before 应为 1，实测 " + str(rc))
    capture("baseline", load_inputs())
    print("PHASE_BEFORE_DONE  现在实施（仅 §2 两文件），完成后跑 run.sh after")


def phase_after():
    capture("after", load_inputs())
    rc = errset("after")
    if rc != 1:
        die("errset.after 应为 1，实测 " + str(rc))
    freeze("after")
    diff_inputs()
    compare()
    rc_compare()
    index_compare()
    print("PHASE_AFTER_DONE")


def main():
    if len(sys.argv) < 2:
        die("usage: run.py before|after")
    phase = sys.argv[1]
    if phase == "before":
        phase_before()
    elif phase == "after":
        phase_after()
    else:
        die("未知阶段 " + phase)


if __name__ == "__main__":
    main()
```

### 5.3 步骤时序（**`errset.before` 在实施之前**）

| 阶段 | 步 | 动作 | 产物 |
|---|---|---|---|
| **A（实施前）** | A1 | 档位断言 | — |
| | A2 | 写前 pin ＋ `index.before.txt` | `rollback/*.pre.bin/.pre.meta` |
| | A3 | 输入冻结 before ＋ `inputs.list` | `inputs.before.txt/.sha256`、`kb-info.before.sha256` |
| | **A4** | **`errset.before`（断言 rc==1）** | **`errset.before.json` ＋ `.rc`** |
| | A5 | `baseline`（显式路径 ＋ `--strict`） | `baseline.json` ＋ `.rc` |
| | — | **停下，人工实施**（仅 §2 两文件） | — |
| **B（实施后）** | B1 | `after`（同显式路径 ＋ `--strict`） | `after.json` ＋ `.rc` |
| | B2 | `errset.after`（断言 rc==1） | `errset.after.json` ＋ `.rc` |
| | B3 | 输入冻结 after ＋ **两条 diff** | `inputs.after.*` |
| | B4 | JSON 逐件对照 | `COMPARE_PASS` |
| | B5 | 整次 rc ＋ errset 双侧 rc 断言 | — |
| | B6 | index `mode+blob` 对照 | `index.after.txt` |
| | B7 | pytest（既有两条 ＋ 新增；含"会红"验证） | — |

---

## 6 pytest（阶段 B 第 B7 步，脚本外单列）

```bash
python -m pytest tests/test_kb_hook_skipped.py tests/test_kb_placeholder_gate.py -q
python -m pytest tests/test_kb_parse_status.py -q
```

新增测试须含**「会红」验证**：注入一个应失败样例（如把 `checked` 计数故意改错 1），确认测试**变红**后再移除注入。

---

## 7 errset 设计说明（**撤销 R17 指出的错误推论**）

> ❌ **撤销**（v3 曾写"若沙箱档位为 off 则占位校验不介入 ⇒ 不产生 bad"）：
> **该推论不成立**。`placeholder_gate` 档位只控制**占位校验**是否介入；而
> **六必填为空、字段非法、`id` 非法等属 `validate_record` 的既有错误，其产生与档位无关**——
> 档位设 `off` **也不会**让这些错误消失。
> ⇒ 沙箱**无需**特殊设定档位，直接复制真库 `kb-info.yaml`（`ops=warn`）即可；`rc=1` 由**既有 `bad`** 决定。
> ⇒ 判据只看"**是否存在既有 bad ⇒ strict rc==1**"，与档位取值**无因果**。

## 8 回退（引用，不重开设计审核）

按 **C4 v10 §16.2**：V1 原字节快照为唯一回退源（`SNAP_SHA256`/`SNAP_SIZE`）；V2 **禁 `git checkout <pin> -- <path>`**，改字节写回（`open(..., "wb")`）／`Copy-Item`／`git cat-file`（核 `returncode==0`）；验证**唯一充分判据 `sha256(path)==SNAP_SHA256`**，`git status` 非空**不构成**失败。新增测试文件回退＝删除。

## 9 范围外

不改 `knowledge/kb-info.yaml`、`.githooks/*`、`_match_family`/`entry_glob`（路由语义不变）、`kb-kit/**`；不新增 `id_prefix`；不延 ops 观察窗；不 push。

## 10 结论

| 项 | 结论 |
|---|---|
| 写入面 | **2 文件**，清单外零改动；驱动脚本全落 run 目录 |
| 输入 | **单一展开源**：显式路径列表（`--files` 吃清单，不再两套 glob） |
| 顺序 | **`errset.before` 在实施前**（阶段 A 第 A4 步），非靠文件名 |
| 停止门 | 唯一入口 `run.sh` ＋ `set -euo pipefail` ＋ `run.py` 失败即 `sys.exit(9)`；run 目录排他新建；失败材料保留 |
| 档位因果 | **已撤销错误推论**：`validate_record` 错误不随档位 `off` 消失 |
| **状态** | **待批 · 未实施** |

## 11 变更记录

| 版本 | 变更 |
|---|---|
| v3 | R16：恒等式更正；errset 分件；停止门方向 |
| **v4** | **R17**：①**代码块语法合法化**——`newline` 改 `NEWLINE` 常量、errset front-matter 改 `NEWLINE.join([...])` 构造（消除长行转义与换行拆断）；全文代码块经 **AST ＋ `bash -n` 静态解析自证通过**；②**`errset.before` 前移**至阶段 A（实施之前），附 A／B 时序表；③**显式路径单一展开源**；④**唯一排他入口** `run.sh`＋`run.py`；⑤**撤销**"档位 off 则 validate 错误消失"的错误推论 |
| | 自证读数：见提交申报（python 块 N 个 AST 全通过；bash 块 M 个 `bash -n` 全通过） |
| | 静态解析自证（R17）：**python 块 2/2 AST 通过**；**bash 块 3/3 `bash -n` 通过（Git Bash 5.2.37）**。注：本机 `bash` 实为 WSL，因 localhost 代理配置无法启动（`NAT 模式不支持`），故 shell 校验改用 Git for Windows 自带 bash；此为环境降级，已如实记录。 |

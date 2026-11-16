# C4 解析状态可见化 —— **精确实施申请 v5**（待批 · 未实施）

> **版本**：**v5**（2026-11-16 R18 修正三项：**①驱动启动链闭合——单文件 `run.sh` 自包含 bootstrap（heredoc 落 `run.py`、生成 `inputs.list`）②去掉 `.run-dir-pointer`，`after` 显式接收 RUN 路径并核阶段身份 ③Git 子进程必须核 `returncode` ＋ 关键路径非空断言**）
> **依据**：C4 候选方案 v10 `docs/handoff/kb-parse-status-detection-design-20261115.md` §16.1
> **状态**：**待批 · 未实施**（本件仅文档；未创建执行驱动、未改 `kb_hook`／测试／档位／index，未跑回归）

---

## 1 候选身份

| 件 | commit | size | sha256 |
|---|---|---|---|
| **C4 方案 v10** | `7bdeffb` | 39,951 B | `ceb4c110b15f453bd3aa35e5dcc893aed1fab493fc5a4905e40bc132df5791f5` |
| 配套《兜底设计 v2》 | — | — | `0c84c89bf573de3bcee1090b17ac70bec2ad23bd7b56e9c306e0eeab66662322` |

## 2 实际文件清单（允许写入 · 精确路径 · 禁通配）

| # | 路径 | 动作 | 当前实测 |
|---|---|---|---|
| 1 | `scripts/kb_hook.py` | 改 | 18,568 B；sha256 `ce38ed73…bca265f`；blob `90bd63232e3d` |
| 2 | `tests/test_kb_parse_status.py` | 新增 | **不存在** |

**清单外一律不动**。`run.sh` 落 run 目录（`docs/evidence/runs/<UTC>-c4-impl/`），**不进仓库**。

## 3 输入：单一展开源（显式路径列表）

`_expand_files`（`kb_hook.py:73-103`）：入参**含 `*`／`?`／`[` 才 glob，否则按路径处理** ⇒ 传显式路径时 `expanded` 恒等于该清单。

`inputs.list` 由 **`run.sh` 的 bootstrap 生成一次**（见 §5.1），此后 `kb_hook --files` 只吃清单内显式路径。

实测（2026-11-16 08:55）：qd 22 ＋ pd 22 ＋ db 44 ＋ ops 5 ＝ **93 件**。

## 4 配置基线（须保持）

```
qd=off  pd=off  db=off  ops=warn   ops.placeholder_gate_since=2026-11-13T20:56:02+08:00
```

---

## 5 回归：**唯一入口 `run.sh`（单文件自包含 bootstrap）**

> **R18-① 闭合启动链**：`run.sh` 必须在 `exec run.py` **之前**把驱动写入 RUN 目录、并生成 `inputs.list`——否则空目录里没有 `run.py`，入口必然失败。
> **R18-② 无共享指针**：删除 `.run-dir-pointer`（该文件在仓库根、超出辅助写面、且共享可被覆盖）；`after` 阶段由调用方**显式传入本次 RUN 路径**。
> **R18-③ Git 失败门**：所有 git 子进程必须核 `returncode==0`；`scripts/kb_hook.py` 的 index 行**必须非空**（防"两次都失败、空输出相等"被误判为未变）。

### 5.1 `run.sh`（唯一入口；两阶段；内嵌驱动）

```bash
#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage:"
  echo "  run.sh before           # 阶段A：排他建 run 目录 + 落驱动 + 生成 inputs.list + 采集改前证据"
  echo "  run.sh after <RUN_DIR>  # 阶段B：显式传入阶段A打印的 RUN 目录"
  exit 2
}

PHASE="${1:-}"
[ -n "$PHASE" ] || usage

if [ "$PHASE" = "before" ]; then
  RUN=$(python -c "import datetime;print(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))")
  RUN_DIR="docs/evidence/runs/${RUN}-c4-impl"
  mkdir "$RUN_DIR"
  mkdir "$RUN_DIR/rollback"
  echo "bootstrap-1: RUN_DIR=$RUN_DIR"

  cat > "$RUN_DIR/run.py" <<'PYEOF_BOOT'
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

NEWLINE = "\n"
KB_INFO = "knowledge/kb-info.yaml"
KEEP = ["scripts/kb_hook.py", "tests/test_kb_parse_status.py"]
REQUIRED_BEFORE = ["inputs.list", "inputs.before.txt", "inputs.before.sha256",
                   "baseline.json", "baseline.json.rc", "errset.before.json.rc"]


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
    files = [i for i in items if i]
    if not files:
        die("inputs.list 为空")
    return files


def git_ls_files(paths, must_contain=None):
    argv = ["git", "-c", "core.quotepath=false", "ls-files", "-s", "--"] + paths
    res = subprocess.run(argv, capture_output=True)
    if res.returncode != 0:
        die("git ls-files 失败 rc=" + str(res.returncode))
    text = res.stdout.decode("utf-8", "replace")
    if must_contain is not None and must_contain not in text:
        die("index 缺条目: " + must_contain)
    return text


def kb_hook(files, repo_root=".", kb_info_rel=KB_INFO):
    argv = [sys.executable, "scripts/kb_hook.py",
            "--repo-root", repo_root,
            "--kb-info", kb_info_rel,
            "--strict", "--json",
            "--files"] + files
    res = subprocess.run(argv, capture_output=True)
    if res.stdout is None:
        die("kb_hook 无输出")
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
    expected = {"qd": "off", "pd": "off", "db": "off", "ops": "warn"}
    for key, val in expected.items():
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
    text = git_ls_files(KEEP, must_contain="scripts/kb_hook.py")
    write_text("index.before.txt", text)


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
    text = git_ls_files(KEEP, must_contain="scripts/kb_hook.py")
    write_text("index.after.txt", text)
    if read_text("index.before.txt") != text:
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
    write_text("phase.before.done", RUN_DIR + NEWLINE)
    print("PHASE_BEFORE_DONE")
    print("现在实施（仅 §2 两文件），完成后执行：bash <本脚本> after " + RUN_DIR)


def phase_after():
    for name in REQUIRED_BEFORE:
        if not os.path.isfile(path(name)):
            die("阶段A 产物缺失: " + name)
    if os.path.isfile(path("inputs.after.txt")):
        die("本目录已跑过 after（inputs.after.txt 已存在），拒绝重复执行")
    write_text("phase.after.started", RUN_DIR + NEWLINE)
    capture("after", load_inputs())
    rc = errset("after")
    if rc != 1:
        die("errset.after 应为 1，实测 " + str(rc))
    freeze("after")
    diff_inputs()
    compare()
    rc_compare()
    index_compare()
    write_text("phase.after.done", RUN_DIR + NEWLINE)
    print("PHASE_AFTER_DONE")


RUN_DIR = ""


def main():
    global RUN_DIR
    if len(sys.argv) < 3:
        die("usage: run.py before|after <run_dir>")
    phase = sys.argv[1]
    RUN_DIR = os.path.abspath(sys.argv[2])
    if not os.path.isdir(RUN_DIR):
        die("RUN_DIR 不存在: " + RUN_DIR)
    print("RUN_DIR=" + RUN_DIR)
    if phase == "before":
        phase_before()
    elif phase == "after":
        phase_after()
    else:
        die("未知阶段 " + phase)


if __name__ == "__main__":
    main()
PYEOF_BOOT
  echo "bootstrap-2: driver written -> $RUN_DIR/run.py"

  RUN_DIR="$RUN_DIR" python -c "import glob,io,os;ps=[];[ps.extend(sorted(glob.glob(p))) for p in ['knowledge/qmt/QD-*.md','knowledge/ptrade-deploy/PD-*.md','knowledge/db/DB-*.md','knowledge/ops/OPS-*.md']];ps=sorted({p.replace(chr(92),'/') for p in ps});io.open(os.path.join(os.environ['RUN_DIR'],'inputs.list'),'w',encoding='utf-8',newline='\n').write('\n'.join(ps)+'\n');print('bootstrap-3: inputs.list='+str(len(ps)))"

  exec python "$RUN_DIR/run.py" before "$RUN_DIR"
elif [ "$PHASE" = "after" ]; then
  RUN_DIR="${2:-}"
  [ -n "$RUN_DIR" ] || usage
  [ -d "$RUN_DIR" ] || { echo "STOP: RUN_DIR 不存在: $RUN_DIR"; exit 9; }
  [ -f "$RUN_DIR/run.py" ] || { echo "STOP: 缺驱动: $RUN_DIR/run.py"; exit 9; }
  exec python "$RUN_DIR/run.py" after "$RUN_DIR"
else
  usage
fi
```

> **启动链顺序（R18-①）**：`mkdir`（排他）→ `mkdir rollback` → **写入 `run.py`（bootstrap-2）** → **生成 `inputs.list`（bootstrap-3）** → `exec python run.py before`。
> 阶段 A 末打印 `RUN_DIR=…`；阶段 B 用该值显式传参，**不再依赖任何指针文件**。

### 5.2 驱动 `run.py`（bootstrap-2 内嵌全文，逐字）

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

NEWLINE = "\n"
KB_INFO = "knowledge/kb-info.yaml"
KEEP = ["scripts/kb_hook.py", "tests/test_kb_parse_status.py"]
REQUIRED_BEFORE = ["inputs.list", "inputs.before.txt", "inputs.before.sha256",
                   "baseline.json", "baseline.json.rc", "errset.before.json.rc"]


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
    files = [i for i in items if i]
    if not files:
        die("inputs.list 为空")
    return files


def git_ls_files(paths, must_contain=None):
    argv = ["git", "-c", "core.quotepath=false", "ls-files", "-s", "--"] + paths
    res = subprocess.run(argv, capture_output=True)
    if res.returncode != 0:
        die("git ls-files 失败 rc=" + str(res.returncode))
    text = res.stdout.decode("utf-8", "replace")
    if must_contain is not None and must_contain not in text:
        die("index 缺条目: " + must_contain)
    return text


def kb_hook(files, repo_root=".", kb_info_rel=KB_INFO):
    argv = [sys.executable, "scripts/kb_hook.py",
            "--repo-root", repo_root,
            "--kb-info", kb_info_rel,
            "--strict", "--json",
            "--files"] + files
    res = subprocess.run(argv, capture_output=True)
    if res.stdout is None:
        die("kb_hook 无输出")
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
    expected = {"qd": "off", "pd": "off", "db": "off", "ops": "warn"}
    for key, val in expected.items():
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
    text = git_ls_files(KEEP, must_contain="scripts/kb_hook.py")
    write_text("index.before.txt", text)


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
    text = git_ls_files(KEEP, must_contain="scripts/kb_hook.py")
    write_text("index.after.txt", text)
    if read_text("index.before.txt") != text:
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
    write_text("phase.before.done", RUN_DIR + NEWLINE)
    print("PHASE_BEFORE_DONE")
    print("现在实施（仅 §2 两文件），完成后执行：bash <本脚本> after " + RUN_DIR)


def phase_after():
    for name in REQUIRED_BEFORE:
        if not os.path.isfile(path(name)):
            die("阶段A 产物缺失: " + name)
    if os.path.isfile(path("inputs.after.txt")):
        die("本目录已跑过 after（inputs.after.txt 已存在），拒绝重复执行")
    write_text("phase.after.started", RUN_DIR + NEWLINE)
    capture("after", load_inputs())
    rc = errset("after")
    if rc != 1:
        die("errset.after 应为 1，实测 " + str(rc))
    freeze("after")
    diff_inputs()
    compare()
    rc_compare()
    index_compare()
    write_text("phase.after.done", RUN_DIR + NEWLINE)
    print("PHASE_AFTER_DONE")


RUN_DIR = ""


def main():
    global RUN_DIR
    if len(sys.argv) < 3:
        die("usage: run.py before|after <run_dir>")
    phase = sys.argv[1]
    RUN_DIR = os.path.abspath(sys.argv[2])
    if not os.path.isdir(RUN_DIR):
        die("RUN_DIR 不存在: " + RUN_DIR)
    print("RUN_DIR=" + RUN_DIR)
    if phase == "before":
        phase_before()
    elif phase == "after":
        phase_after()
    else:
        die("未知阶段 " + phase)


if __name__ == "__main__":
    main()
```

### 5.3 阶段身份核对（R18-②，替代 `.run-dir-pointer`）

| 检查 | 位置 | 判据 |
|---|---|---|
| RUN 目录存在 | `run.sh after` ＋ `run.py main` | `-d` / `os.path.isdir` |
| 驱动在位 | `run.sh after` | `-f "$RUN_DIR/run.py"` |
| **阶段 A 产物齐全** | `run.py phase_after`（`REQUIRED_BEFORE` 六件） | 缺任一 ⇒ `die` |
| **未重复跑 after** | `run.py phase_after` | `inputs.after.txt` 已存在 ⇒ 拒绝 |
| 阶段标记 | `run.py` | 写 `phase.before.done` / `phase.after.started` / `phase.after.done` |

> **不使用** `.run-dir-pointer`，**不使用 HEAD 比对阻断他线**——身份由"本次显式传入的 RUN 路径 ＋ 该目录内阶段产物"共同证明。

### 5.4 步骤时序

| 阶段 | 步 | 动作 | 产物 |
|---|---|---|---|
| **A** | A0 | `run.sh before`：排他建目录 → **落驱动** → **生成 inputs.list** | `run.py`、`inputs.list` |
| | A1 | 档位断言 | — |
| | A2 | 写前 pin ＋ `index.before.txt`（**核 rc ＋ 非空**） | `rollback/*.pre.bin/.pre.meta` |
| | A3 | 输入冻结 before | `inputs.before.*`、`kb-info.before.sha256` |
| | **A4** | **`errset.before`（断言 rc==1）** | **`errset.before.json` ＋ `.rc`** |
| | A5 | `baseline`（显式路径 ＋ `--strict`） | `baseline.json` ＋ `.rc`、`phase.before.done` |
| | — | **停下，人工实施** | — |
| **B** | B0 | `run.sh after <RUN_DIR>`：核 RUN／驱动／阶段产物／未重复 | `phase.after.started` |
| | B1 | `after` | `after.json` ＋ `.rc` |
| | B2 | `errset.after`（断言 rc==1） | `errset.after.json` ＋ `.rc` |
| | B3 | 输入冻结 after ＋ 三条 diff | `inputs.after.*` |
| | B4 | JSON 逐件对照 | `COMPARE_PASS` |
| | B5 | rc 双侧断言 | — |
| | B6 | index（**核 rc ＋ 非空** ＋ 全文比对） | `index.after.txt`、`INDEX_UNCHANGED_OK` |
| | B7 | pytest（既有两条 ＋ 新增；含"会红"验证） | — |

## 6 pytest（阶段 B 第 B7 步）

```bash
python -m pytest tests/test_kb_hook_skipped.py tests/test_kb_placeholder_gate.py -q
python -m pytest tests/test_kb_parse_status.py -q
```

新增测试须含**「会红」验证**：注入一个应失败样例（如把 `checked` 计数故意改错 1），确认变红后再移除。

## 7 errset 档位因果（**撤销 v3 错误推论**）

> ❌ v3 曾写"若沙箱档位为 `off` 则占位校验不介入 ⇒ 不产生 bad"——**不成立**。
> `placeholder_gate` 只控制**占位校验**是否介入；**六必填为空／字段非法／`id` 非法等属 `validate_record` 的既有错误，与档位无关**，`off` 也不会让其消失。
> ⇒ 沙箱直接复制真库 `kb-info.yaml`（`ops=warn`）；`rc=1` 由**既有 bad** 决定；判据只看"**存在既有 bad ⇒ strict rc==1**"。

## 8 回退（引用，不重开设计审核）

按 **C4 v10 §16.2**：V1 原字节快照为唯一回退源；V2 **禁 `git checkout <pin> -- <path>`**，改字节写回／`Copy-Item`／`git cat-file`（核 `returncode==0`）；验证**唯一充分判据 `sha256(path)==SNAP_SHA256`**；新增测试回退＝删除。

## 9 范围外

不改 `knowledge/kb-info.yaml`、`.githooks/*`、`_match_family`/`entry_glob`、`kb-kit/**`；不新增 `id_prefix`；不延观察窗；不 push。

## 10 结论

| 项 | 结论 |
|---|---|
| 启动链 | **闭合**：`mkdir` → 落 `run.py` → 生成 `inputs.list` → `exec`（§5.1） |
| RUN 传递 | **显式传参**，无共享指针；阶段身份六件产物 ＋ 防重复 |
| Git 门 | **核 `returncode`** ＋ `kb_hook.py` index 行**非空断言** |
| 写入面 | **2 文件**，驱动全落 run 目录 |
| **状态** | **待批 · 未实施** |

## 11 变更记录

| 版本 | 变更 |
|---|---|
| v3 | R16：恒等式更正；errset 分件；停止门方向 |
| v4 | R17：代码语法自证；`errset.before` 前移；显式路径；唯一入口；撤销档位推论 |
| **v5** | **R18**：①**启动链闭合**——`run.sh` 单文件自包含（`cat > run.py <<'PYEOF_BOOT'` ＋ `python -c` 生成 `inputs.list`），给出 bootstrap-1/2/3 顺序，消除"空目录 exec 不存在的驱动"；②**删除 `.run-dir-pointer`**（仓库根、超辅助写面、共享可覆盖），`after` 显式接收 RUN 路径并核 RUN／驱动／六件产物／未重复跑；③**Git 失败门**——`git_ls_files` 统一核 `rc!=0 ⇒ die`，且 `scripts/kb_hook.py` 的 index 行必须非空（防两次失败空输出相等被误判未变）。静态自证：python 块 2/2 AST、bash 块 1/1 `bash -n`（Git Bash 5.2.37；本机 `bash` 为 WSL 因代理无法启动，属环境降级已记）。 |

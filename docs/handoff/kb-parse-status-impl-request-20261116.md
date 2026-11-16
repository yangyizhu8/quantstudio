# C4 解析状态可见化 —— **精确实施申请 v2**（待批 · 未实施）

> **版本**：**v3**（2026-11-16 R16 修正三项：**①展开恒等式更正＋集合身份判据 ②errset 改前/改后分存双断言 ③接通外层停止门＋run 目录排他新建**；承接 R15：①**冻结实际输入**（展开路径＋逐件 hash＋配置 hash，失配即停）②**真实比对退出码**（同输入同 strict 双跑、分别落 rc、覆盖既有错误返回 1 的分支）③**辅助写面单列**（唯一 UTC run 目录，禁字面占位/禁覆盖固定件））
> **依据**：C4 候选方案 v10 `docs/handoff/kb-parse-status-detection-design-20261115.md` §16.1
> **状态**：**待批 · 未实施**（本件仅申请工件；未改 `kb_hook`／测试／路由／档位，未运行回归、未动 index、未回退）

---

## 1 候选身份

| 件 | commit | size | sha256 |
|---|---|---|---|
| **C4 方案 v10** | `7bdeffb` | 39,951 B | `ceb4c110b15f453bd3aa35e5dcc893aed1fab493fc5a4905e40bc132df5791f5` |
| 配套《兜底设计 v2》 | — | — | `0c84c89bf573de3bcee1090b17ac70bec2ad23bd7b56e9c306e0eeab66662322` |

复核（只读）：`git show 7bdeffb:docs/handoff/kb-parse-status-detection-design-20261115.md | sha256sum`

---

## 2 实际文件清单（允许写入 · 精确路径 · 禁通配）

| # | 路径 | 动作 | 当前实测 |
|---|---|---|---|
| 1 | `scripts/kb_hook.py` | 改：候选识别 P1/P2/P3 ＋ 互斥桶 ＋ 可叠加标签 ＋ `parse_stats`/`parse_detail`/`skip_detail`/`level` | 18,568 B；sha256 `ce38ed739ab19504bd1066a6d3bca3f5d55b9b2c2c52aa33c87f2f102bca265f`；blob `90bd63232e3d` |
| 2 | `tests/test_kb_parse_status.py` | 新增 | **不存在** |

**清单外一律不动**（`knowledge/kb-info.yaml`、`.githooks/*`、`kb-kit/**`、其它测试文件）。

---

## 3 输入定义 ＋ **冻结实际输入（R15-①）**

### 3.1 glob 串（传给 `--files` 的原样文本）

```
knowledge/qmt/QD-*.md  knowledge/ptrade-deploy/PD-*.md  knowledge/db/DB-*.md  knowledge/ops/OPS-*.md
```
实测件数（2026-11-16 08:55）：qd 22 ＋ pd 22 ＋ db 44 ＋ ops 5 ＝ **93 件**。

### 3.2 **为什么必须冻结**（R15 判据）

> **同一 glob 串 ≠ 同一输入集合/同一内容**：前后两次运行之间，他线可能新增/删除/修改任一匹配文件。
> 因此**必须把"实际展开结果"与"每个文件的字节 hash"落盘，并在 before／after 之间比对；失配即停比较**（不是判 PASS/FAIL——输入已变时比较无意义）。

### 3.3 冻结三件（全部落本次 run 目录，见 §11）

| 文件 | 内容 | 生成命令 |
|---|---|---|
| `inputs.before.txt` | **实际展开路径清单**（相对路径、排序、逐行一个） | 见下 §3.4-① |
| `inputs.before.sha256` | **逐件字节 hash**（`<sha256>  <path>` 逐行，覆盖全部输入文件） | 见下 §3.4-② |
| `kb-info.before.sha256` | **配置 hash**（`knowledge/kb-info.yaml` 全文 sha256，单行） | 见下 §3.4-③ |

### 3.4 精确命令（跨平台，`RUN` 定义见 §11.1）

```bash
# ① 展开路径清单（与 --files 完全等价的 glob 展开，排序去重）
python -c "import glob,sys,io;ps=[];[ps.extend(sorted(glob.glob(p))) for p in ['knowledge/qmt/QD-*.md','knowledge/ptrade-deploy/PD-*.md','knowledge/db/DB-*.md','knowledge/ops/OPS-*.md']];ps=sorted({p.replace(chr(92),'/') for p in ps});io.open(r'$RUN_DIR/inputs.before.txt','w',encoding='utf-8',newline='\n').write('\n'.join(ps)+'\n');print(len(ps))"

# ② 逐件 hash（读字节，不经文本层）
python -c "import hashlib,io;s=io.open(r'$RUN_DIR/inputs.before.txt',encoding='utf-8').read().split();out=[];[out.append(hashlib.sha256(io.open(p,'rb').read()).hexdigest()+'  '+p) for p in s];io.open(r'$RUN_DIR/inputs.before.sha256','w',encoding='utf-8',newline='\n').write('\n'.join(out)+'\n');print(len(out))"

# ③ 配置 hash
python -c "import hashlib,io;h=hashlib.sha256(io.open('knowledge/kb-info.yaml','rb').read()).hexdigest();io.open(r'$RUN_DIR/kb-info.before.sha256','w',encoding='utf-8',newline='\n').write(h+'\n');print(h)"
```

### 3.5 after 侧同法 ＋ **失配即停**（写死）

```bash
# after 侧生成 inputs.after.txt / inputs.after.sha256 / kb-info.after.sha256（命令同上，替换 before→after）
# 三路比对（任一不符 ⇒ 打印 MISMATCH 并 exit 9，禁止继续比较 JSON）
python -c "import sys,io;a=io.open(r'$RUN_DIR/inputs.before.txt',encoding='utf-8').read();b=io.open(r'$RUN_DIR/inputs.after.txt',encoding='utf-8').read();h1=io.open(r'$RUN_DIR/inputs.before.sha256',encoding='utf-8').read();h2=io.open(r'$RUN_DIR/inputs.after.sha256',encoding='utf-8').read();c1=io.open(r'$RUN_DIR/kb-info.before.sha256',encoding='utf-8').read().strip();c2=io.open(r'$RUN_DIR/kb-info.after.sha256',encoding='utf-8').read().strip();bad=[];bad+=['路径集合变化'] if a!=b else [];bad+=['逐件 hash 变化'] if h1!=h2 else [];bad+=['配置 hash 变化'] if c1!=c2 else [];print('MISMATCH: '+'; '.join(bad)) if bad else print('INPUTS_FROZEN_OK');sys.exit(9 if bad else 0)"
```
- **退出码语义**：`0`＝输入已冻结一致，可继续比较；`9`＝**输入失配，停止比较**（须查明是哪个文件变了：`diff inputs.before.sha256 inputs.after.sha256`）。

**3.5.1 一致性核对（公式已更正 · R16）**

> ⚠️ **更正声明**：v2.1 写的 `输入文件数 == total + len(skipped)` **错误**——`total` **已包含** `skipped`，再加属**重复计数**。
> 源码依据（只读核对 `scripts/kb_hook.py`）：
> - `L303`：`out = {"total":0, "checked":0, "bad":[], "skipped":[], ...}`
> - `L306`：`out["total"] = len(expanded) + len(glob_misses)`
> - `L307-308`：`for pat in glob_misses: out["skipped"].append({"file": pat, "reason": "glob_no_match"})`
> ⇒ **`glob_misses` 的每一项都被计入 `skipped`**，且以**模式串**（非文件路径）作为 `file`。

正确关系：

| 量 | 含义 |
|---|---|
| `expanded` | `--files` 展开后的**真实文件路径**集合（不入 payload，需外部展开对照） |
| `glob_misses` | **未命中的模式串**集合（入 `skipped`，`reason="glob_no_match"`） |
| `total` | `len(expanded) + len(glob_misses)` |
| `skipped` | **两类混装**：`{file: 模式串, reason: glob_no_match}` ＋ `{file: 路径, reason: <其他>}` |
| `checked` | `len(expanded) − (skipped 中路径类条目数)` |

| 恒等式 | 状态 |
|---|---|
| `total = checked + len(skipped)` | **成立**（既有式(1)，锁定于 `tests/test_kb_hook_skipped.py`） |
| `len(expanded) = total − n_glob_miss` | **本步核对用此式** |

核对命令（`n_glob_miss` 从 `skipped` 里按 `reason` 过滤计数，**不用 `len(skipped)`**）：

```bash
python -c "import json,io,os,sys;run=r'$RUN_DIR';n=len([l for l in io.open(os.path.join(run,'inputs.before.txt'),encoding='utf-8').read().split('\n') if l.strip()]);j=json.load(io.open(os.path.join(run,'baseline.json'),encoding='utf-8'));gm=len([s for s in j.get('skipped',[]) if s.get('reason')=='glob_no_match']);m=j['total']-gm;print('inputs=%d expanded_by_hook=%d glob_miss=%d match=%s'%(n,m,gm,n==m));sys.exit(0 if n==m else 9)"
```

> **★ 但件数一致≠集合相同**（R16 判据）。本步**只作"我方 glob 与 `kb_hook` 展开是否等价"的实现偏差探测**；
> **"同输入"的唯一充分证明是 §3.5 的两条 diff**：
> 1. `diff inputs.before.txt inputs.after.txt` **为空** ⇒ **路径集合身份**；
> 2. `diff inputs.before.sha256 inputs.after.sha256` **为空** ⇒ **逐件内容身份**。
> ⇒ 件数核对**不得**单独作为"同输入"的结论依据；若件数一致但集合 diff 非空 ⇒ **以 diff 为准，判 MISMATCH**。

---

## 4 配置基线（须保持）

```
qd=off  pd=off  db=off  ops=warn   ops.placeholder_gate_since=2026-11-13T20:56:02+08:00
```
断言见 §5 第 0 步；变化 ⇒ 本次实施暂停（观察窗 2026-11-20T20:56:50+08:00 到期后按原批准处理）。

---

## 5 **回归命令（同输入 · 同 strict · 退出码实采）**

> **R15-② 核心**：before／after **必须同输入、同 `--strict` 模式**，**分别保存进程退出码**再比较；
> **不得**用"after 单跑 ops 再 `echo`"代替 93 件整次的退出码比对。

### 第 0 步 · 写前 pin ＋ 档位断言 ＋ index 记录

```bash
# 0.1 档位断言
python -c "import yaml,io;d=yaml.safe_load(io.open('knowledge/kb-info.yaml',encoding='utf-8'));g={f['id']:(f.get('placeholder_gate'),f.get('placeholder_gate_since')) for f in d['families'] if 'placeholder_gate' in f};print(g);assert {k:v[0] for k,v in g.items()}=={'qd':'off','pd':'off','db':'off','ops':'warn'};assert g['ops'][1]=='2026-11-13T20:56:02+08:00'"

# 0.2 原字节快照（唯一回退源，见 C4 v10 §16.2-N6-V1）
python -c "import shutil,hashlib,os;src='scripts/kb_hook.py';dst=os.path.join(r'$RUN_DIR','rollback','scripts__kb_hook.py.pre.bin');os.makedirs(os.path.dirname(dst),exist_ok=True);shutil.copyfile(src,dst);d=open(dst,'rb').read();h=hashlib.sha256(d).hexdigest();open(dst+'.meta','w',encoding='utf-8',newline='\n').write(h+'\n'+str(len(d))+'\n'+src+'\n');print(h,len(d))"

# 0.3 index 条目记录（mode+blob+stage+path）
git -c core.quotepath=false ls-files -s -- scripts/kb_hook.py tests/test_kb_parse_status.py > "$RUN_DIR/index.before.txt"

# 0.4 状态登记（辅助，不作回退源）
git -c core.quotepath=false status --porcelain -- scripts/kb_hook.py tests/test_kb_parse_status.py > "$RUN_DIR/status.before.txt"
```

### 第 1 步 · **baseline（含 rc）**——同 strict 模式

```bash
python -c "import subprocess,sys,os;out=os.path.join(r'$RUN_DIR','baseline.json');rcf=out+'.rc';a=[sys.executable,'scripts/kb_hook.py','--repo-root','.','--strict','--json','--files','knowledge/qmt/QD-*.md','knowledge/ptrade-deploy/PD-*.md','knowledge/db/DB-*.md','knowledge/ops/OPS-*.md'];r=subprocess.run(a,capture_output=True);open(out,'wb').write(r.stdout);open(rcf,'w').write(str(r.returncode));print('baseline rc=%d bytes=%d'%(r.returncode,len(r.stdout)));sys.exit(0)"
```

### 第 2 步 · 实施（仅 §2 两文件）

### 第 3 步 · **after（含 rc）**——**同一命令、同一 glob、同一 `--strict`**

```bash
python -c "import subprocess,sys,os;out=os.path.join(r'$RUN_DIR','after.json');rcf=out+'.rc';a=[sys.executable,'scripts/kb_hook.py','--repo-root','.','--strict','--json','--files','knowledge/qmt/QD-*.md','knowledge/ptrade-deploy/PD-*.md','knowledge/db/DB-*.md','knowledge/ops/OPS-*.md'];r=subprocess.run(a,capture_output=True);open(out,'wb').write(r.stdout);open(rcf,'w').write(str(r.returncode));print('after rc=%d bytes=%d'%(r.returncode,len(r.stdout)));sys.exit(0)"
```

### 第 4 步 · 输入冻结复核（**必须在比较 JSON 之前**）

```bash
# 见 §3.5：exit 0 才可继续；exit 9 ⇒ 停止
```

### 第 5 步 · **JSON 逐件对照**

```bash
python "$RUN_DIR/compare.py" "$RUN_DIR/baseline.json" "$RUN_DIR/after.json"
```
`compare.py`（落 run 目录，**不进 `scripts/`**）判据：
1. 既有键**逐位相同**：`total`/`checked`/`bad`/`skipped`/`families_used`/`note`；
2. `bad` 为**逐件错误文本**逐条相同（`{file: '|'.join(errors)}` 映射比对），非仅件数；
3. 仅允许**新增** `parse_stats`/`parse_detail`/`skip_detail`（含其内 `level`/`tags`）；
4. 任一不符 ⇒ `FAIL`，`exit 1`。

### 第 6 步 · **退出码比对（含既有错误分支 · 改前改后各采一次）**

**6.1 全量 93 件整次 rc**（同输入同 `--strict`，第 1／3 步已分别落 `.rc`）

```bash
python -c "import io,os,sys;run=r'$RUN_DIR';b=io.open(os.path.join(run,'baseline.json.rc')).read().strip();a=io.open(os.path.join(run,'after.json.rc')).read().strip();print('baseline_rc=%s after_rc=%s match=%s'%(b,a,b==a));sys.exit(0 if b==a else 9)" || { echo "STOP: 整次退出码不一致"; exit 9; }
```

**6.2 含既有错误分支（`rc=1`）：改前、改后**各跑一次并**分存**，不得覆盖改前证据**

构造沙箱（不污染真库；`trigger` 置空 ⇒ 命中"六必填为空"现有 `bad` ⇒ `--strict` 应 `rc=1`）：

```bash
python - <<'PY'
import os, shutil, tempfile, io, sys
t = tempfile.mkdtemp(prefix='kb_err_')
os.makedirs(os.path.join(t, 'knowledge', 'ops'), exist_ok=True)
shutil.copyfile('knowledge/kb-info.yaml', os.path.join(t, 'knowledge', 'kb-info.yaml'))
io.open(os.path.join(t, 'knowledge', 'ops', 'OPS-2026-9004.md'),
        'w', encoding='utf-8', newline='
').write('---
id: OPS-2026-9004
trigger: ""
---
')
io.open(os.path.join(os.environ['RUN_DIR'], 'errset.sandbox.path'), 'w', encoding='utf-8').write(t)
print('sandbox =', t)
PY
```

**改前采集**（写入 `errset.before.json` ＋ `.rc`）：

```bash
SB=$(cat "$RUN_DIR/errset.sandbox.path")
python -c "import subprocess,sys,os;sb=open(r'$RUN_DIR/errset.sandbox.path',encoding='utf-8').read().strip();out=os.path.join(r'$RUN_DIR','errset.before.json');a=[sys.executable,'scripts/kb_hook.py','--repo-root',sb,'--kb-info','knowledge/kb-info.yaml','--strict','--json','--files','knowledge/ops/OPS-*.md'];r=subprocess.run(a,capture_output=True);open(out,'wb').write(r.stdout);open(out+'.rc','w').write(str(r.returncode));print('errset.before rc=%d (期望 1)'%r.returncode);sys.exit(0)"
```

**改后采集**（另存 `errset.after.json` ＋ `.rc`，**不触碰 before 文件**）：

```bash
SB=$(cat "$RUN_DIR/errset.sandbox.path")
python -c "import subprocess,sys,os;sb=open(r'$RUN_DIR/errset.sandbox.path',encoding='utf-8').read().strip();out=os.path.join(r'$RUN_DIR','errset.after.json');a=[sys.executable,'scripts/kb_hook.py','--repo-root',sb,'--kb-info','knowledge/kb-info.yaml','--strict','--json','--files','knowledge/ops/OPS-*.md'];r=subprocess.run(a,capture_output=True);open(out,'wb').write(r.stdout);open(out+'.rc','w').write(str(r.returncode));print('errset.after rc=%d (期望 1)'%r.returncode);sys.exit(0)"
```

**断言（双侧均为 1 且相等；任一不符即停）**：

```bash
python -c "import io,os,sys;run=r'$RUN_DIR';b=io.open(os.path.join(run,'errset.before.json.rc')).read().strip();a=io.open(os.path.join(run,'errset.after.json.rc')).read().strip();ok=(b=='1' and a=='1');print('errset.before=%s errset.after=%s both_1=%s'%(b,a,ok));sys.exit(0 if ok else 9)" || { echo "STOP: 含既有错误分支未双侧返回 1"; exit 9; }
```

> **改前证据保护**：`errset.before.*` 一经写入**不得被后续步骤覆盖**（run 目录排他新建已保证跨轮不覆盖；本步 within-run 也只用不同文件名）。
> **前提核对**（实施时先在沙箱空转一次）：确认 `--repo-root` ＋ `--kb-info knowledge/kb-info.yaml` 组合确实读到沙箱配置、且 `trigger` 为空确实产 `bad`（若沙箱档位为 `off` 则占位校验不介入，须确认 `ops` 在沙箱配置里仍为 `warn`）。

### 第 7 步 · 测试

```bash
python -m pytest tests/test_kb_hook_skipped.py tests/test_kb_placeholder_gate.py -q
python -m pytest tests/test_kb_parse_status.py -q
```
新增测试须含**「会红」验证**：注入一个应失败样例（如把 `checked` 计数故意改错 1），确认测试**变红**后再移除注入。

### 第 8 步 · index 未变（mode＋blob）

```bash
git -c core.quotepath=false ls-files -s -- scripts/kb_hook.py tests/test_kb_parse_status.py > "$RUN_DIR/index.after.txt"
diff "$RUN_DIR/index.before.txt" "$RUN_DIR/index.after.txt"
```
- 判据：**目标路径 `mode` 与 `blob` 一致**（`ls-files -s` 逐行 `mode blob stage<TAB>path`）；
- **仅比清单内 2 路径**；**清单外 index 变化属他线正常活动，不归责本席**；
- **禁用**仅 `git diff --cached --name-only`（名称相同、内容可变）。

---

## 6 回退（引用，不重开设计审核）

严格按 **C4 v10 §16.2**：V1 原字节快照为唯一回退源（`SNAP_SHA256`/`SNAP_SIZE`）；V2 **禁 `git checkout <pin> -- <path>`**，改 `wb` 字节写回／`Copy-Item`／`git cat-file`（核 `returncode==0`）；验证**唯一充分判据 `sha256(path)==SNAP_SHA256`**，`git status` 非空不构成失败；新增测试文件回退＝删除。

---

## 7 范围外声明

不改 `knowledge/kb-info.yaml`、`.githooks/*`、`_match_family`/`entry_glob`（路由语义不变）、`kb-kit/**` 副本；不新增 `id_prefix`；不延 ops 观察窗；不 push。

---

## 11 **辅助写面单列（R15-③）**

> **原则**：①**一律落本次 run 的唯一 UTC 目录**（**禁字面时间占位**，执行时取实际时间）；②**禁反复覆盖固定基线件**（如 `docs/evidence/kb-parse-baseline.json` 这类固定名会在多次运行间互相覆盖——**全部改为 run 目录内唯一路径**）。

### 11.1 `RUN` / `RUN_DIR` 定义（三选一，取实际 UTC）

```bash
# Git Bash / WSL
RUN=$(date -u +%Y%m%dT%H%M%SZ); RUN_DIR="docs/evidence/runs/${RUN}-c4-impl"; mkdir -p "$RUN_DIR/rollback"

# PowerShell
#   $RUN = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'); $RUN_DIR = "docs/evidence/runs/$RUN-c4-impl"; New-Item -ItemType Directory -Force "$RUN_DIR\rollback" | Out-Null

# 跨平台（python 取 UTC，避免本地时区）
#   RUN_DIR=$(python -c "import datetime,os;p='docs/evidence/runs/'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-c4-impl';os.makedirs(p+'/rollback',exist_ok=True);print(p)")
```

### 11.2 辅助写面清单（全部在 `$RUN_DIR` 内，互不覆盖）

| 文件 | 用途 | 生成步 |
|---|---|---|
| `rollback/scripts__kb_hook.py.pre.bin` | 回退源（原字节快照） | 第 0.2 步 |
| `rollback/scripts__kb_hook.py.pre.meta` | `sha256`／`size`／源路径／UTC | 第 0.2 步 |
| `index.before.txt` / `index.after.txt` | index `mode+blob` 记录 | 第 0.3 / 8 步 |
| `status.before.txt` / `status.after.txt` | `git status` 辅助登记（**非判据**） | 第 0.4 / 8 步 |
| `inputs.before.txt` / `inputs.after.txt` | **实际展开路径清单** | §3.4 / §3.5 |
| `inputs.before.sha256` / `inputs.after.sha256` | **逐件字节 hash** | §3.4 / §3.5 |
| `kb-info.before.sha256` / `kb-info.after.sha256` | **配置 hash** | §3.4 / §3.5 |
| `baseline.json` / `baseline.json.rc` | baseline（JSON ＋ **进程退出码**） | 第 1 步 |
| `after.json` / `after.json.rc` | after（JSON ＋ **进程退出码**） | 第 3 步 |
| `errset.json` / `errset.json.rc` | 含既有错误输入集的 rc（覆盖 1 分支） | 第 6.2 步 |
| `compare.py` | 逐件对照脚本（**临时件，落 run 目录，不进 `scripts/`**） | 第 5 步前写入 |
| `compare.log` | 对照输出（PASS/FAIL ＋ 差异清单） | 第 5 步 |

### 11.3 禁止项（写死）

| 禁止 | 原因 |
|---|---|
| 文档/命令中保留**字面时间占位符**（未替换成实际 UTC） | 执行者会照抄成非法路径 |
| 用**固定文件名**存 baseline（如 `docs/evidence/kb-parse-baseline.json`） | 多次运行互相覆盖，历史对比失效 |
| 把 `compare.py` 写进 `scripts/` | 违反 §2 允许写入清单（清单外改动＝违约） |
| 快照与日志混放同一文件 | 快照需字节保真，日志是文本 |

---

### 11.4 run 目录**排他新建**（R16）

```bash
RUN=$(python -c "import datetime;print(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))")
RUN_DIR="docs/evidence/runs/${RUN}-c4-impl"
mkdir "$RUN_DIR"          # 不用 -p：目录已存在 ⇒ mkdir 失败（排他，防覆盖既有材料）
mkdir "$RUN_DIR/rollback"
```

> 若 `mkdir "$RUN_DIR"` 失败 ⇒ **立即停止**（不换名重试、不写入既有目录）——说明同秒内已有材料或路径被占。

### 11.5 **外层停止门**（R16：普通 Bash 逐行执行**不会**因 Python 非 0 自动停）

> **问题**：`python -c "...; sys.exit(9)"` 单独执行后，shell 默认继续执行下一行——**门形同虚设**。
> **要求**（三选一，必须落地）：
> 1. **单入口脚本**：全部步骤收敛进一个 `.sh`，首行 `set -euo pipefail`；
> 2. 或**逐行串联**：每步写成 `cmd || { echo "STOP: <步名>"; exit 9; }`；
> 3. 或 `cmd && next` 链式（不推荐，长链可读性差）。
> **禁止**：把判定命令单独粘贴后忽略退出码继续下一步；禁止以"我看输出是 PASS/FAIL"代替退出码判定。
> **失败材料保留**：脚本失败时**不得清理** `$RUN_DIR`（禁 `trap ... EXIT` 内 `rm -rf`、禁失败后手动删目录）——失败材料正是取证对象。

**单入口脚本骨架**（实施时按第 0→8 步填入）：

```bash
#!/usr/bin/env bash
set -euo pipefail
RUN=$(python -c "import datetime;print(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))")
RUN_DIR="docs/evidence/runs/${RUN}-c4-impl"
export RUN_DIR
mkdir "$RUN_DIR"; mkdir "$RUN_DIR/rollback"
# 第0步 pin/档位/index → 第1步 baseline+rc → 【实施】 → 第3步 after+rc
# → 第4步 输入冻结 diff（失配 exit 9）→ 第5步 JSON 对照 → 第6步 rc＋errset
# → 第7步 pytest → 第8步 index mode+blob
```

## 12 申请结论

| 项 | 结论 |
|---|---|
| 设计依据 | C4 v10（`ceb4c110…`）已过 R5–R13 退修 |
| 写入面 | **2 文件**（1 改 1 增），清单外零改动 |
| 输入冻结 | §3（**展开路径＋逐件 hash＋配置 hash**；**集合身份以两条 diff 为准**，件数仅辅助；失配 exit 9） |
| 退出码 | §5 第 1/3/6 步（**同 strict 双跑、分别落 rc、覆盖 1 分支**） |
| 辅助写面 | §11（**唯一 UTC run 目录**、互不覆盖、禁字面占位） |
| 回退 | §6（引用 C4 §16.2 V1/V2，不重开审核） |
| **状态** | **待批 · 未实施** |

---

## 13 本版变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v2.1 | 2026-11-16 | R15 三项收口（输入冻结／退出码实采／辅助写面单列） |
| **v3** | **2026-11-16（R16）** | **①恒等式更正**：删除错误的 `total + len(skipped)`（`total` 已含 `skipped`，重复计数），改据源码更正为 `len(expanded) = total − n_glob_miss`（`n_glob_miss` 从 `skipped` 按 `reason="glob_no_match"` 计数），并**明写件数一致≠集合相同**——集合身份由 `diff inputs.before/after.txt`（路径）与 `diff *.sha256`（内容）两条承担，件数仅作实现偏差探测；**②errset 改前/改后分存**：`errset.before.json(.rc)` 于**实施前**采集、`errset.after.json(.rc)` 于实施后另存，断言**双侧均 == 1 且相等**，任一不符 exit 9，且**明令 before 文件不得被覆盖**（附沙箱构造＋空转前提核对）；**③接通外层停止门**：新增 §11.5（`set -euo pipefail` 单入口脚本／`|| { echo STOP; exit 9; }` 串联／禁忽略退出码），§11.4 **run 目录排他新建**（`mkdir` 不带 `-p`，已存在即停），**失败材料一律保留**（禁 trap 清理）。边界不变：仅文档，未改 `kb_hook`／测试／路由／档位，未运行回归/实验；11-20 20:56:50 原 ops 回 off、清 since 保持。 |

# C4 解析状态可见化 —— **精确实施申请 v2**（待批 · 未实施）

> **版本**：**v2**（2026-11-16 R15 修正三项：①**冻结实际输入**（展开路径＋逐件 hash＋配置 hash，失配即停）②**真实比对退出码**（同输入同 strict 双跑、分别落 rc、覆盖既有错误返回 1 的分支）③**辅助写面单列**（快照/日志/临时脚本一律落唯一 UTC run 目录，禁字面时间占位、禁覆盖固定件））
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

**3.5.1 展开一致性交叉核对（防止"我的 glob"与"`kb_hook` 的 glob"不等价）**

> 我的 `inputs.before.txt` 由独立 `glob` 生成，而 `kb_hook` 用自己的 `_expand_files` 展开；两者**必须等价**，否则"冻结输入"冻结的是错的集合。
> 恒等式：**输入文件总数 ＝ `total` ＋ `len(skipped)`**（因为 `total = checked + len(skipped)`，且每个输入文件恰落入 checked 或 skipped）。

```bash
python -c "import json,io,os,sys;n=len([l for l in io.open(os.path.join(r'$RUN_DIR','inputs.before.txt'),encoding='utf-8').read().split('\n') if l.strip()]);j=json.load(open(os.path.join(r'$RUN_DIR','baseline.json'),encoding='utf-8'));m=j['total']+len(j['skipped']);print('inputs=%d  kb_hook(total+skipped)=%d  match=%s'%(n,m,n==m));sys.exit(0 if n==m else 1)"
```
- `match=True` ⇒ 展开等价，`inputs.before.sha256` 即为**真实输入集合**的逐件 hash；
- `match=False` ⇒ **停止**，先查明差异（常见：`glob` 命中了 `kb_hook` 因族路由丢掉的路径，或反之），**不得**带着不等价集合继续比对。

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

### 第 6 步 · **退出码比对（R15-② 主场）**

```bash
python -c "import io,sys,os;b=io.open(os.path.join(r'$RUN_DIR','baseline.json.rc')).read().strip();a=io.open(os.path.join(r'$RUN_DIR','after.json.rc')).read().strip();print('baseline_rc=%s after_rc=%s match=%s'%(b,a,b==a));sys.exit(0 if b==a else 1)"
```
**并须覆盖「既有错误返回 1」的分支**（否则只证明了 0→0）：

```bash
# 6.2 含既有错误的输入集（组合 6 件，来自 C4 v10 §9 沙箱构造；在临时目录生成，不污染真库）
#     目的：before/after 各跑一次，rc 都必须为 1 且相等
python -c "import subprocess,sys,os,tempfile,shutil,io,yaml;t=tempfile.mkdtemp(prefix='kb_err_');os.makedirs(os.path.join(t,'knowledge','ops'),exist_ok=True);open(os.path.join(t,'knowledge','ops','OPS-2026-9004.md'),'w',encoding='utf-8').write('---\nid: OPS-2026-9004\ntrigger: \"\"\n---\n');shutil.copyfile('knowledge/kb-info.yaml',os.path.join(t,'kb-info.yaml'));r=subprocess.run([sys.executable,'scripts/kb_hook.py','--repo-root',t,'--kb-info',os.path.join(t,'kb-info.yaml'),'--strict','--json','--files','knowledge/ops/OPS-*.md'],capture_output=True);open(os.path.join(r'$RUN_DIR','errset.json'),'wb').write(r.stdout);open(os.path.join(r'$RUN_DIR','errset.json.rc'),'w').write(str(r.returncode));print('errset rc=%d (期望 1)'%r.returncode);shutil.rmtree(t,ignore_errors=True)"
```
- **判据**：`errset.json.rc` 在 before／after **都为 `1`** 且相等 ⇒ 证明"含既有错误的运行仍返 1"；
- 若 before 侧该构造 rc 不为 1（例如档位/判据不同导致），**先查明**再比对，不得直接沿用。

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

## 12 申请结论

| 项 | 结论 |
|---|---|
| 设计依据 | C4 v10（`ceb4c110…`）已过 R5–R13 退修 |
| 写入面 | **2 文件**（1 改 1 增），清单外零改动 |
| 输入冻结 | §3（**展开路径＋逐件 hash＋配置 hash**，**失配即停**，exit 9） |
| 退出码 | §5 第 1/3/6 步（**同 strict 双跑、分别落 rc、覆盖 1 分支**） |
| 辅助写面 | §11（**唯一 UTC run 目录**、互不覆盖、禁字面占位） |
| 回退 | §6（引用 C4 §16.2 V1/V2，不重开审核） |
| **状态** | **待批 · 未实施** |

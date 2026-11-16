# C4 解析状态可见化 —— **精确实施申请**（待批 · 未实施）

> **申请日期**：2026-11-16（R14 · DRIFT-N6-r1）
> **依据**：C4 候选方案 v10 `docs/handoff/kb-parse-status-detection-design-20261115.md` **§16.1**（实施申请须附五件套）
> **状态**：**待批**。本件为申请工件，**未实施任何改动**（未改 `kb_hook`／路由／档位／契约，未回退、未跑测试、未分发）。

---

## 1 候选身份（申请所依据的设计件）

| 件 | commit | size | sha256 |
|---|---|---|---|
| **C4 方案 v10**（主锚） | `7bdeffb` | 39,951 B | `ceb4c110b15f453bd3aa35e5dcc893aed1fab493fc5a4905e40bc132df5791f5` |
| 配套《兜底设计 v2》 | — | — | `0c84c89bf573de3bcee1090b17ac70bec2ad23bd7b56e9c306e0eeab66662322` |

复核命令（只读）：`git show 7bdeffb:docs/handoff/kb-parse-status-detection-design-20261115.md | sha256sum`

---

## 2 **实际文件清单（允许写入 · 精确路径 · 禁通配）**

| # | 路径 | 动作 | 当前状态（实测） |
|---|---|---|---|
| 1 | `scripts/kb_hook.py` | **改**：新增候选识别（P1/P2/P3）＋互斥桶（`ok`/`empty`/`fail`）＋可叠加标签＋`parse_stats`/`parse_detail`/`skip_detail`/`level` 输出；`main` 文本面改逐件 | **18,568 B**，sha256 `ce38ed739ab19504bd1066a6d3bca3f5d55b9b2c2c52aa33c87f2f102bca265f`，blob `90bd63232e3d` |
| 2 | `tests/test_kb_parse_status.py` | **新增**：桶互斥／标签可叠加／三守恒式／E 类可见／退出码零变化 | **不存在**（新增） |

**清单外一律不动**（含 `knowledge/kb-info.yaml`、`.githooks/*`、`kb-kit/**`、其它测试文件）。清单外任何改动 ⇒ 违约。

---

## 3 **输入列表（baseline 与 after 必须用同一份）**

| 族 | glob（原样传 `--files`） | 实测件数 |
|---|---|---|
| qd | `knowledge/qmt/QD-*.md` | 22 |
| pd | `knowledge/ptrade-deploy/PD-*.md` | 22 |
| db | `knowledge/db/DB-*.md` | 44 |
| ops | `knowledge/ops/OPS-*.md` | 5 |
| — | **合计** | **93** |

> 实测时点 2026-11-16 08:55（`glob` 展开，真库四族）。**若实施当日件数变化**，以实施日 `baseline.json` 的 `total` 为准并在证据件记明差额；**after 必须用完全相同的 glob 串**。

---

## 4 配置基线（必须保持）

```
qd=off  pd=off  db=off  ops=warn   ops.placeholder_gate_since=2026-11-13T20:56:02+08:00
```
断言命令见 §5 第 0 步；**档位/since 一旦变化 ⇒ 本次实施暂停**（观察窗于 2026-11-20T20:56:50+08:00 到期后另按原批准处理）。

---

## 5 **回归命令（原样可复制）**

### 第 0 步 · 写前 pin ＋ 档位断言 ＋ index 记录（**未记录不得开工**）

```bash
# 0.1 档位断言
python -c "import yaml,io;d=yaml.safe_load(io.open('knowledge/kb-info.yaml',encoding='utf-8'));g={f['id']:(f.get('placeholder_gate'),f.get('placeholder_gate_since')) for f in d['families'] if 'placeholder_gate' in f};print(g);assert {k:v[0] for k,v in g.items()}=={'qd':'off','pd':'off','db':'off','ops':'warn'};assert g['ops'][1]=='2026-11-13T20:56:02+08:00'"

# 0.2 原字节快照（N6-V1：唯一回退源）
python -c "import shutil,hashlib,os;src='scripts/kb_hook.py';dst='docs/evidence/rollback-snapshots/scripts__kb_hook.py.pre-<UTC>.bin';os.makedirs(os.path.dirname(dst),exist_ok=True);shutil.copyfile(src,dst);d=open(dst,'rb').read();print(hashlib.sha256(d).hexdigest(),len(d))"

# 0.3 index 条目记录（R14：mode+blob+stage+path，非仅 name-only）
git -c core.quotepath=false ls-files -s -- scripts/kb_hook.py tests/test_kb_parse_status.py > docs/evidence/kb-parse-index-before.txt

# 0.4 状态登记（辅助，不作回退源）
git -c core.quotepath=false status --porcelain -- scripts/kb_hook.py tests/test_kb_parse_status.py
```

### 第 1 步 · baseline（实施前）

```bash
python scripts/kb_hook.py --repo-root . --json \
  --files "knowledge/qmt/QD-*.md" "knowledge/ptrade-deploy/PD-*.md" \
          "knowledge/db/DB-*.md" "knowledge/ops/OPS-*.md" \
  > docs/evidence/kb-parse-baseline.json
```

### 第 2 步 · 实施（仅第 2 节清单内两文件）

### 第 3 步 · after（实施后，**同一命令同一 glob**）

```bash
python scripts/kb_hook.py --repo-root . --json \
  --files "knowledge/qmt/QD-*.md" "knowledge/ptrade-deploy/PD-*.md" \
          "knowledge/db/DB-*.md" "knowledge/ops/OPS-*.md" \
  > docs/evidence/kb-parse-after.json
```

### 第 4 步 · **baseline／after 逐件对照**（判据实现，内联不落仓）

```bash
# 主径（Git Bash / WSL）
python - <<'PY'
import json,sys
b=json.load(open('docs/evidence/kb-parse-baseline.json',encoding='utf-8'))
a=json.load(open('docs/evidence/kb-parse-after.json',encoding='utf-8'))
ALLOW={'parse_stats','parse_detail','skip_detail'}
fail=[]
for k in sorted(set(b)|set(a)):
    if k in ALLOW: continue                      # 仅允许新增这三键
    if b.get(k)!=a.get(k):
        fail.append('既有键变化: %s  %r -> %r'%(k,b.get(k),a.get(k)))
def badmap(x): return {i['file']:'|'.join(i['errors']) for i in x.get('bad',[])}
bb,aa=badmap(b),badmap(a)
for f in sorted(set(bb)|set(aa)):
    if bb.get(f)!=aa.get(f):
        fail.append('逐件 bad 差异: %s  %r -> %r'%(f,bb.get(f),aa.get(f)))
print('FAIL' if fail else 'PASS')
for x in fail: print(' -',x)
sys.exit(1 if fail else 0)
PY

# 备径（PowerShell / cmd：先落临时脚本，不写入仓库）
#   python %TEMP%\kb_cmp.py   ← 内容同上
```

**对照判据（写死）**：
1. **既有键逐位相同**：`total` / `checked` / `bad` / `skipped` / `families_used` / `note`（其中 `bad` 为**逐件错误文本**相同，非仅件数）；
2. **允许的新增**：仅 `parse_stats` / `parse_detail` / `skip_detail`（及其内部 `level`／`tags` 字段）；
3. **整次退出码相同**（见第 6 步）；
4. 任一不符 ⇒ **FAIL ⇒ 按 §7 回退**。

### 第 5 步 · 测试

```bash
python -m pytest tests/test_kb_hook_skipped.py tests/test_kb_placeholder_gate.py -q   # 既有，须仍全绿（含守恒断言 L81/L92/L131）
python -m pytest tests/test_kb_parse_status.py -q                                      # 新增，须全绿
```

**新增测试必须含「会红」验证**（防止恒绿脚手架）：注入一个应失败样例（如故意把 `checked` 计数改错 1），确认该测试**确实变红**后再移除注入。

### 第 6 步 · 退出码复核（单件 vs 整次）

```bash
python scripts/kb_hook.py --repo-root . --strict --json --files "knowledge/ops/OPS-*.md" > /dev/null; echo "strict_rc=$?"
```
- 基线（93 件）实测整次 `--strict` rc 应 **0**；若实施后 **仍 0** ⇒ 与"只增诊断"一致；
- **若输入中含既有错误**（如手工构造的组合件），rc 应为 **1** 且**与基线相同**——**不得把"含旧错误的整次运行"报成 0**。

---

## 7 **index 未变验证（R14 加强：比 mode+blob，不只 `--name-only`）**

> **理由**：`git diff --cached --name-only` 只比**路径名**——同一暂存路径**内容变了名称仍相同**，故不能证明 index 未变。

```bash
# 恢复/实施后
git -c core.quotepath=false ls-files -s -- scripts/kb_hook.py tests/test_kb_parse_status.py > docs/evidence/kb-parse-index-after.txt
diff docs/evidence/kb-parse-index-before.txt docs/evidence/kb-parse-index-after.txt
```

**判据**：
1. **目标路径的 `mode` 与 `blob` 必须一致**（`ls-files -s` 输出逐行 `mode blob stage<TAB>path`）；
2. **仅比对清单内 2 个路径**；
3. **清单外路径的 index 变化属他线正常活动，不归责本席**（不纳入本条判定，亦不得据此阻断）；
4. `docs/evidence/kb-parse-index-before.txt` 须在**实施前**生成（第 0.3 步），否则本判据不成立。

---

## 8 回退（引用，不重复）

回退**严格按 C4 方案 v10 §16.2** 执行：
- **V1**：回退源＝**原字节快照**（`SNAP_SHA256`／`SNAP_SIZE`），`HEAD:path` 仅辅助；
- **V2**：**禁 `git checkout <pin> -- <path>`**（改 index ＋ 过 autocrlf/filter），改 `wb` 字节写回／`Copy-Item`／`git cat-file`（核 `returncode==0`）；
- **验证**：`sha256(path)==SNAP_SHA256` 为**唯一充分判据**；`git status` **非空不构成失败**。

本次需复原的文件同 §2 清单（`scripts/kb_hook.py`；新增测试文件若已创建则**删除**而非"复原"——删除前先确认其未被任何提交引用）。

---

## 9 **不做的事（范围外声明）**

不改 `knowledge/kb-info.yaml`（档位/since）、不改 `.githooks/*`、不改 `_match_family`／`entry_glob`（**路由语义不变**）、不同步 `kb-kit/**` 副本、不新增 `id_prefix` 配置键、不延 ops 观察窗、不 push。

---

## 10 申请结论

| 项 | 结论 |
|---|---|
| 设计依据 | C4 v10（`ceb4c110…`）已过 R5–R13 退修，**本申请请求批准实施** |
| 写入面 | 2 个文件（1 改 1 增），**清单外零改动** |
| 回归 | §5 六步（pin／baseline／after／逐件对照／测试／退出码）＋ §7 index mode+blob 核对 |
| 回退 | §8（引用 C4 §16.2 V1/V2） |
| **当前状态** | **待批 · 未实施**（本件不含任何代码改动） |

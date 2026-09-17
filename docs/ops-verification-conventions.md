# 核对惯例（ops verification conventions）

- 建立：2026-09-17｜来源：批一（写锁死亡自愈）T7 验收期间的口径讨论与实测教训
- 适用范围：跨仓/跨形态指纹比对、残留判定、测试隔离、推送后同步门登记
- 性质：**惯例（v1）**，新增条目需注明来源事件与证据

---

## C1 跨仓/跨形态指纹比较：先做 EOL 归一化，再比内容

**规则**：比较两个位置（主仓工作区 / git blob / 副本仓 / 归档包）中"同一文件"时，
**必须先归一化行尾（CRLF↔LF）再算哈希**，否则会得到假 DIFF。

**来源事件（2026-09-17）**：批一验收期间，主仓工作区 `writers.py`/`mcp_adapter.py` 为 CRLF，
git blob 与 QuantStudio-trading 副本为 LF → 直接比原始字节哈希得 DIFF（假）；EOL 归一化后
两侧完全一致（`e4dd14c6…`/`1cfb9742…`，且等于主仓 HEAD blob）。

**操作**：
```python
norm = raw.replace(b"\r\n", b"\n")
sha256(norm)   # 用于跨仓/跨形态比较
```
**注意**：若某个指纹是"原始字节哈希"（含 CRLF），它**不能**直接与 LF 侧比对；
指纹登记时必须写明口径（raw bytes / normalized）。

## C2 `diff_sha256` 是口径依赖量，盘面真值 = 逐文件 SHA-256

**规则**：`git diff` 输出哈希取决于 ① pathspec ② **HEAD 内容** ③ 文本归一化/换行形态，
故**跨 HEAD 或跨工具链不可比**，只能作"同一 HEAD 同口径"的复核锚；
验收对象的**盘面真值**一律用逐文件 SHA-256（并按 C1 注明是否归一化）。

**加固做法**：另记一个 **HEAD 无关规范指纹**，形如
`SHA256(逐行 "<相对路径>\0<文件SHA>\n" 拼接)`（批一实例：`artifacts_sha256=4f247302…`）。

**来源事件**：批一 T7（总调度复核项）。

## C3 "无残留" 判定：前/后基线逐条目 diff，而非"当前不存在"断言

**规则**：验收/测试**不得**断言"生产目录里没有 X"——生产资源是活的（如 `.write_lock`
在 daemon 正常写入期间会瞬时出现/消失）。正确做法：跑测**前**与**后**各取一次目录基线
（name/kind/size/mtime + 小文件 SHA），**逐条目 diff**——从而区分"清干净了"与"本来就没有"，
并把差异逐条归因（属测试残留 / 属生产自身活动）。

**工具**：`agent_workspace/snapshot_dir_baseline.py <out.json>`。

**来源事件**：批一 T7-2（总调度裁定①）；一次真实假红：新增用例断言"生产目录无锁文件"，
恰逢生产 daemon 写入瞬间 → 假红。

## C4 测试必须与生产资源物理隔离（三件套）

**规则**：涉及共享锁/共享目录的测试，必须同时满足：
① **锁目录重定向**（`QS_WRITE_LOCK_DIR`→ 会话临时目录，`tests/conftest.py` 会话级兜底）；
② **审计/日志重定向**（如 `QS_WRITE_LOCK_AUDIT_LOG`）；
③ **子进程环境在调用时构造**（模块级常量会冻结 import 期 `os.environ`，导致重定向传不进子进程）。

**来源事件**：批一 S8；一次真实污染：子进程环境被固化为模块级常量 → 8 个子进程把 1 条
回收审计写进了真实 `data/snapshots/`。

## C5 推送后同步门与豁免登记

**规则**：主仓双推后，同一工作周期内对 QuantStudio-trading 执行同步门
（`fetch && merge origin/main` + check-drift/ci-smoke）；
**仅当本次推送未触及共享层文件**（`quantstudio/`、`config/`、`skills/`、`scripts/`、`tests/`、`main_gui.py`）
时可豁免，但**必须在台账登记豁免依据**（推送 SHA + 改动文件清单 + 判定）。

**本仓登记**：
| 日期 | 推送 SHA | 改动文件面 | 判定 | 依据 |
|---|---|---|---|---|
| 2026-09-17 | `7192017` | 仅 `docs/customer-user-guide.md`（+10/−1） | **豁免同步门** | 未触及任何共享层文件；纯客户文档修正 |
| 2026-09-17 | docs-only 小提交（本 ops 规范 + 客户通知草稿，2 文件） | 仅 `docs/ops-verification-conventions.md`、`docs/handoff/customer-notice-lock-selfheal-20260917.md` | **豁免同步门** | 同上：未触及 `quantstudio/`、`config/`、`skills/`、`scripts/`、`tests/`、`main_gui.py` |
| 2026-09-17 | `6b8fde1` | 共享层（`quantstudio/pipeline/*`、`tests/*`）+ 文档 | **需同步门** | trading 线已执行并登记：`bf3e787` merge → `853817f docs(sync)`（全绿含三新测试） |

## C6 并发会话下的提交纪律（补充）

- 一律 `git commit -m "..." -- <精确路径>`（路径限定），**禁** `git add -A`；
- **若目标文件尚未被 git 跟踪（`??` 状态）**：`git commit -- <路径>` 会报
  `pathspec ... did not match any file(s) known to git` → 须先 `git add <精确路径>` 完成跟踪，
  再做路径限定提交（2026-09-18 实例：`fc97edc` 两个新文档提交）；
- 提交后核对 `git show --stat HEAD` **恰含预期文件**；
- 推送后核对**两远程 READ（`git ls-remote`）与本地 HEAD 逐位一致**；
- 若提交前发现 HEAD 已被其他会话推进：不回退、不覆盖，只提交自己的路径（本仓 2026-09-17 实例：
  HEAD 由 `f3b405a` → `65ec433` → `6b8fde1` 期间，本会话两次路径限定提交均安全落地）。

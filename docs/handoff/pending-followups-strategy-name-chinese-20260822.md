# 待办登记：中文命名任务闭环后的两条跟进项（2026-08-22）

> 用户验收闭环时指示：仅本地登记，均不单独推送。

## 1. AGENTS.md「写前快照」铁律执行细则补充（待下次文档同步批次顺带）

用户原话建议：把「**白名单文件本身也可能被他人污染 → 必须做 hunk 级剥离**」这条经验补进 AGENTS.md 写前快照铁律的执行细则。

本次实战沉淀的可复用要点（供届时撰写细则引用）：

- **风险场景**：多会话共享工作区中，即便 staging 采用了显式文件白名单，白名单内的文件（如 README.md、SKILL.md）也可能已被其他会话写入大量未提交改动（本次实例：README 1215 行、implementation-status.md 1547 行）。整文件 `git add` 表面合规、实则混入他人工作。
- **剥离方法（本次验证有效）**：对每个混合文件，以 `HEAD blob 字节 + 仅本会话的精确文本替换` 构造 index blob（`git hash-object -w --no-filters` + `git update-index --cacheinfo`），工作区他人改动原样保留。
- **三层污染拦截点**：
  1. 整 hunk 混入（他人语义改动）——替换锚点必须精确到本会话编辑的原文，断言唯一命中；
  2. **EOL 幽灵**——`git hash-object` 默认按 autocrlf clean filter 处理，会把 HEAD 中散落的 CRLF 行静默转为 LF（内容零变化、字节变动）；必须 `--no-filters` 裸哈希；
  3. 终检全量扫描——提交前 dump 全部 ± 行，按他人改动特征标记扫描，外来命中必须为 0。
- **配套**：推送前 `git stash create -u`（零副作用回退点）登记 hash 入 handoff 文档，随提交入库。

## 2. 三层止损 v2 发布时的首次中文命名实战确认点

正在走 R3-R6 的三层止损 v2 将是中文命名契约上线后的**首次实战发布**。R5 证据审核时顺带确认中文身份链端到端无阻：

- 候选文件 `quantstudio/backtest/strategies/<中文strategy_name>__candidate_quantstudio.py` 被 PyQt 正常列出、运行；
- `config.csv` 的 `strategy_file` 为中文 stem，`review_user_backtest_evidence.py` 身份匹配（`{strategy_id, strategy_name, candidate_stem}`）PASS；
- 正式发布 `<中文strategy_name>.py`、候选删除、R6 报告记录完整。

（契约测试 `tests/test_strategy_name_chinese_contract.py` 已覆盖以上链路的模拟路径；v2 为真实路径首验。）

## 3. AGENTS.md 细则「分档定案」补充登记（2026-08-22 R0 推送复盘，用户确认）

> 用户指示：分档执行、避免重武器常态化；除非另有指示，**等他轨道 AGENTS.md 未提交改动（当前 75 行）提交后**，单独走 方案→审计→实施→验收→确认→双仓库推送，现在不动。

针对 §1 的方法论定案（以 `996bbac` R0 工作流图推送为实战样本，规则 25 命中数=0 + 普通/-w diff stat 逐字节一致 + README 129 行 CRLF 字节保留，连续三次推送零污染）：

- **必做档（每次混合文件推送）**：
  1. 验证三件套：`git diff --cached --stat`（普通）与 `-w` 版 **stat 逐字节一致**（零 whitespace-only 差异）；目标行命中数=0 断言（如"规则 25 不卷入"）；提交后 `git show HEAD:<path>` 复查关键行；
  2. 工作区终检：提交后 `git status --porcelain` 只剩他轨道 ` M` 项，本次文件全部干净；
  3. 双回退点：改动前 + 推送前两次 `git stash create -u`，hash 登记入 evidence。
- **CRLF 敏感档（仅当 HEAD 散落孤 `\r`/CRLF 行且要求零字节扰动时启用，重武器不常态化）**：loose-object 直写保真法——`blob <len>\0<data>` + sha1 + zlib 直接写 `.git/objects`，绕过 `git hash-object` 的 autocrlf clean 剥离；写前用 python 逐字节确认非改动区域与 HEAD blob 一致。
- 备注：`git hash-object`（含 `--literally`）与 edit 工具均可能规范化行尾，这两条路径在 CRLF 敏感场景不可依赖，仅可作快速路径。

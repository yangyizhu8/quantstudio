# 交付线 · 出仓前检查清单（例行机制）

> 依据：节点⑳ 2026-11-04 事故闭环（客户「7/73」误判 + 交付完整性第三洞）+ DB-2026-0026 采纳裁定。
> 适用范围：**交付线（specs/003）/ 框架层改动**每次出仓（推远端前）逐条过。
> 纪律：每条须有**实测读数**，缺读数即视为未过；任一条不过 ⇒ 不推。

## ① 纯增益基线相对审计（SC-013）

```bash
python scripts/delivery_pure_gain_audit.py --mode audit --baseline docs/evidence/delivery-pure-gain-baseline-v4-20261104.json
```

- 期望：`结论：PASS`、`保护路径总数：N；有变化：0`、`新增未跟踪（受保护目录）：无`；
- 若本轮**经批准**改了受保护路径 ⇒ 先 `--mode baseline` 重建基线（note 里写清追认清单），再复审。

## ② 资产完整性扫描（DB-2026-0026，新增）

```bash
python scripts/delivery_asset_completeness_scan.py            # 交付域
python scripts/delivery_asset_completeness_scan.py --scope all # 全域（慢，例行可只跑交付域）
```

- 期望：`✅ 无缺口：已入库文件未引用未入库资产`（退出码 0）；
- 有缺口 ⇒ 二选一：①把资产**精确入库**（禁 `git add -A`）；②登记进
  `scripts/delivery_local_only_assets.txt` 白名单并写明理由（声明式"本地不推送"）；
- 背景：2026-11-04 实测——`scripts/delivery_wave_gate.py` 从未入库，副本/新克隆跑共享层回归**必挂 3 例**。

## ③ 测试与回归（隔离两变量，零触碰生产库）

```bash
# 隔离变量（必须同时设两项：数据根 + 锁目录）
$env:QUANTSTUDIO_DATA_ROOT=<临时>/data
$env:QS_WRITE_LOCK_DIR=<临时>/lock
python -m pytest tests/test_delivery_*.py tests/test_qfq_*.py -q
```

- 期望：全绿；退出码须为 **0**（若用例全 PASS 但进程退出码非 0，属 teardown 崩溃，须归因后再出仓）。

## ④ 知识沉淀与反哺（铁律 22）

- **R1 开工留痕**：方案文档写明命中条目 / 无命中检索面；
- **R4 验收沉淀**：`python scripts/kb_draft.py --kb knowledge/db --domain D4 --layer artifact_rule …`
  → 必须过 `kb_hook.validate_record`（族 db）；条目的 `fix_target` 要能回溯到文件/函数；
- **回哺落点**：可机检（契约表 / 测试固件 / 扫描脚本），不接受散文式条目。

## ④b 异常传递形态检查（2026-11-04 新增 · DB-2026-0027）

**规约：结构化异常不得降级成字符串。**

- 背景（实测缺陷）：`gui/workers.py` 异常块只对 `BacktestCancelled` 原样传递异常对象，
  其余一律 `emit(f"{type(e).__name__}: {e}\n{traceback}")` **降级成字符串**
  ⇒ 接收侧 `isinstance(err, CollectorStopTimeout)` 恒 False ⇒ **已实现的三选项对话框永不触发**，
  用户只看到裸 traceback「回测错误」；CLI 与 GUI 口径同时分裂。
- 检查动作（每轮改动涉及"worker 异常块 / 信号发射 / 槽内 isinstance 分支"时必查）：
  1. 列出本轮新增/改动的**可恢复类异常**（超时、取消、部分失败、需用户裁决…）；
  2. 逐个确认其在**跨层传递**时是否仍是**异常对象**（`emit(exc)`），而非 `f"{type(e).__name__}…"` 字符串；
  3. 接收侧所有 `isinstance(...)` 分支，对每类结构化异常**至少一条用例**能命中
     （用例断言"走的是专用分支"，不是"抛得出"）；
  4. 若确实需要字符串化给人看，则**异常对象与文本同时携带**（如异常对象上挂 `_gui_traceback`），
     文本只作附加信息，不作唯一载体。
- 判据：**"能抛出"不等于"能被识别"**——本条只认"接收侧分支被用例命中"。

## ⑤ 会话归属映射登记（SC-013 归属口径）

- 本轮新增/修改的文件路径必须登记进 `docs/evidence/session-attribution.json` 的对应会话域
  （前缀形态：`quantstudio/pipeline/xxx.py`、`tests/test_xxx.py`、`docs/evidence/…`）；
- 否则审计会把本线改动判成 `OTHER_LINE`，掩盖真实归属。

## ⑥ 文档与实现口径一致（防"下发材料漂移"）

- 客户材料 / 说明书里的**命令名、参数名、默认值、备份档位、退出码语义**必须与代码逐项核对；
- 核对方式：`--help` 实跑 + 一次端到端实跑（不是只看文档）；
- 背景：2026-11-04 实测 4 处漂移（命令名 / 不存在的 `--plan` 参数 / 备份档位描述 / 漏写必填参数）。

## ⑦ 推送纪律（C7 全量显式声明）

- **锚（`-Commit`）只覆盖它自己**，其余范围内**每一笔**都要进 `QS_PUSH_BUNDLE_ALLOWALSO`；
- 范围取 `git log --format=%h origin/main..HEAD`，排除锚之后的**全部**列出；
- 推送后核对：local = plus = qs = eco，未推送笔数 = 0。

## ⑧ 同步门（铁律 14）

```bash
cd <副本> && git fetch origin && git merge --no-edit origin/main
git diff --name-only origin/main HEAD -- quantstudio/ config/ skills/ scripts/ tests/ main_gui.py
```

- **两点比对**（merge 后，期望为空；三点比对在 merge 后会退化漏检）；
- 非空时逐个归因：声明式"本地不推送"资产（如 archify 线的 `scripts/archify-map.ps1`）不算漂移，但须在回执里点名；
- 副本共享层回归段须全绿；**副本推送归副本线**，我方不代推。

## ⑨ 交付完整性补件规则（治本）

- 交付线的**必备件**（被已入库测试/脚本引用的资产）必须在**当轮入库**，
  不得沿用"本地提交·不推送"的惯例——该惯例只适用于个人临时资产；
- 入库前用 ② 扫描自证。

---

**本清单来源**：节点⑳ 客户事故闭环（2026-11-02 → 11-04）。每次出仓逐条过，缺读数即未过。

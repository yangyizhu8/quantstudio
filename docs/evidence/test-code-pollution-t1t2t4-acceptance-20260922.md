# 错误二实施阶段验收（T1/T2/T4）· 2026-09-22

- 方案：`docs/qfq-test-code-pollution-fix-design.md`（六步①②已过审）
- 回退锚点：HEAD `17c6369`（快照基线）；写前快照 `git stash store`：
  `4afd5930d1019b9a273644553d70b32d618a7cd5`（msg=baseline-err2-impl-20260922_111757）
- **状态：实施完成、验收全绿、未提交未推送（六步⑤待呈）**

## 一、落地清单（相对过审案的**现状修正**在 §三）

| 项 | 文件 | 内容 | 证据 |
|---|---|---|---|
| **T1** | `quantstudio/pipeline/code_contract.py`（新） | 形式契约：`[0-9]{6}` 或 `+.(SH\|SZ\|BJ)`，归一裸码；**无关键词**（过审裁定）。开发中自纠：`\d` 匹配全角「０」→ 显式 ASCII 类 | `tests/test_code_contract.py` **8 passed**（拒绝向量=8 处实测脏值；通过向量=沪深/创业/科创/北交所/ETF/指数全谱） |
| **T2** | `sources/mcp_adapter.py` `_get_adj_latest_global` | `want` 集建前契约预过滤：`TEST999.SH` 形态源头剔除 + WARNING，**消灭误触全历史冷启动路径**（报告 §2.1 机制） | `tests/test_mcp_etf_latest_anchor.py` 新增 2 例：混合输入合法码逐位不变 + `calls==[]`（修复前必红——TEST999 进 want→still 非空→冷启动）；全脏输入→`{}` 零触发 |
| **T4** | `qfq_event_discovery.py` `_observe_factors` | aux 快照行预过滤（9-22 01:48 `[qfq_orch] 周期异常 FIXTEST` 崩溃链的构建点）；`record_observations` **阻断2 整批契约不动**（fail-safe 上移调用方，`_normalize_code` 保持严格） | `tests/test_qfq_event_discovery.py::test_observe_snapshot_contract_prefilter_dirty_skipped`：混入 FIXTEST/TEST.SH **不抛**、合法行入观察、脏行不入账本（修复前此例必炸） |

**套件合计**：8 文件 **68 passed / 0 failed**（event_discovery、factor_new、fetch_routing、export_cache、streaming、latest_anchor、aux_route、code_contract）。

## 二、顺带清账（如实披露，非静默）

`tests/test_mcp_etf_latest_anchor.py` 存在 **HEAD 既有红 3 例**（`test_etf_restore_anchor_...`/
`test_nonmonotonic_...`/`test_factor_snapshot_sync_...`，`qfq_aux_override AttributeError`）。

- **定性证据**：`git worktree` 于 HEAD(`bb6029b`) 独立复跑 = **3 failed, 1 passed** ⇒
  红与本线改动无关，系 **`fed25dc`（TD-D2 路由，`_query_adj_latest` 改经 `_qfq_aux_path()`）后
  `__new__` harness 未同步属性**；d9ea208 B-5 批同型债修复有先例（该批修了 aux_route 套件 harness）。
- **本线处置**：harness 补一行 `adapter.qfq_aux_override = None`（=正常 `__init__` L349 同款语义，
  legacy 推导路径正合该 harness 的 tmp 同目录 aux 布局）→ 3 例转绿。**登记：若归属线认为处置
  不当可单独 revert 该一行，不影响 T1/T2/T4。**
- **教训**：ci-smoke 共享层子集未含此文件 ⇒ 既有红未入门禁视野——**已入本线自查台账**
  （「HEAD 既有红须 worktree 实证归因，不得默认『我改红的』也不得默认『与我无关』」）。

## 三、对过审方案的两处现状修正（实施期发现，如实回写）

1. **注入侧防线已存在**：`normalize_mcp_adj_factor_df` L2205 = **F-1 修复（d9ea208，2026-09-08，
   CASE-003）**——aux 增量注入路径已被非 6 位过滤；现库 `adj_factor.FIXTEST` 1 行系 **9-08 前
   历史存量**（非现行漏点）⇒ T2 实际缺口收敛为**冷启动决策侧**（已修）+ 存量清理（T5 读窗）。
2. **T4 落点上移**：过审案写 `_discover` per-code try/except；实施按崩溃链精确定位到
   `_observe_factors`（observations 构建点）做**预过滤**——语义优于 try/except（不吞
   `record_observations` 真冲突整批契约，只剥离本非法行）。属实现细节收敛，非范围变更。

## 四、orch 解锁状态（错误一 T4 的前置）

- **代码级：已解锁**（T4 预过滤在案，回归钉在套）。
- **实环境：待证**——「连续 ≥3 周期无崩溃 + 53,925 积压排水斜率」需主库写窗运行后取证；
  与 T5 存量清理（aux FIXTEST 1 行 + 主库 5+2 行，`cleanup_test_codes.py` 尚未创建）同排
  「计划空档窗」（与 dev 三测、错误一 T6 同窗，避免多次停机）。

## 五、待办与提交计划

- 待实施（错误一案内）：T1/T2/T3/T5（检查器 map 契约 + UNKNOWN 占比指标、阻断规则、
  告警 outbox、fallback fail-safe）——注意 `daemon.py` 现为他线在途 **M**（11:17 基线核查实录），
  实施时须精确文件清单 + 叠加事实入 commit message。
- 待创建：`scripts/cleanup_test_codes.py`（dry-run 默认，读窗执行）。
- 本批提交形态建议（错误二独立 commit）：
  `code_contract.py + mcp_adapter.py + qfq_event_discovery.py + 3 个测试文件 + 2 个 docs
  （方案+本证据）`——与错误一 commit 分开，各自六步⑤呈批。

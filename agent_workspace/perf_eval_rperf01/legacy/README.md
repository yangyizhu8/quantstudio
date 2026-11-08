# legacy/ —— 冻结证据归档（R-PERF-23 K-1~K-4）

冻结日期：2026-11-08 16:08
冻结原因：这些文件产生于 D-2 命名规范（四要素标签）生效之前，命名不合规，
但已进入 R-17~R-21 的 sha 登记表并被引用 ⇒ **不改名**（改名会破坏「文件名↔sha」引用），
整体移入本目录冻结。本目录由 `scripts/check-evidence-naming.py` 整目录排除（legacy-frozen）。

**规则：本目录只读。新增证据一律写上级目录并带四要素标签**
`<probe>_<version>_<thermal>_<layer>_<window>_<stamp>.{json,log}`。

## 路径变更 + sha 登记（sha 为内容哈希，移动前后已核对一致）

| 文件 | 原路径 | 现路径 | sha256(前16) | 状态 |
|---|---|---|---|---|
| A1_post_cold.log | `agent_workspace/perf_eval_rperf01/A1_post_cold.log` | `agent_workspace/perf_eval_rperf01/legacy/A1_post_cold.log` | 165dd3af3a3ac23a | OK |
| A2_post_hot.log | `agent_workspace/perf_eval_rperf01/A2_post_hot.log` | `agent_workspace/perf_eval_rperf01/legacy/A2_post_hot.log` | 35c81a106ff8a457 | OK |
| B1_pre_cold.log | `agent_workspace/perf_eval_rperf01/B1_pre_cold.log` | `agent_workspace/perf_eval_rperf01/legacy/B1_pre_cold.log` | 33ca06734f142167 | OK |
| B2_pre_hot.log | `agent_workspace/perf_eval_rperf01/B2_pre_hot.log` | `agent_workspace/perf_eval_rperf01/legacy/B2_pre_hot.log` | 61c0f709ccb64059 | OK |
| readings_A1_post_cold.json | `agent_workspace/perf_eval_rperf01/readings_A1_post_cold.json` | `agent_workspace/perf_eval_rperf01/legacy/readings_A1_post_cold.json` | ddf525a03b0d8b8b | OK |
| readings_A2_post_hot.json | `agent_workspace/perf_eval_rperf01/readings_A2_post_hot.json` | `agent_workspace/perf_eval_rperf01/legacy/readings_A2_post_hot.json` | 9c9895d6317a6424 | OK |
| readings_B1_pre_cold.json | `agent_workspace/perf_eval_rperf01/readings_B1_pre_cold.json` | `agent_workspace/perf_eval_rperf01/legacy/readings_B1_pre_cold.json` | 9ebe9226b138b54d | OK |
| readings_B2_pre_hot.json | `agent_workspace/perf_eval_rperf01/readings_B2_pre_hot.json` | `agent_workspace/perf_eval_rperf01/legacy/readings_B2_pre_hot.json` | fcb9838d528e43a7 | OK |
| baseline_2026-06-05_2026-06-17_golden.json | `agent_workspace/perf_eval_rperf01/baseline_2026-06-05_2026-06-17_golden.json` | `agent_workspace/perf_eval_rperf01/legacy/baseline_2026-06-05_2026-06-17_golden.json` | 56a24690048500a4 | OK |
| baseline_2026-06-05_2026-06-17_readings.json | `agent_workspace/perf_eval_rperf01/baseline_2026-06-05_2026-06-17_readings.json` | `agent_workspace/perf_eval_rperf01/legacy/baseline_2026-06-05_2026-06-17_readings.json` | 518d7e9dec5f341d | OK |
| baseline_2026-06-08_2026-06-19_golden.json | `agent_workspace/perf_eval_rperf01/baseline_2026-06-08_2026-06-19_golden.json` | `agent_workspace/perf_eval_rperf01/legacy/baseline_2026-06-08_2026-06-19_golden.json` | 8ad56e8be9c1d227 | OK |
| baseline_2026-06-08_2026-06-19_readings.json | `agent_workspace/perf_eval_rperf01/baseline_2026-06-08_2026-06-19_readings.json` | `agent_workspace/perf_eval_rperf01/legacy/baseline_2026-06-08_2026-06-19_readings.json` | 6fa423674e12ef07 | OK |
| c1b_pre_cold.log | `agent_workspace/perf_eval_rperf01/c1b_pre_cold.log` | `agent_workspace/perf_eval_rperf01/legacy/c1b_pre_cold.log` | 139c73ffd6be6ed8 | OK |
| contA1.log | `agent_workspace/perf_eval_rperf01/contA1.log` | `agent_workspace/perf_eval_rperf01/legacy/contA1.log` | 304ce720266967bd | OK |
| contA2.log | `agent_workspace/perf_eval_rperf01/contA2.log` | `agent_workspace/perf_eval_rperf01/legacy/contA2.log` | c8a3d65ed154eed1 | OK |
| fullwindow_pre_e04.log | `agent_workspace/perf_eval_rperf01/fullwindow_pre_e04.log` | `agent_workspace/perf_eval_rperf01/legacy/fullwindow_pre_e04.log` | 251798f4f2fc9355 | OK |
| gold156_daily.log | `agent_workspace/perf_eval_rperf01/gold156_daily.log` | `agent_workspace/perf_eval_rperf01/legacy/gold156_daily.log` | a359dfd352bdaec6 | OK |
| gold156_minute.log | `agent_workspace/perf_eval_rperf01/gold156_minute.log` | `agent_workspace/perf_eval_rperf01/legacy/gold156_minute.log` | b8e596fb9ff7d853 | OK |
| r8target.log | `agent_workspace/perf_eval_rperf01/r8target.log` | `agent_workspace/perf_eval_rperf01/legacy/r8target.log` | 7e03e267f745b556 | OK |
| r8target2.log | `agent_workspace/perf_eval_rperf01/r8target2.log` | `agent_workspace/perf_eval_rperf01/legacy/r8target2.log` | d4ba03b977098776 | OK |

## M-2 两次覆写记录（R-PERF-24 补记）

| 文件 | 首次覆写（1.4.5 原版灭失时点，以此为准） | 第二次覆写 | 说明 |
|---|---|---|---|
| aseline_readings.json | **2026-11-08 01:57**（1.5.6 跑） | **2026-11-08 03:29:21**（四跑矩阵 B2_pre_hot） | 冻结期内再次覆写 ⇒ 门禁报 KNOWN_VIOLATION_MUTATED |
| aseline_golden.json | 2026-11-08 01:57 | 2026-11-08 03:29:21 | 同上 |
| daily_baseline_readings.json | 2026-11-08 01:50:56 | 未再变 | 仍处冻结态（known，非 mutated） |

**定性**：冻结应冻结「文件的原始状态」，不冻结「对它的监测义务」。首版实现按 mtime <= cutoff 判 known，
导致 cutoff 前覆写 1 次与 100 次输出相同（D-2 违规在冻结期内第二次发生而门禁无声）。
**修正（M-1）**：KNOWN_VIOLATIONS 每项登记 rozen_mtime（= 首次覆写时刻），实跑比对当前 mtime，
不一致即报 KNOWN_VIOLATION_MUTATED（WARN / strict FAIL）。登记表只列实际存在的文件，
不存在者移入 KNOWN_VIOLATIONS_MISSING 单列。

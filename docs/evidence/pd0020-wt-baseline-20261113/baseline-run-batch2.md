# 基线复现第二批（安全组 7 文件）

> 基线 worktree `7826aed`（detached，未提交差异 0）
> 命令：`python -m pytest <file> -q`（逐文件，cwd = worktree 根）
> **未裸跑全库**；本轮只跑用户指定的 7 个安全组文件。

## 一、逐文件结果

| 文件 | 基线结果 | 当前 135 清单 | 判定 |
|---|---|---|---|
| `test_fund_contract_wiring_matrix.py` | **7 failed / 2 passed** | 7 | **基线既有 7** |
| `test_phase4_minute_batch_provider.py` | **6 failed / 6 passed** | 6 | **基线既有 6** |
| `test_phase4_minute_memory_slice.py` | **6 failed / 5 passed** | 6 | **基线既有 6** |
| `test_strategy_fidelity_gates.py` | **4 failed / 2 passed** | 4 | **基线既有 4** |
| `test_batch_apis.py` | **5 passed / 8 skipped** | 4 | **候选新增 4**（基线无 failed） |
| `test_d3_firstday_noprice.py` | **2 passed / 4 skipped** | 4 | **候选（但基线为 skipped，非 passed）** |
| `test_pd14b_time_normalize.py` | **4 passed / 4 skipped** | 4 | **候选（但基线为 skipped，非 passed）** |

## 二、关键限制（必须标注）

**`test_d3_firstday_noprice` 与 `test_pd14b_time_normalize` 的基线结果是
`4 skipped` 而非 `4 passed`** ⇒ 当前 4 项 failed 与基线 4 项 skipped
**可能不是同一批 nodeid**。

按用户裁定「缺 nodeid 不能简单判好坏」：

- 这两组**不得**判为「本批新增」——须先做 **nodeid 精确比对**：
  基线 skipped 的具体用例名 vs 当前 failed 的具体用例名；
- 若 nodeid 不一致 ⇒ 属「**基线缺 nodeid**」类，单列，**不计 PASS、也不判新增**；
- 若 nodeid 一致（同一用例基线 skip、当前 fail）⇒ 可能是**跳过条件变化**
  （如依赖缺失/环境差异），亦须单列核查，不能直接归本批。

## 三、累计分类（截至第二批）

| 类别 | 数量 | 明细 |
|---|---|---|
| **已证基线既有** | **30** | source_import 7 ＋ fund_contract 7 ＋ phase4_batch 6 ＋ phase4_memory 6 ＋ fidelity_gates 4 |
| **已证本批新增（根因已定位）** | **2** | test_08、test_08b（A-1 `is_trade` DENY→KEEP） |
| **候选新增（待 nodeid 精确比对）** | **4** | test_batch_apis（基线无 failed，可较直接判候选） |
| **基线缺 nodeid / 需精确比对** | **8** | test_d3_firstday_noprice 4、test_pd14b_time_normalize 4（基线 skipped） |
| **仍未运行** | **91** | — |
| **合计** | **135** | 30 + 2 + 4 + 8 + 91 = 135 ✓ |

## 四、边界声明（严格按用户裁定）

1. **30 项「基线既有」不等于根因相同**，也**不免除**当前修复义务；
   基线同失败只对 `7826aed` 该版本、该配置、该输入成立。
2. 第二批各文件的 `passed`（2/6/5/2/5/2/4）**均不属 135 清单内** ⇒
   **不等于 135 项里解决了任何项**。
3. `test_batch_apis` 基线 `5 passed / 8 skipped`、当前 4 failed：
   基线无 failed ⇒ 候选新增较直接，但仍需 nodeid 比对确认是同一批用例。
4. 本轮**未修改任何测试或业务代码**。

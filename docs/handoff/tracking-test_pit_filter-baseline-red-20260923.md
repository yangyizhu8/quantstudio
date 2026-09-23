# 追单：`test_pit_filter::test_validator_is_single_chokepoint` 既有红（低优先）

- 登记：2026-09-23（客户运维会话）｜优先级：**低**（入客户运维线 backlog）｜归属：客户运维线
- 定性：**基线即红、与本批（GUI 启动卡死案 / duckdb 混版案）无关**——审核方已采信，本单为**归因追单**（非修复派单）
- 来源裁定：ZCode 2026-09-23「test_pit_filter 既有红——另起追单归因（低优先入你线 backlog：单一入口期望 vs 4 处 writer.write 的契约漂移）」

## 1. 复现（实测）

```
python -m pytest tests/test_pit_filter.py -q
→ 1 failed, 15 passed
```

失败断言（`tests/test_pit_filter.py:170`）：
```python
write_lines = [i for i, line in enumerate(lines, 1)
               if 'writer.write' in line and 'def ' not in line]
assert len(write_lines) == 1, f"writer.write 应仅存在于统一入口，实际 {len(write_lines)}"
```
**实测**：`writer.write` 在 `quantstudio/pipeline/daemon.py` 出现 **4 处**，行号 **1334 / 1336 / 2993 / 3021**
（同用例前两条断言通过：`validator.validate` ≥4 ✓、`self._stamp_and_write(` ≥4 ✓）。

## 2. 与本批无关的证据

本批对 `daemon.py` 的改动**仅两处**、且都不在上述行号附近：
① `main()` 首行加版本闸（笔5）；② `ResidentCollector.close()` 末尾加安全检查点（笔3）。
上述 4 处 `writer.write` 均为**既有代码**（未在本批触碰），故属基线红。

## 3. 待归因的两个方向（禁止未证实归因，需先取证）

| 方向 | 含义 | 取证动作 |
|---|---|---|
| A. **契约漂移** | 「所有写入汇聚唯一 chokepoint」的设计约束被后续 4 处直调破坏（设计意图 vs 实现的漂移） | 查 4 处的引入提交（`git log -L`）与当时是否有意放宽；核 `_stamp_and_write` 与直调 `writer.write` 的语义差（是否绕过 stamp/校验/审计） |
| B. **期望陈旧** | 用例写于「单入口」时代，之后架构合法演进为 4 个受控入口（断言未同步） | 查用例引入提交与最近一次架构变更记录；核对 4 处是否都在受控路径内（是否均经 validator 前置） |

## 4. 关单标准（本单）

- 归因结论**证据确凿**（A 或 B，含引入提交与语义差/受控性核对）；
- 若判 A：转「框架层改动六步流水线」（方案→审计→实施→验收→确认→双推），**不得**直接改断言；
- 若判 B：同步更新用例断言（并说明架构演进的合法依据），回归全绿；
- 无论 A/B，均在 `docs/case*` 或本单补记结论与证据指针。

## 5. 关联

- 本批验收记录：`docs/evidence/gui-nonblocking-implementation-20260923.md` §4 待办 4
- 同域 tech-debt（会话 2 已登记）：云端 `etf_minutes` close 口径与还原链自洽性疑问
  （若归因中发现同域线索，一并核）

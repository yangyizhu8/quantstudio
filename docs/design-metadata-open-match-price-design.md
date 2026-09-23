# F3-A 方案：design_metadata 接受 `match_price_mode='open'`（daily-bar-v1）

> 状态：**方案稿（待审）** — 六步流水线第 1 步
> 提出：2026-09-23；发现场景：ou_reversal_csi300_10 的 PTrade 转换（客户目标 = 验证转换管线双端对齐）
> 关联铁律：框架层改动六步流水线；框架问题立即解决（禁止登记挂账）

## 1. 问题定义（已实测复现）

**现象**：转换 `quantstudio/backtest/strategies/沪深300均值回归超跌反弹.py` 时，`source_import_report.json` 的
`design_metadata_resolution` 返回：

```json
{
  "status": "INVALID_PROFILE",
  "design_path": "agent_workspace/ou_reversal_csi300_10/agent_strategy_design.json",
  "ledger_path": "agent_workspace/ou_reversal_csi300_10/workspace_state.json",
  "strategy_id": null,
  "engine_profile": null,
  "source_sha256": "9cfb26e568d8be11...（与本策略一致，可信链已命中）",
  "reason": "invalid/conflicting engine_profile: 'daily-bar-v1'"
}
```

**根因（行级定位）**：`quantstudio/strategy_compiler/design_metadata.py:115-126`

```python
if profile_id == "daily-bar-v1":
    return bf == "1d" and mp == "close"      # ← 硬编码 close
```

该判断**早于 E1-2**（2026-09-22 定稿：`close`/`open` 均合法；策略语义为「开盘执行」时必须显式声明 `match_price_mode='open'`）。
本策略正是 E1 合规的 `daily-bar-v1 + open` 设计 → 被误判为 profile 组合矛盾。

**影响面**：
- CLI：`qs-compile import` 的 `--engine-profile` 缺失时「fail-closed BLOCK」（cli.py:165-167）→ 显式传参可绕过，但**设计元数据可信链失效**（门禁输入退化为人工传参）；
- **GUI（PyQt 转 PTrade tab）**：`ptrade_export_tab.py:269-278` 依赖 `find_design_for_strategy` 自动带出 profile；`INVALID_PROFILE` → `profile` 保持 `None` → 门禁 BLOCK（除用户手工选择引擎周期外无法转换）；
- 所有 E1 合规的 `open` 撮合策略（未来会越来越多）在 GUI 上均无法自动转换。

**非回归确认**：`render.py:230-232` 已把 `open` 视为合法模式（`open`/`next_open` 走 09:31 调度）；
schema `engine_profile.match_price_mode` 枚举本就含 `open`。故本改动是**使校验器与既有语义对齐**，非新能力。

## 2. 改动范围（最小化）

| 项 | 内容 |
| --- | --- |
| 改动文件 | `quantstudio/strategy_compiler/design_metadata.py`（单文件单行） |
| 改动点 | `_profile_combo_valid` 第 121 行 |
| 改动内容 | `return bf == "1d" and mp == "close"` → `return bf == "1d" and mp in ("close", "open")` |
| 明确不改 | `minute-bar-v1` / `daily-open-close-proxy-v1` 分支；`next_open` 维持不可用（已废弃）；schema；转换器；引擎 |
| 附带 | 新增测试：`(daily-bar-v1, 1d, open)` → RESOLVED；`(daily-bar-v1, 1d, next_open)` → INVALID_PROFILE（保持废弃语义） |

**为什么不含 `next_open`**：skill 自 2026-08-13 起强制 close 口径（`next_open` 把 T+1 数据引入 T 日时间片）；
E1-2 只合法化 `open`。保留 `next_open` → INVALID_PROFILE 可继续 fail-closed 拦截 legacy 设计。

## 3. 影响面与风险

- 正向：E1 合规的 `open` 策略在 CLI/GUI 双路径均可自动解析 profile → `get_index_day_bar` 重写门禁的权威输入恢复；
- 风险：若某 `open` 设计实际语义与「开盘执行」不符，profile 会被放行——但 `match_price_mode` 是设计契约的显式声明，
  且 R4 校验器（`PROFILE-SCHEDULE-MISMATCH`、`NO-LOOKAHEAD-*`）与 R5 引擎语义版本核对仍是独立闸门；
- 兼容：仅放宽一个此前被误拒的组合，不改变任何已 RESOLVED 的设计。

## 4. 验收标准

| # | 判据 |
| --- | --- |
| V1 | `find_design_for_strategy` 对本策略返回 `RESOLVED` + `engine_profile='daily-bar-v1'`（`source_import_report.json` 的 `design_metadata_resolution.status` 由 INVALID_PROFILE → RESOLVED） |
| V2 | `qs-compile import` 在**不传** `--engine-profile` 时对本策略仍能通过 `get_index_day_bar` 门禁（不再 BLOCK） |
| V3 | 既有 `tests/test_design_metadata.py` 全绿；新增两条用例（open → RESOLVED / next_open → INVALID_PROFILE） |
| V4 | 6 策略重转 + 相关测试套件零衰减；矩阵 reverify 无需（不触 `_QS_*` wrapper 模板） |

## 5. 回退条件

- V3/V4 出现失败 → 单行回退；
- 若审核方认为 `open` 不应进入 design_metadata 可信链 → 回退并改为在转换器侧增加显式 `--engine-profile` 提示（GUI 弹窗），但该路径体验更差。

## 6. 六步排程

1. **方案**（本文）→ 2. **审计** → 3. **实施**（单行 + 2 用例）→ 4. **验收**（V1-V4 证据落 `docs/evidence/`）→ 5. **用户确认** → 6. **双仓库推送**。

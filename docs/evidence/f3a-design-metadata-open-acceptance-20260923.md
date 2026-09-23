# F3-A 验收证据：design_metadata 接受 `match_price_mode='open'`（daily-bar-v1）

> 六步流水线第 4 步（验收）｜执行：2026-09-23｜方案：`docs/design-metadata-open-match-price-design.md`
> 改动：`quantstudio/strategy_compiler/design_metadata.py` `_profile_combo_valid`（单处）

## 1. 根因（行级）

```python
# 改前（第 121 行）
if profile_id == "daily-bar-v1":
    return bf == "1d" and mp == "close"      # 硬编码 close，早于 E1-2（2026-09-22）
```

E1-2 定稿：`close`/`open` 均合法；策略语义为「开盘执行」时必须显式声明 `match_price_mode='open'`。
该硬编码使 E1 合规的 open 设计被误判为 `INVALID_PROFILE` → design 元数据可信链失效 →
GUI 转 PTrade tab 自动解析 profile 失败（`ptrade_export_tab.py:269-278`）→ `get_index_day_bar` 重写门禁 BLOCK。

## 2. 改动

```python
if profile_id == "daily-bar-v1":
    return bf == "1d" and mp in ("close", "open")   # next_open 仍拒（2026-08-13 废弃）
```

## 3. 验收判据与结果

| # | 判据 | 结果 | 证据 |
| --- | --- | --- | --- |
| V1 | 本策略 design 解析 `INVALID_PROFILE` → `RESOLVED` | **PASS** | `source_import_report.json`：`status=RESOLVED` / `engine_profile=daily-bar-v1` / `strategy_id=ou_reversal_csi300_10` |
| V2 | 不传 `--engine-profile` 仍通过 `get_index_day_bar` 门禁 | **PASS** | `qs-compile import <策略> --no-smoke`（无 profile 旗标）→ `source_import=PASS`，`SHIM=[get_index_day_bar]`，`BLOCK=[]` |
| V3 | 既有 `tests/test_design_metadata.py` 全绿 + 新增 2 用例 | **PASS** | `12 passed`（含 `test_profile_combo_accepts_open_match_price` / `test_profile_combo_still_rejects_next_open`） |
| V4 | 全框架同类硬编码复扫归零 | **PASS** | 扫描 `match_price_mode == "close"` / `mp == "close"` 全仓 → 仅命中本次改动注释；其余 close 出现均为合法默认值 |
| V5 | 矩阵 reverify | **无需** | 未触 `_QS_*` wrapper 模板串 |

## 4. 影响面与回退

- 放宽的仅是一个此前被误拒的组合，不改变任何已 `RESOLVED` 的设计；`next_open` 维持拒绝（继续 fail-closed 拦截 legacy 设计）。
- 回退：单处 `git revert`；回退后本策略转换需显式传 `--engine-profile`，GUI 路径恢复 BLOCK。

## 5. 教训登记（案例库）

> **原则级变更须触发全框架硬编码假设扫描**：E1-2（2026-09-22）把 `open` 提升为合法成交价模式，
> 但框架内 `design_metadata._profile_combo_valid` 仍以 `mp == "close"` 表达「daily 只能收盘撮合」这一
> 已被推翻的假设。原则层放宽后，**必须全仓扫描以旧假设写死的判断**，否则新旧语义在框架内并存，
> 表现为「按新原则写的策略在某个下游环节被静默拒绝」。本案的发现路径是端到端转换实测——
> 单测全绿（旧假设无对应用例）也未能暴露。

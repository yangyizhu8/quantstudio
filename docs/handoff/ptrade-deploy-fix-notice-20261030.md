# 修复完成通知 · PTrade 回测转模拟盘/实盘（qs-deploy）

> 定稿日：2026-10-30 ｜ 依据：PD-0012（`160a9b2`）/ PD-0013（`855250a`），已随 #11 出仓，
> 双远程逐位一致 `e941944faad59842336b8953eebd6ba5d79fb245`。
> 读者＝用户本人（自报自验场景，非客户分发件）；②审两裁已定：保留产物路径、保留 ETF 注。

## 一、问题

策略「**全球资产轮动**」转换时被 P5 门禁拦下：

```
行 942: set_limit_mode 交易环境不可用且未门控（契约表 trading:false）
```

转换停在该段、不产出部署代码。另有每次转换都出现的提示：

```
P5 未知 API（需人工确认）: 行 1203: get_order（契约表外，需人工确认）
```

## 二、修复

| 案 | 修复内容 | 出仓 |
|---|---|---|
| **PD-0012** | 源产物里 `if not False:`（由 `is_trade()` 固化而来）包裹的**回测专用调用**，现由转换器还原为运行时门控 `if not is_trade():` —— 回测侧照旧执行、交易侧正确跳过；转换审计行留痕「恒真分支还原@行号」 | `160a9b2` |
| **PD-0013** | 契约表补 `get_order` / `get_open_orders`（双端可用、不门控），消除该提示 | `855250a` |

两笔均已推送双仓库（`e941944`，两端一致）。

## 三、重验（一条命令）

```powershell
cd "D:\miniQMT策略实盘\QuantStudio"
qs-compile deploy "output/.qs-deploy-quick/strat_0c202b.yaml"
```

> `strat_0c202b` 即「全球资产轮动」的 `strategy_id`（中文名派生：ASCII 段为空 → `strat_` + 名称 sha1 前 6 位）。

## 四、预期变化

- **七个阶段全部 PASS**：P0 预检 / P1 档位 / P2 配置 / P3 变换 / **P4 等价** / **P5 门禁** / P6 交付；
- 产物生成到 `Demo_account_Real_account\全球资产轮动\`：`strat_0c202b_trade.py` + `report.md` + `runbook.md`；
- 「`get_order` 契约表外」提示**消失**；仅剩 3 条正常提示（回哺来源 / g 豁免 / PBOX 版本注记）；
- **回测行为不变**（P4 等价段 PASS 为证）。

> **注**：另一策略「**ETF动量**」仍会在 P0 被拒（`危险 import: sklearn`，交易环境禁用），
> 属平台安全规则（F10），**未在本次范围内改动**；如需该策略上线，请另行提出。

---

## 附：本稿事实来源（实测读数，供自查）

| 项 | 读数 | 来源 |
|---|---|---|
| 重验命令退出码 | **0** | `qs-compile deploy "output/.qs-deploy-quick/strat_0c202b.yaml"` 实跑 |
| 阶段 | P0–P6 **逐段 PASS** | 同上输出 |
| 产物三件 | `strat_0c202b_trade.py` / `report.md` / `runbook.md` | 同上输出 `artifact[...]` 行 |
| 残留提示数 | 3 条，无 `set_limit_mode` / 无 `get_order` | 同上输出 `note:` 行 |
| 命令可用性 | `usage: qs-compile deploy [-h] config` | `qs-compile deploy --help` |
| 策略↔配置映射 | `derive_strategy_id("全球资产轮动") = strat_0c202b` | `quickmode.derive_strategy_id` 实测 |
| 双远程一致 | plus = eco = `e941944faad…` | `git ls-remote` 双 URL |

# 修复方案：raw 撮合下 ETF 除权日送股补正（因子反推）

> 供 reasonix 审核。审核通过后实施。
> 关联：PTrade 撮合对齐（raw close）+ 工作包 D（QFQ 数据质量）
> 性质：**框架层行为变更**（撮合引擎公司行为处理逻辑），适用 AGENTS.md 铁律。

---

## 一、问题定义

### 现象

撮合改为 raw close（PTrade 对齐）后，ETF 动量策略回测结果严重偏离 PTrade：

| 指标 | PTrade | 本地（修复后） | 差异 |
|------|--------|--------------|------|
| 总收益率 | -23.85% | **-61.91%** | **差 38%** |
| 07-01 买入 159995 | 30100 股 | 30100 股 | ✅ 一致 |
| 07-07 除权送股 | 30100→**60200** 股 | **未送股（30100）** | ❌ **缺失** |
| 07-16 卖出 159995 | 60200 股 × 1.317 = 79,283 | 30100 股 × 1.317 = 39,642 | ❌ **差 50%** |

### 根因（已实证）

```
stock_dividend 表（DuckDB 主库）：2181 行，全部是股票（002/301/603/688 开头）
ETF（5/1 开头）分红/拆分记录：0 条 ← 完全缺失

159995（芯片ETF）07-07 除权（10送10，close 3.009→1.501）：
  stock_dividend 无记录 → _apply_corporate_actions 查不到 → 送股不执行
  → 持仓 30100 股不翻倍 → 净值虚假腰斩 -50%
```

**方案 A（前复权撮合）时代不受影响**：close_front 自动消除除权跳价，不需要送股补正。撮合改 raw 后，除权补正责任从"前复权自动处理"转移到"_apply_corporate_actions 手动处理"，但转移未完整——stock_dividend 缺 ETF。

---

## 二、修复设计：preClose 反推除权补正（兜底路径）

### 2.1 核心原理

日线快照的 `preClose` 字段在除权日是**除权参考价**（管线已处理），非除权日是**前一日收盘价**。利用这个特性检测除权事件：

```
ratio = prev_close / curr_preClose

非除权日：preClose = prev_close → ratio = 1.0 → 不触发
除权日：  preClose = 除权参考价 → ratio > 1.0 → 触发补正
```

**实证**（159995 芯片ETF）：

```
07-06: prev_close = 3.009（raw close）
07-07: preClose = 1.505（除权参考价 = 3.009 × 0.5）
ratio = 3.009 / 1.505 = 1.9993
吸附到 0.5 倍数 → 2.0
送股：30100 × 2.0 = 60200（PTrade 精确一致 ✅）
```

### 2.2 ratio 吸附逻辑

A 股送股比例通常是 0.5 的倍数（10送5=1.5, 10送10=2.0, 10转10=2.0）。数据源 preClose 有 3 位小数舍入误差（1.9993 vs 精确 2.0），需吸附：

```python
def _snap_split_ratio(ratio: float) -> float:
    """吸附除权比例到 0.5 的倍数（A股送股通常为整数或0.5倍数）。
    容差 0.5%：1.9993 → 2.0，1.5027 → 1.5。超出容差不吸附（保守）。"""
    snapped = round(ratio * 2) / 2
    if snapped > 0 and abs(ratio - snapped) / snapped < 0.005:
        return snapped
    return ratio  # 不吸附，保留原始比例
```

### 2.3 与现有逻辑的兼容（统一覆盖股票+ETF）

```
_apply_corporate_actions（行 506，现有逻辑不变）：
  查 stock_dividend → 有记录（股票）→ 精确 cash/stk 处理 → 记录到 result.corporate_actions

_apply_factor_derived_split（新增，行 527 curr_data 加载后执行）：
  对每个持仓标的：
    1. 若已在 result.corporate_actions 中有当日记录（stock_dividend 已处理）→ 跳过
    2. prev_close ← prev_data[code]['close']（前一日 raw close）
    3. curr_preClose ← curr_data[code]['preClose']（当日快照）
    4. ratio = prev_close / curr_preClose
    5. ratio ≤ 1.01 → 无除权，跳过
    6. ratio > 1.01 → _snap_split_ratio(ratio) → 送股 + avg_cost /= ratio
```

**分工**：
- **股票**：stock_dividend 有精确记录（cash_div + stk_div）→ 现有逻辑处理，preClose 反推跳过（步骤 1）
- **ETF**：stock_dividend 无记录 → preClose 反推兜底
- **统一生效**：两个路径互补，股票精确值优先，ETF 兜底覆盖

### 2.4 送股执行（与现有 _apply_corporate_actions 同语义）

```python
added = int(round(old_volume * snapped_ratio)) - old_volume  # 新增股数
added = round_to_lot(added, 100)  # 整手取整
new_volume = old_volume + added
pos.avg_cost = pos.avg_cost * old_volume / new_volume  # 成本摊薄
pos.volume = new_volume
pos.can_sell += added  # T+0 ETF 立即可卖；股票 T+1 下日可卖
```

159995 验证：`30100 × 2.0 = 60200`，`avg_cost = 3.321 → 1.661`（减半）✅

---

## 三、净值计算影响分析

### 结论：无独立问题——送股正确执行后净值自然正确

净值计算链路（backtest_engine.py:534/579）：

```python
prices = {code: curr_data['close']}   # 估值价 = raw close（修复后）
nav = cash + sum(volume × prices[code])
```

除权日（07-07）净值对比：

| 场景 | volume | raw close | 市值 | 前一日市值 | 变化 |
|------|--------|-----------|------|-----------|------|
| ❌ 未送股 | 30100 | 1.501 | 45,180 | 90,570 | **-50.1% 虚假腰斩** |
| ✅ 送股后 | 60200 | 1.501 | 90,360 | 90,570 | **-0.23% 正确**（pctChg=-0.27%） |

**送股执行后，净值在连续性和涨跌幅上都正确。** 不需要额外的净值修复。

### 需确认的边界

1. **除权日当日的撮合价**：07-07 除权日如果策略下单，撮合价 = raw close = 1.501（除权后价格）✅ 正确——PTrade 也是除权后价格成交。
2. **送股日的 can_sell**：ETF T+0 下日可卖（`can_sell += added`）；股票 T+1 次日才可卖（现有逻辑已处理）。
3. **pctChg 涨跌停判断**：P1-3 已改用 pctChg 列（预计算真实涨跌幅），不受 preClose 原始化影响 ✅。

---

## 四、PTrade 对齐验证

| 对齐项 | PTrade | 本地（修复后+本方案） | 状态 |
|--------|--------|---------------------|------|
| 撮合价 | raw close | raw close | ✅ 已对齐（P1-1/2） |
| 除权送股 | 30100 → 60200 | 30100 → 60200（preClose 反推+吸附） | ✅ 本方案补齐 |
| avg_cost 调整 | 3.321 → 1.661 | 3.321 → 1.661（÷2.0） | ✅ 本方案补齐 |
| 净值连续性 | 除权日 -0.27% | 除权日 -0.23%（pctChg 口径） | ✅ 一致 |
| 现金分红 | 自动入账 | stock_dividend 有记录时入账；ETF 纯分红暂不覆盖（见技术债） | ⚠️ 部分 |

---

## 五、技术债（本次不修复，记录）

**ETF 纯现金分红（无送股）的除权**：preClose 反推 ratio ≈ 1.0 + 微小偏差（如 1.005），吸附到 1.0 后不触发送股——正确（纯分红不送股），但现金分红入账也缺失。影响 < 5%（现金分红通常远小于送股比例），列入技术债：

> TD-ETF-DIV：ETF 纯现金分红（无送股）的现金入账缺失。数据源：qfq_aux.db fund_adj 因子微变化可检测（factor 1.0 → 1.005），但需分离现金分红金额。影响 <5%，待股票 stock_dividend 的 ETF 扩展或因子现金分红字段接入后修复。

---

## 六、代码修改点

### 修改 1：`backtest_engine.py` 新增 `_apply_factor_derived_split`

**位置**：`_apply_corporate_actions`（行 704）之后新增方法。

**调用点**：主循环行 527（`curr_data = self._get_daily_data(day)`）之后插入：

```python
curr_data = self._get_daily_data(day)
...
# 因子反推除权补正（ETF 兜底：stock_dividend 无 ETF 记录时从 preClose 反推）
self._apply_factor_derived_split(curr_data, prev_data, day_str)
```

### 修改 2：方法实现

```python
def _apply_factor_derived_split(self, curr_data, prev_data, day_str):
    """ETF 除权补正兜底：preClose 反推除权比例（stock_dividend 无 ETF 记录时）。
    
    preClose 语义：除权日=除权参考价（管线处理），非除权日=前一日收盘价。
    ratio = prev_close / curr_preClose > 1.01 → 除权事件 → 送股补正。
    吸附 0.5 倍数处理 preClose 舍入误差（1.9993 → 2.0）。
    股票已被 _apply_corporate_actions（stock_dividend 精确值）处理 → 跳过。
    """
    if not self.account.positions:
        return
    if curr_data is None or prev_data is None:
        return
    from .libs.shared_ashare_rules import round_to_lot
    # 已被 stock_dividend 处理的标的（避免重复送股）
    already_handled = {a.get('code') for a in self.result.corporate_actions
                       if a.get('date') == day_str}
    # 构建前一日 close 映射
    prev_close_map = {}
    if 'code' in prev_data.columns:
        for _, row in prev_data.iterrows():
            prev_close_map[str(row['code'])] = row.get('close', 0)
    for code, pos in self.account.positions.items():
        if pos.volume <= 0:
            continue
        bare = code.split('.')[0] if '.' in code else code
        if bare in already_handled:
            continue  # stock_dividend 已处理
        # 获取 prev_close 和 curr_preClose
        prev_close = prev_close_map.get(bare, 0)
        if prev_close <= 0:
            continue
        row = curr_data[curr_data['code'] == bare]
        if len(row) == 0:
            continue
        preclose = row.iloc[0].get('preClose', 0)
        if preclose <= 0:
            continue
        ratio = prev_close / preclose
        if ratio <= 1.01:
            continue  # 非除权日
        # 吸附 0.5 倍数
        snapped = round(ratio * 2) / 2
        if snapped > 0 and abs(ratio - snapped) / snapped < 0.005:
            ratio = snapped
        if ratio <= 1.01:
            continue  # 吸附后不触发
        # 执行送股
        old_volume = int(pos.volume)
        new_total = int(round(old_volume * ratio))
        added = round_to_lot(new_total - old_volume, 100)
        if added <= 0:
            continue
        new_volume = old_volume + added
        pos.avg_cost = pos.avg_cost * old_volume / new_volume
        pos.volume = new_volume
        pos.can_sell += added
        self.result.corporate_actions.append({
            'date': day_str, 'code': bare,
            'type': 'factor_derived_split',
            'ratio': ratio, 'old_volume': old_volume,
            'new_volume': new_volume, 'added': added,
            'note': 'preClose反推（stock_dividend无记录）',
        })
        logger.info(f"[Split] {code} {day_str} 因子反推送股: "
                    f"{old_volume}→{new_volume} (ratio={ratio:.4f}, "
                    f"preClose反推)")
```

---

## 七、测试方案

新增 `tests/test_factor_derived_split.py`：

1. **ETF 除权送股**：159995 场景（prev_close=3.009, preClose=1.505 → ratio 2.0 → 30100→60200）
2. **非除权日不触发**：prev_close=preClose → ratio=1.0 → 无操作
3. **吸附逻辑**：ratio=1.9993 → 吸附 2.0；ratio=1.5027 → 吸附 1.5；ratio=1.37 → 不吸附
4. **stock_dividend 已处理跳过**：有记录的股票不重复送股
5. **净值连续性**：除权日市值变化 ≈ pctChg（-0.27%），非 -50%
6. **avg_cost 调整**：avg_cost /= ratio
7. **ETF动量策略回归**：07-07 除权日净值不跳水，与 PTrade 对齐

---

## 八、不变项（禁止改动）

- `_apply_corporate_actions` 现有逻辑（stock_dividend 路径）不变
- 撮合价（match_prices）构建逻辑不变
- 估值价（prices）构建逻辑不变
- 涨跌停判断（pctChg 列优先，P1-3）不变
- get_history/get_price 的 fq='pre' 信号路径不变
- align/_apply_qfq 签名和返回值不变

---

## 九、风险评估

| 风险 | 等级 | 缓解 |
|------|------|------|
| preClose 反推误触发（数据异常日） | 中 | ratio > 1.01 阈值 + 0.5 倍数吸附 + 0.5% 容差；异常 ratio 不吸附保守不送股 |
| 与 stock_dividend 重复送股 | 低 | already_handled 集合跳过；测试 4 覆盖 |
| 现金分红丢失（纯分红 ETF） | 低 | 技术债 TD-ETF-DIV 登记；影响 <5% |
| 吸附误差导致送股数偏差 | 低 | 0.5% 容差 + round_to_lot 整手；PTrade 实证 60200 精确匹配 |

---

## 十、验收标准

1. **159995 精确对齐**：07-01 买入 30100 股 → 07-07 送股 60200 股 → 07-16 卖出 60200 股 × 1.317 = 79,283（与 PTrade 一致）
2. **净值连续性**：07-07 除权日净值变化 ≈ -0.27%（pctChg），非 -50%
3. **ETF动量净值对齐**：本地 -23.85%（PTrade）→ 修复后差异 < 1%
4. **股票不回归**：stock_dividend 有记录的股票走原有路径，行为不变
5. **测试全过**：新增 7 例 + 既有回归

---

## 十一、与数据管线方案的分工（zcode 审核 R-2 回写）

> 关联方案：`pipeline-etf-dividend-integration-plan.md`（QuantStudio 管线接入 etf_dividend + stock_dividend_full）。
> 本方案只解决**撮合引擎的除权补正计算**；分红数据由管线方案提供。

| 问题 | 解法 | 依赖 | 状态 |
|---|---|---|---|
| ETF/股票分红数据缺失（stock_dividend 无 ETF、仅 2026 现金、无送转字段） | 管线方案：stock_dividend_full（全历史含送转）+ etf_dividend（ETF 每份派息 div_cash） | 数据管线任务 | 待执行（本方案之后/并行） |
| ETF 份额拆分补正缺失（159995 2026-07-07 10送10；tushare fund_div 只管分红不管拆分，fund_div 对 159995 返回 0 条） | **本方案**：preClose/factor 反推送股（`_apply_factor_derived_split` + 吸附） | 仅需 etf_adj_factor/preClose（本地已存在） | 本方案 |
| ETF 纯现金分红入账（TD-ETF-DIV） | 引擎读 `etf_dividend` 表（管线产出 div_cash）+ 引擎消费逻辑（本方案实施时一并接入，或紧随其后） | 两方案联合 | 待管线落地后联合验收 |

**边界说明（重要）**：
- 本方案（preClose 反推）**不依赖 etf_dividend 表**，可先行实施、并行推进（R-5）；
- 管线方案落地后：股票侧 `stock_dividend` 全历史（含 stk_div/stk_bo_rate/stk_co_rate）使"股票精确路径（cash+stk）"真正生效，本方案 §2.3 分工前提（stk_div 全 0）随之成立；
- 联合验收：ETF 动量回测净值对齐（管线后 ETF 现金入账 + 本方案拆分补正共同生效，目标差异 <1%）。

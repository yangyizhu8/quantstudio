# ETF/股票回测除权补正方案 v2-final（可落地执行版）

> **状态**：zcode 执行许可——已修 already_handled bug（P0-1）并显式标注全部 P0/P1 修订落点（§十二 索引）；按 R-5 与管线方案并行实施。
> **取代**：`etf-split-factor-derived-fix-plan.md`（原方案 v1，含 §十一 分工回写，被本文档取代，不再作为实施依据）。
> **审核依据**：`etf-split-factor-derived-fix-plan.review.md`（审核意见：P0-1/P0-2/P1-3/P1-4/P1-5 + §八 tushare 实测勘误）——全部修订已落入本文档。
> **关联管线方案**：`pipeline-etf-dividend-integration-plan.md`（zcode 已批准执行，§四步骤 1→11）。
> 性质：**框架层行为变更**（撮合引擎公司行为处理），适用 AGENTS.md 铁律（本地修复 → 用户确认 → GitHub 双仓库同步 + README/docs）。

---

## 一、问题定义

raw 撮合（PTrade 对齐）下，ETF 除权日送股缺失：`stock_dividend` 无 ETF 记录 → `_apply_corporate_actions` 查不到 → 送股不执行 → 净值虚假腰斩 -50%。

| 指标 | PTrade | 本地（修复前） | 差异 |
|------|--------|--------------|------|
| 07-01 买入 159995 | 30100 股 | 30100 股 | ✅ |
| 07-07 除权送股（10送10） | 30100→**60200** | **未送股** | ❌ |
| 07-16 卖出 | 60200×1.317=79,283 | 30100×1.317=39,642 | ❌ 差 50% |

**根因（已实证）**：stock_dividend 无 ETF 记录（2181 行全为股票，仅 2026 现金）；ETF 份额折算（159995 2026-07-07，prev_close 3.009 / preClose 1.505 = 1.9993）**无任何结构化数据源**（tushare fund_div 只管分红不管拆分，实测对 159995 返回 0 条；fund_split/fund_convert 等接口不存在）。

**数据管线落地后的状态**（管线方案批准执行）：stock_dividend 将变为全历史精确送转（stk_div/stk_bo_rate/stk_co_rate）→ **股票精确路径（cash+stk）真正生效**；etf_dividend 表提供 ETF 每份派息（div_cash）→ ETF 现金入账可行。**但 ETF 份额拆分仍只能靠本方案 preClose/factor 反推**。

---

## 二、两方案分工（与管线方案 §〇 一致）

| 问题 | 解法 | 依赖 | 状态 |
|---|---|---|---|
| ETF/股票分红数据缺失 | 管线方案（stock_dividend_full + etf_dividend） | 数据管线 | 已批准，执行中 |
| ETF 份额拆分补正缺失（159995 10送10） | **本方案**：preClose/factor 反推送股 | 仅需 etf_adj_factor/preClose（已存在） | **本方案，可立即并行实施** |
| ETF 纯现金分红入账（TD-ETF-DIV） | 引擎读 etf_dividend（§三.6 阶段 2） | 管线落地后 | 阶段 2 |

---

## 三、修复设计 v2

### 3.1 核心原理（不变）

日线快照 `preClose` 在除权日 = 除权参考价（管线已处理），非除权日 = 前一日 close（16 标的多板块抽查 + 159995 实证）。

```
ratio = prev_close / curr_preClose
```

### 3.2 带区规则【P0-2/P1-3/P1-4 修订落点——取代 v1 §2.2/§六 的"未吸附也送股"逻辑】

| 带区 | 判定 | 动作 |
|---|---|---|
| ratio < 0.99 | 份额合并 | **对称处理**【P1-4】：吸附 0.5 倍数（容差 0.5%）命中 → `volume ×= ratio`（整手向下取整）、`avg_cost /= ratio`；未吸附 → **WARN + 跳过** |
| 0.99 ≤ ratio ≤ 1.01 | 非除权日 | 跳过 |
| 1.01 < ratio < 1.10 | **现金分红带**【P0-2】 | **跳过 + WARN**（不送股、不改 avg_cost、不入账——现金由阶段 2 etf_dividend 精确入账） |
| ratio ≥ 1.10 | 送股/份额折算 | 吸附 0.5 倍数（容差 0.5%）：命中用吸附值；**未命中按原值送股 + WARN**【P1-3】 |

依据：
- 现金分红带（1.01~1.10）跳过：ETF 单次现金分红收益率实测 < 10%（510500：1.0%~4.5%；510880：3%~5.5%），该带区全部为现金分红事件（全宇宙实测 1575 次），原 v1 代码会在此带区产生幽灵送股（510880 ratio≈1.0347 → +3.47% 假股 + 成本错误摊薄），**必须跳过**；
- ≥1.10 未吸附按原值：真实份额折算比例并非全为 0.5 倍数（实测 512890≈2.0462、510500 2022-08-29≈1.1455、513100≈5.0019 吸附 5.0），按原值送股正确；
- <0.99 对称处理：份额合并日不处理会净值虚涨 1/ratio（实测全宇宙 725 次，如 511030≈0.1、510580≈0.248），对称公式与送股一致。

### 3.3 范围：ETF-only【P1-5 修订——取代 v1 "统一覆盖股票+ETF"】

反推路径**仅对 `is_etf(bare)` 生效**。股票完全走 stock_dividend 精确路径（管线落地后含全历史送转字段，`_apply_corporate_actions` 不变），**股票回测行为零变化**（验收"股票不回归"严格成立）。已处理的 ETF（未来若 stock_dividend 出现 ETF 行）由 already_handled 跳过（3.4）。

### 3.4 already_handled 修复【P0-1 修订——zcode 确认的一行版】

```python
already_handled = {str(a.get('code', '')).split('.')[0]
                   for a in self.result.corporate_actions
                   if a.get('date') == day_str}
```

> 背景：`_apply_corporate_actions` 记录的 code 是 **QMT 格式**（`backtest_engine.py:721` `self._to_qmt(...)` → `'600000.SH'`），v1 用 `bare in already_handled` 裸码比对 → 永远 False → **股票被重复送股（股数翻倍）**。裸码统一后（positions 键 `'600000.SH'` → `bare='600000'`），与 `curr_data['code']`（裸码）口径一致。
> 新记录 append 的 `'code'` 也统一用裸码（与 curr_data 比对口径一致）。

### 3.5 执行代码（完整，修订点已标注）

**调用点**：`backtest_engine.py` 主循环行 527（`curr_data = self._get_daily_data(day)`）之后、`prices`（534）之前插入：

```python
curr_data = self._get_daily_data(day)
...
# ETF 除权补正兜底：preClose 反推（ETF-only，stock_dividend 无 ETF 记录时）
self._apply_factor_derived_split(curr_data, prev_data, day_str)
```

**方法实现**：

```python
def _apply_factor_derived_split(self, curr_data, prev_data, day_str):
    """ETF 除权补正兜底：preClose 反推除权比例（仅 ETF，stock_dividend 无 ETF 记录时）。

    带区规则（v2-final §3.2）：
      ratio < 0.99      份额合并 → 对称处理（吸附 0.5 倍数，未吸附 WARN+跳过）【P1-4】
      0.99~1.01         非除权 → 跳过
      1.01 < ratio < 1.10 现金分红带 → 跳过 + WARN（阶段2 etf_dividend 精确入账）【P0-2】
      ratio >= 1.10     送股/折算 → 吸附 0.5 倍数（容差 0.5%），未吸附按原值 + WARN【P1-3】
    仅 is_etf 生效（股票走 stock_dividend 精确路径，行为零变化）【P1-5】。
    already_handled 用裸码比对（corporate_actions code 为 QMT 格式）【P0-1】。
    """
    if not self.account.positions:
        return
    if curr_data is None or prev_data is None:
        return
    from .libs.shared_ashare_rules import round_to_lot
    from .libs.security_code_rules import is_etf
    # 【P0-1 修订】裸码统一：corporate_actions 的 code 是 QMT 格式（'600000.SH'）
    already_handled = {str(a.get('code', '')).split('.')[0]
                       for a in self.result.corporate_actions
                       if a.get('date') == day_str}
    prev_close_map = {}
    if 'code' in prev_data.columns:
        for _, row in prev_data.iterrows():
            prev_close_map[str(row['code'])] = row.get('close', 0)
    for code, pos in self.account.positions.items():
        if pos.volume <= 0:
            continue
        bare = code.split('.')[0] if '.' in code else code
        # 【P1-5 修订】仅 ETF 走反推；股票由 stock_dividend 精确路径处理
        if not is_etf(bare):
            continue
        if bare in already_handled:
            continue  # 已有精确记录（stock_dividend/现金入账）→ 不重复处理
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

        # ---- 带区规则（v2-final §3.2）----
        if ratio < 0.99:
            # 【P1-4 修订】份额合并：对称处理（吸附 0.5 倍数）
            snapped = round(ratio * 2) / 2
            if snapped > 0 and abs(ratio - snapped) / snapped < 0.005:
                ratio = snapped
            else:
                logger.warning(f"[Split] {code} {day_str} 疑似份额合并 ratio={ratio:.4f} "
                               f"未吸附，跳过（数据异常/非 0.5 倍数）")
                continue
            old_volume = int(pos.volume)
            new_total = int(round(old_volume * ratio))
            # 【P1-4 执行修正（2026-08-16 实施时发现）】round_to_lot 对负值截 0
            # （max(int(x/100)*100, 0)，A股订单语义，shared_ashare_rules.py:52）→ 合并为
            # 负向变化时 added 恒得 0 → 合并永不生效（与 §3.2 表格"volume ×= ratio（整手
            # 向下取整）"及 §六 测试 7 矛盾）。实施按 §3.2 语义对乘积整手向下取整：
            # new_volume = int(new_total/100)*100；并补 new_volume<=0 防御（防除零）。
            new_volume = int(new_total / 100) * 100
            added = new_volume - old_volume
            if added >= 0 or new_volume <= 0:
                continue  # 合并且无净减少/合并到 0 股（数值异常）→ 跳过
            new_volume = old_volume + added
            pos.avg_cost = pos.avg_cost * old_volume / new_volume
            pos.volume = new_volume
            pos.can_sell += added
            self.result.corporate_actions.append({
                'date': day_str, 'code': bare,
                'type': 'factor_derived_merge',
                'ratio': ratio, 'old_volume': old_volume,
                'new_volume': new_volume, 'added': added,
                'note': 'preClose反推合并（stock_dividend无记录）',
            })
            logger.info(f"[Split] {code} {day_str} 因子反推合并: "
                        f"{old_volume}→{new_volume} (ratio={ratio:.4f})")
            continue

        if ratio <= 1.01:
            continue  # 非除权日

        if ratio < 1.10:
            # 【P0-2 修订】现金分红带：不送股、不改成本（阶段2 由 etf_dividend 精确入账）
            logger.warning(f"[Split] {code} {day_str} 现金分红带 ratio={ratio:.4f} "
                           f"（收益率 {1-1/ratio:.2%}），跳过送股（TD-ETF-DIV/阶段2）")
            continue

        # ratio >= 1.10：送股/份额折算
        snapped = round(ratio * 2) / 2
        if snapped > 0 and abs(ratio - snapped) / snapped < 0.005:
            ratio = snapped  # 吸附命中（1.9993→2.0）
        else:
            logger.warning(f"[Split] {code} {day_str} 非 0.5 倍数折算 ratio={ratio:.4f}，"
                           f"按原值送股")  # 【P1-3】512890≈2.0462 等真实折算

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
                    f"{old_volume}→{new_volume} (ratio={ratio:.4f}, preClose反推)")
```

### 3.6 阶段 2：ETF 现金分红精确入账（依赖管线 etf_dividend 表，独立小步）

- 新增 `duckdb_data_access.query_etf_dividends(date_ms)`：`SELECT code, div_cash FROM etf_dividend WHERE ex_date = ?`（div_proc='实施'）；
- 新增 provider 方法 + 引擎 `_apply_etf_cash_dividends(day_str)`：对持仓 ETF，`cash += volume × div_cash`（**全额入账**——公募基金分红对个人投资者免征所得税，与股票 20% 短持税口径不同；以 PTrade 实测对齐为准），并记录 corporate_actions（type='etf_cash_dividend'）；
- 表不存在/未落地 → **no-op 跳过**（阶段 2 前不影响阶段 1）；
- 与阶段 1 的关系：现金分红带（1.01~1.10）阶段 1 跳过 + WARN 的兜底逻辑**保留**（etf_dividend 覆盖不全时 518880/159915 类缺口仍由 fund_adj/preClose 检测告警）。

---

## 四、净值影响（结论不变，验收口径修订）

送股正确执行后净值自然正确（无需额外净值修复）：

| 场景 | volume | raw close | 市值 | 前一日市值 | 变化 |
|---|---|---|---|---|---|
| ❌ 未送股 | 30100 | 1.501 | 45,180 | 90,570 | -50.1% 虚假腰斩 |
| ✅ 送股后 | 60200 | 1.501 | 90,360 | 90,570 | **-0.23%**（pctChg=-0.27%） |

**验收口径修订**：-0.27%（pctChg，close 1.501 vs preClose 1.505）与 -0.23%（净值）本就不相等，断言改为：**`|净值变化率 − pctChg| ≤ 0.1pp` 且非 -50%**。

---

## 五、PTrade 对齐表（更新）

| 对齐项 | PTrade | 本地（本方案） | 状态 |
|---|---|---|---|
| 撮合价 | raw close | raw close | ✅（P1-1/2 已完成） |
| 除权送股 | 30100→60200 | 30100→60200（preClose 反推+吸附） | ✅ 本方案 |
| avg_cost | 3.321→1.661 | ÷2.0 一致 | ✅ 本方案 |
| 净值连续性 | -0.27% | -0.23%（|Δ|≤0.1pp） | ✅ 本方案 |
| 现金分红 | 自动入账 | 阶段 2：etf_dividend.div_cash 全额入账（管线落地后） | ⏳ 阶段 2 |

---

## 六、测试方案（`tests/test_factor_derived_split.py`，10 例）

1. **ETF 除权送股**：159995 场景（prev_close=3.009, preClose=1.505 → ratio 1.9993 吸附 2.0 → 30100→60200）；
2. **非除权日不触发**：ratio=1.0 → 无操作；
3. **吸附逻辑**：1.9993→2.0；1.5027→1.5；
4. **【P0-1】already_handled QMT 格式**：corporate_actions 已有 `{'date': day, 'code': '600000.SH'}`（QMT 格式）+ 当日 preClose 异常 → 断言**不重复送股**；
5. **【P0-2】现金分红带**：ratio=1.0347（510880 型）→ 跳过 + WARN，无幽灵送股、avg_cost 不变；
6. **【P1-3】非 0.5 倍数折算**：ratio=2.0462（512890 型）→ 按原值送股；
7. **【P1-4】份额合并**：ratio=0.5 → 对称处理（volume×0.5、avg_cost÷0.5）；ratio=0.9718 → WARN+跳过；
8. **净值连续性**：除权日 |净值变化率−pctChg| ≤ 0.1pp，非 -50%；
9. **avg_cost 调整**：送股后 avg_cost×old/new；
10. **回归**：ETF动量策略 07-07 净值不跳水（与 PTrade 对齐）；股票策略黄金基线**零变化**（ETF-only 门控）。

---

## 七、不变项（禁止改动）

- `_apply_corporate_actions`（stock_dividend 路径）逻辑不变；
- 撮合价（match_prices）、估值价（prices）构建逻辑不变（raw close）；
- 涨跌停判断（pctChg 列优先，P1-3）不变；
- get_history/get_price 的 fq='pre' 信号路径不变；
- align/_apply_qfq 签名和返回值不变。

---

## 八、风险表（更新）

| 风险 | 等级 | 缓解 |
|---|---|---|
| 现金分红带误触发幽灵送股（v1 缺陷） | 高→已消除 | 【P0-2】带区规则：1.01~1.10 一律跳过 + WARN |
| already_handled 格式不匹配重复送股（v1 缺陷） | 高→已消除 | 【P0-1】裸码统一比对 + 测试 4 |
| QFQ discovery 洪水（管线切换 stock_dividend 全历史） | 高（管线侧） | 管线方案 §四步骤 4 决策矩阵（baseline-build 首选）；本方案不涉及 |
| fund_div 覆盖缺口（518880/159915 无记录） | 中 | 阶段 2 入账后缺口清单仍走 fund_adj/preClose 检测告警；登记 TD-ETF-DIV 兜底 |
| 份额合并带误判（0.9718 类异常/数据噪声） | 低 | 吸附 0.5 倍数 + 未吸附 WARN+跳过（保守） |
| 同一 ETF 同日现金+拆分（罕见） | 低 | already_handled 跳过拆分（保守）；日志可追溯 |
| 分钟 profile 未挂钩 | 低 | 明确范围：本方案仅日线主循环；minute-bar-v1/daily-open-close-proxy-v1 需另行挂钩（范围外，登记） |

---

## 九、验收标准（更新）

1. **159995 精确对齐**：07-01 买入 30100 → 07-07 送股 60200 → 07-16 卖出 60200×1.317=79,283（已复核 ✓）；
2. **净值连续性**：除权日 |净值变化率−pctChg| ≤ 0.1pp（-0.23% vs -0.27%），非 -50%；
3. **ETF动量净值对齐**：本地与 PTrade 差异 <1%（阶段 1 先验证拆分项；管线落地 + 阶段 2 后联合验收，含现金分红项）；
4. **股票零回归**：ETF-only 门控 → **反推路径不触碰股票**（黄金基线逐项一致，仅验证反推逻辑无侵入）。**注意（数据修正预期）**：管线切换 stock_dividend 后，股票现金入账金额将因**单位修正**（遗留源 601628 存 61.8 → tushare 每股 0.618）与全历史送转而变化——属数据层修正，股票回测结果以 PTrade 对齐为准重新比对，**不要求与旧基线逐位一致**；本验收项仅指"反推逻辑对股票零侵入"；
5. **测试全过**：新增 10 例 + 既有回归。

---

## 十、执行顺序（R-5 并行）

- **阶段 1（本方案，立即并行实施）**：§三.1~3.5 反推送股（不依赖管线）→ 测试 1-10（除阶段 2 项）→ ETF 动量回测拆分项对齐验证；
- **管线方案**（已批准）：按 pipeline 方案 §四步骤 1→11 执行；
- **阶段 2（依赖管线落地）**：§三.6 ETF 现金入账（etf_dividend 表）→ 联合验收（ETF 动量净值 <1% 含现金项）；
- 全程遵守铁律：本地修复 → 用户确认 → GitHub 双仓库同步 + README/docs（strategy_toolbox.md / prompt_engineering.md）+ MCP 实时进度报告更新。

---

## 十一、技术债（更新）

- **TD-ETF-DIV**：ETF 纯现金分红入账缺失 → **阶段 2 经 etf_dividend.div_cash 解决**；fund_div 覆盖缺口（518880/159915）→ 兜底 fund_adj/preClose 检测告警，待云端数据源补全；
- **TD-STOCK-DIV-COVERAGE**：stock_dividend 仅 2026 现金 → **由管线方案（stock_dividend_full 全历史含送转）解决**；
- **TD-ETF-MERGE**：份额合并补正 → **本方案 P1-4 已实现**，关闭；
- **分钟 profile 挂钩**：ETF 分钟回测若需同修复，另行任务（范围外登记）。

---

## 十二、修订索引（zcode 要求显式标注的落点）

| 修订 | 内容 | 落点 |
|---|---|---|
| **P0-1** | already_handled 裸码统一（QMT 格式 '600000.SH' vs 裸码 '600000' 不匹配 → 股票重复送股） | §三.4 修复 + §三.5 代码（已标注）+ 测试 4 |
| **P0-2** | 未吸附 ratio 不得直接送股（1.01~1.10 现金分红带 1575 次全量误触发） | §三.2 带区规则 + §三.5 代码（已标注）+ 测试 5 |
| **P1-3** | 非 0.5 倍数真实折算（512890≈2.0462 等 189 次）按原值送股 | §三.2 带区规则（≥1.10 未吸附按原值）+ 测试 6 |
| **P1-4** | 份额合并（ratio<0.99，全宇宙 725 次）对称处理 | §三.2 带区规则 + §三.5 代码（已标注）+ 测试 7 |
| **P1-5** | 范围收窄为 ETF-only（股票零回归） | §三.3 + §三.5 代码（已标注）+ 测试 10 |
| 审核意见 §八 | tushare 实测勘误（fund_div 存在但覆盖不全、无拆分接口） | §一 根因更新 + §三.6 阶段 2 + §八 风险表 |

---

## 十三、与管线方案的一致性确认

- 股票侧：管线落地后 stock_dividend 全历史精确 → 本方案 ETF-only 门控下股票路径零变化 ✓；
- ETF 侧：etf_dividend（div_cash）→ 阶段 2 现金入账；拆分仍由本方案反推 ✓；
- 顺序：本方案阶段 1 与管线并行；管线批准执行（R-1~R-6 已落入管线方案）；联合验收为最终目标 ✓。

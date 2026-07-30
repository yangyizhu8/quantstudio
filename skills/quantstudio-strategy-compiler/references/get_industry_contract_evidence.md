# get_industry 契约冲突证据（已裁决：降级 LOCAL_ONLY，见 §5）

> 本文件为 A5-iii 交付物。原为"只记录证据、待用户裁决"；**用户已于 2026-07-30 裁决认可降级 LOCAL_ONLY**，处置详见 §5。

## 1. 三方冲突

| 来源 | 对 `get_industry` 的定性 | 关键表述 |
|---|---|---|
| AGENTS.md 铁律#4（L148） | **本地别名，非 Ptrade API → 须 LOCAL_ONLY** | `get_industry`（本地别名，非 Ptrade API）、`load_research_signals`（本地研究辅助）列入 LOCAL_ONLY 本地扩展清单 |
| skill 签名档案 `ptrade-api-signatures.json`（L441-458） | **可移植 PORTABLE**（无 `unsupported_on_ptrade` 标记，contexts=research/backtest/trade） | `get_industry(code)` 返回 `{'sw_l1': {...}}`；注释承认 "Real-PTrade shape … is NOT verified" |
| Ptrade 官方文档 `Ptrade量化交易文档.md` | **不存在独立 `get_industry`** | 全文档仅出现 `get_industry_stocks`（行业→成份股），无独立 `get_industry` 函数（9 处 `get_industry` 命中全部为 `get_industry_stocks`/上下文） |

## 2. 本地签名（框架真相源）

`quantstudio/backtest/ptrade_api.py:1922`（另：`load_research_signals` 位于 `ptrade_api.py:1730`）：

```python
def get_industry(self, code):
    """获取证券行业信息（对应 Ptrade get_industry），APPROXIMATION_REQUIRES_CONFIRMATION（F4，非 PIT READY）。
    返回 {'sw_l1': {'industry_code','industry_name','classification_system','classification_version'}} 格式。
    签名不变；回测上下文自动注入当前回测日期（as-of），无有效历史归属返回 None，绝不使用最新行业。"""
    bare = bare_code(code)
    if self._reference is None:
        return None
    effective_date = str(self._current_date)[:10] if self._current_date else None
    return self._reference.get_industry(bare, effective_date)
```

- 入参：单 positional `code`（无 `date` 关键字）。
- 返回：**直接 `sw_l1` 字典**（非按 security 包裹），as-of 当前回测日。
- 标注 `APPROXIMATION_REQUIRES_CONFIRMATION`（F4，非 PIT READY）。

## 3. Ptrade 文档签名（若存在）

- 文档中**无独立 `get_industry`**。最近似的是 `get_industry_stocks(industry_code)`（行业→成份股，方向相反）。
- skill 档案注释假设的真实 Ptrade 形态：`{security: {'sw_l1': ...}}`（按 security 包裹）+ `date` 关键字 + 平台分类版本 —— **均未经文档证实**。

## 4. 结论与建议（未执行）

- **证据指向**：AGENTS.md 铁律#4 的 LOCAL_ONLY 定性更准确——Ptrade 公共 API 不存在独立 `get_industry`，框架的 `get_industry` 是本地适配别名。
- **当前状态**：skill 签名档案仍将其标为可移植，与 AGENTS.md 冲突，且自身已注明 "NOT verified"。
- **建议（待裁决）**：将 `get_industry` 从 `signatures` 移入 `local_only_symbols`（与 `load_research_signals` 同等处理），双目标生成时 BLOCK。
- **本波不执行**：依任务书 A5-iii「不得擅自改其 portability」，留待用户确认后在下发动作中处理；如需现在即对齐，请用户拍板。

## 5. 用户裁决（2026-07-30）

- **日期**：2026-07-30
- **结论**：**认可降级 LOCAL_ONLY**。
- **依据**：PTrade 官方文档仅提供 `get_industry_stocks`（行业代码→成份股列表），**无独立 `get_industry`**（个股→行业归属），支持 AGENTS.md 铁律#4 的定性。
- **处置**：
  1. `ptrade-api-signatures.json` 中 `get_industry` 已移入 `local_only_symbols`（24 → 25）；
  2. `signatures.get_industry` 标 `unsupported_on_ptrade: true`，`contexts` 去掉 `trade`（仅 research/backtest 本地可用）；
  3. 补 `replacement_for` 说明：PTrade 仅有 `get_industry_stocks(code)`（返回行业成分股列表），与本地 `get_industry`（返回个股行业归属）**语义不同、无直接等价**；双目标代码须改用 `get_industry_stocks` 重建逻辑，或降级为本地专有；
  4. 双目标（strict_ptrade）生成/校验时该 API 将被 **BLOCK**。

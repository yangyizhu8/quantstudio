# test_etf_daily_rejected_when_isST_missing 退役记录（2026-08-28）

## 处置：随 legacy 废弃退役（pytest.skip，非修复）

## 探针定位（定谳）

| 项 | 结论 | 证据 |
|---|---|---|
| 3 行死因 | **UnitCheck**（非 IsSTNull） | 测试数据 amount=160000/close=1.6/volume=10000 → ratio=10 > 2.0 上界 → validator.py L226-229 UnitCheck 整表拒 3 行 |
| IsSTNull 分支未触发 | schema `etf_daily.isST.required=false`（mcp_only） | alignment_rules.json L204 → validator.py L190 `elif isst_spec.get("required")` False → 分支永不进入 |
| 测试守护契约 | 已不存在 | 旧契约"required=true 整表拒缺列"随 legacy 废弃；现架构 = required=false + adapter 常态补列 |

## 关键洞察（为何非修复而是退役）

1. **测试自始未守护到目标行为**：构造数据 ratio=10 在 UnitCheck 下自始非法——即使 schema 仍 required=true，该测试也先死于 UnitCheck，从未真正测过 IsSTNull 分支；
2. **现架构已有正向守护**：`test_etf_daily_*` 正向测试断言 IsSTNull 拒绝数==0 + passed_df>0（adapter 补列后通过）——比反向锁"缺列拒"更贴合 required=false 设计；
3. **isST 缺列已由 adapter 常态补列覆盖**：mcp_adapter L2129 注释（isST 从 stock_basic.name LIKE %ST% 判断）+ xtquant_adapter L412 注释——缺列防护在数据源头，无需测试锁校验器行为。

## 替代守护

- adapter 补列正向断言（test_xtquant_daily_switch L282-287）——现架构权威守护；
- UnitCheck 兜底数据质量（ratio 非法自拒）。

## 结果

- 测试改为 pytest.skip（带退役原因注释），不删除函数体（保留决策痕迹）；
- 回归：test_xtquant_daily_switch 10 passed + 1 skipped；含 compliance 104 passed + 1 skipped 全绿。
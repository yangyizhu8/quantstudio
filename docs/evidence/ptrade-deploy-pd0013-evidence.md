# PD-0013 验收证据：契约表补 `get_order` / `get_open_orders`（2026-10-30）

> 方案：`docs/ptrade-deploy-contract-getorder-design.md`（②审四裁：取值采 (a) `backtest: true`）
> ③实施：`contract_table.yaml` 补 2 行 + `tests/test_deploy_contract_getorder.py`（6 用例）
> 本轮沉淀：PD-2026-0013 · 判型：**数据修正型**（note 消失，产物零改动）

## 1. 问题与依据

| 事实 | 证据 |
|---|---|
| `get_order(order_id)` 是 PTrade 真实 API | Context7 `/kay-ou/ptradeapi`：单笔委托查询（Order Query 段与 `get_orders`/`get_open_orders` 并列） |
| 本地回测引擎已实现 | `ptrade_api.py:2731 def get_order(self, order_id)`；同族 `get_orders:2693`、`get_open_orders:2708` |
| 契约表原缺 | 改前仅 `get_orders`（L360）；P5 报「契约表外，需人工确认」（**note，不阻断**） |

## 2. 实施内容

```yaml
  get_order:            # 新增
    signature: "get_order(order_id)"
    backtest: true
    trading: true
    action: keep
    replacement: null
  get_open_orders:      # 新增（同族补齐，防同类 noise 复发）
    signature: "get_open_orders(security=None)"
    backtest: true
    trading: true
    action: keep
    replacement: null
```

## 3. 验收四项（方案 §5）

| # | 标准 | 结果 |
|---|---|---|
| 1 | 实跑：P5 notes 不再出现 `get_order 未知 API` | ✅ `全球资产轮动` **status=PASS / P5=PASS**，notes 中 `get_order` 归零（端到端实测 `get_order_in_notes=False`） |
| 2 | 契约表 schema（5 字段齐全） | ✅ `test_contract_rows_exist_with_five_fields`（参数化 2 行） |
| 3 | deploy 全套件 + 契约矩阵 | ✅ **110/110**；`check_fund_matrix --check` = `OK`（本表非 wrapper 模板，无需 reverify） |
| 4 | 族机检 | ✅ PD 族 **13 件 / 不合规 0 件**（含新增 PD-2026-0013） |

## 4. 语义与回归

- `test_contract_rows_semantics_keep_both_sides`：双端 `true` + `action: keep` + `replacement: null`（与同族一致）；
- `test_get_order_signature_matches_local_impl`：签名与 `ptrade_api.py:2731` 实现一致（防表实不符）；
- 零行为变更：`keep` 不参与任何变换路径；产物代码与 P5 verdict 除 note 外不变。

## 5. 与 PD-0012 的关系

两者**互不依赖**但协同：PD-0012 解开 `set_limit_mode` 未门控（**阻断项**），PD-0013 消掉 `get_order`
契约噪音（**非阻断项**）。任一方单独存在时转换仍可能失败/仍带 noise，故建议同批出仓（独立提交，可分别回退）。

## 6. 六步进度

①方案 ✅ → ②审计 ✅（取值 (a) 采准）→ ③实施 ✅ → ④验收 ✅（本件）→ **⑤用户确认（待）** → ⑥随批统推。

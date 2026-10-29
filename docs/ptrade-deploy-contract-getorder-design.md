# PD-0013 方案：契约表补 `get_order`（及核 `get_open_orders`）

> 立项依据：`全球资产轮动` 转换 P5 note「未知 API（需人工确认）: 行 1203: get_order（契约表外）」；
> 前置：PD-0011 已出仓。状态：①方案（待②审计）——**未实施、未提交**。
> **注意**：本项**不阻断**转换（P5 记为 note 非 FAIL）；阻断项是 PD-0012。

## 1. 问题定义

| 事实 | 证据 |
|---|---|
| `get_order(order_id)` 是 PTrade **真实 API**（查本策略单笔委托） | Context7 `/kay-ou/ptradeapi`：`get_order(order_id)` → 单笔 Order 对象；同族 `get_orders`/`get_open_orders`/`get_all_orders`/`get_trades` |
| 本地回测引擎**已实现** | `quantstudio/backtest/ptrade_api.py:2731 def get_order(self, order_id)`（另有 `get_orders:2693`、`get_open_orders:2708`） |
| 契约表**缺行** | `contract_table.yaml` 仅有 `get_orders:`（L360）；无 `get_order`、无 `get_open_orders` |
| 后果 | P5 报「契约表外，需人工确认」（note）——不阻断，但每次转换制造噪音，并掩盖真实未知 API |

## 2. 目标

补契约行使 `get_order`（及 `get_open_orders`）进入已知 API 集合，消除噪音 note；
**不改变任何转换变换行为**（`action: keep`，与同族一致）。

## 3. 设计

参照同族 `get_orders` 现成条目（L360-365）：

```yaml
  get_order:
    signature: "get_order(order_id)"
    backtest: true
    trading: true
    action: keep
    replacement: null

  get_open_orders:
    signature: "get_open_orders(security=None)"
    backtest: true
    trading: true
    action: keep
    replacement: null
```

**字段判定依据**：
- `backtest: true` —— 本地引擎已实现（`ptrade_api.py:2731`）；回测语境可查策略自身委托；
- `trading: true` —— PTrade 文档明示「Get a single order specified by this strategy」（交易模块可用）；
- `action: keep` —— 与 `get_orders`/`get_open_orders`/`get_trades` 等同族一致（历史/查询类 API，两侧均可用，无需门控）。

**`get_open_orders` 一并补的理由**：PTrade 文档同段列出，且本地已实现；不补则下一策略大概率再报同一类 noise
（同族遗漏一并补齐，符合「修复通用性」铁律）。

## 4. 改动范围

| 文件 | 改动 |
|---|---|
| `quantstudio/ptrade_deploy/contract_table.yaml` | 新增 2 行条目（`get_order` / `get_open_orders`），置于 `get_orders` 相邻位置 |
| `tests/test_deploy_contract.py`（或现有 gates 测试） | 用例：①产物含 `get_order(x)` → P5 **无** unknown-API note；②契约表 schema 完整性（新增行含 5 字段）；③`get_orders` 行为不变（回归） |
| `docs/ptrade-deploy-pipeline-design.md` | 契约表种子条目集补 2 行说明 |
| 条目 | `PD-2026-0013` |

**不动**：gates 判定逻辑、transformer、source_import。

## 5. 验收标准（4 项）

1. `全球资产轮动` 实跑：P5 notes **不再**出现 `get_order 未知 API`（PD-0012 修好后应整体 PASS）；
2. 契约表 schema 校验（5 字段齐全）通过；
3. deploy 全套件绿 + 契约矩阵哈希一致（**注意**：改契约表是否触发 matrix 追认？——本表非 wrapper 模板，
   按铁律 10 第 6 条判定为**不触发**；实施时以 `check_fund_matrix.py --check` 实测为准，若 FAIL 则同批 reverify）；
4. 族机检全绿（新增 PD-2026-0013）。

## 6. 影响面 / 风险 / 回退

| 项 | 评估 |
|---|---|
| 转换行为 | 无变化（`keep` = 不门控、不替换、不删除） |
| 风险 | 若 `get_order` 在**回测**语境实际不可用（本地有实现但 PTrade 回测模块无），标 `backtest:true` 会让回测专用校验失真 —— **实施前须用 Context7 PTrade 文档确认回测模块是否提供该 API**（本方案已确认"交易模块可用"，回测侧待核；若不确定，保守取 `backtest: false` 并 `action: keep`，即"交易可用、回测不可用也不拦"需再议） |
| 回退 | 删 2 行，单批 revert |

## 7. 三型判定

**数据修正型**（契约表补遗漏行）：verdict 由「未知 API（note）」变为「已知 keep」；
影响面仅限该 API 的 note 消失，**不改变任何产物代码**，准确度只升不降（误报减少）。

## 8. R1 知识反哺检索留痕

```
知识反哺：[命中] PD-2026-0003（PD/D2/PD-2026-0003.md，2026-10-30）——契约表补充后 P5 notes 误报消失的先例
知识反哺：[无命中] PD 族检索（get_order / 契约表补行），2026-10-30
```

## 9. 排期

①方案（本件）→②审计→③实施→④验收→⑤确认→⑥随批统推（可与 PD-0012 同批，但**独立提交**，便于回退隔离）。

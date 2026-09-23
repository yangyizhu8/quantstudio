# 移交件：P1 对拍防线基准口径与脚本 → Trae（经用户转发）

- **移交方**：客户运维会话 2（dsh 侧）
- **接收方**：Trae（巡检项在建方）
- **日期**：2026-09-23
- **性质**：**基准口径与脚本复用移交**（成果移交，非问题上报）
- **背景裁定**：用户 2026-09-23 裁定——P1 的 C 路径（长期对拍防线）**不单独建设**，
  与 Trae 在建巡检项**合并为双层扫描器**：

  | 层 | 归属 | 内容 |
  |---|---|---|
  | 内层 | Trae（在建） | 自洽率基线突变扫描 |
  | 外层 | Trae 承接（本会话供口径与脚本） | tdx 前复权抽码对拍 |

- **上游依据**：`docs/evidence/p1-cloud-etf-minutes-close-caliber-aprraisal-20260923.md` ·
  `docs/p1-upstream-clarification-request-design.md`（§八）

---

## 一、要监测什么（不要再重新发现）

**对象**：云端 `etf_minutes` / `etf_daily` 的 **close 与 `amount/vol` 的口径一致性**（**不是** close 的绝对水平）。

**为什么用它做判据**：`amount/vol` 恒为**该时点的真实可成交价**（金额/成交量在复权下不变）
⇒ 是无外部依赖的**口径裁判**。

**已实测基线（2025-06-03，可直接作为内层基线值）**：

| 指标 | 基线实测值 | 说明 |
|---|---|---|
| `etf_daily` 的 `close/(amount/vol)` 中位 | **10.0005**（P10 9.5312 ~ P90 10.0371） | **稳定 ~10×**，非噪声 |
| `etf_daily` 的 `close/(amount/vol)` ≈1 占比 | **0.0000%** | 全样本偏离 |
| `etf_minutes` 的 `close/(amount/vol)` | 约 **0.111（=1/9）** | 同码同日 |
| 两表 `amount/vol` 互比 | 约 **10×**（日表 0.119 vs 分钟表 1.193） | 同码同日 |
| 跨表 `minute_close/daily_close` | P50 0.9981；≈1(±1%) 82.36%；≈⅓(±5%) 1.19% | **陷阱**：82% 的"≈1"发生在错误价格水平上 |
| 两表 `adj_factor` 一致率 | 89.69% | 分裂子集约 10.3% |
| 外部基准（tdx 前复权）校准 | 日表 ÷ tdx ≈ **0.9950**（✅）；分钟表 ÷ tdx ≈ **0.3325**（❌） | 判定谁在真实价格水平 |

> **内层扫描建议**：以「`close/(amount/vol)` 自洽率 / 中位比值」为指标，**监控其突变**
> （例如某日突然从 10.0 跳到 1.0 或 3.0）——突变=上游口径变更或修复落地，均需人工确认。

---

## 二、外部基准（外层）口径说明

**基准源**：tdx MCP（通达信问小达），工具 `mcp__tdx__tdx_wenda_quotes`。

| 要点 | 说明 |
|---|---|
| ETF 查询 | `range=JJ`；例：`question="159327最新行情"` |
| 股票查询 | `range=AG`；例：`question="600519最新行情"` |
| **历史日期** | **支持**——`question="159327在2025年6月3日的收盘价"` → 返回 `收盘价.前复权 2025.06.03 = 0.40` |
| 返回口径 | **前复权**（与云端 `is_qfq=true` 同族） |
| 附带能力 | `question="159327历史K线"` → 返基金份额净值/所有者权益序列（另一类校准基准） |
| 单位 | 元（与云端 close 同单位） |

**对拍口径**：
- 取**同码同日**：云端 `etf_daily.close` ÷ tdx 前复权 close（该日无除权事件时应 ≈1）；
- 云端 `etf_minutes` 当日 close ÷ tdx 前复权 close（同为前复权，应 ≈1）；
- 判定：**哪一个 ≈1，哪一个就在真实价格水平**（当前实测：日表 ✅、分钟表 ❌）。

---

## 三、脚本复用要点（已在仓，只读）

| 脚本 | 作用 | 复用建议 |
|---|---|---|
| `scripts/acceptance/p1_baseline_1and2_probe.py` | 基准②自洽性（日表三字段互证）+ 基准①跨表对照 | 直接复用；内含窗口口径修正（见 §四） |
| `scripts/acceptance/p1_baseline_3_tdx.py` | tdx 外部基准单码三点对照 | **tdx 值为硬编码**（需按查询结果更新）——建议外层扫描改为**实时调用** |
| `scripts/acceptance/p1_baseline_3_scale.py` | tdx 与云端逐点比值 | 同上 |
| `scripts/acceptance/p1_baseline_3_cross_table.py` | 跨表 `amount/vol` / close 分布统计 | 直接复用（内层基线来源） |

**复用前提**：需 `MCP_API_KEY`（本仓 `config/secrets.env` 或环境变量）；全部只读，不写云端。

**MCP 客户端用法（脚本内已封装的调用形态）**：
```python
from quantstudio.pipeline.mcp.client import MCPClient, load_mcp_api_key
cli = MCPClient(endpoint="https://124.223.159.234/mcp", api_key=load_mcp_api_key())
cli.handshake()                      # 必须：否则 tools/call 报 Missing session ID
ref = cli.create_export_job("qdb.etf_daily", page_size=50_000,
                            time_start=ts, time_end=te, row_limit=None)
man = cli.get_manifest(ref)          # 逐 shard get_artifact(...) 取 parquet 字节
```

---

## 四、边界事实（务必遵守，否则取数静默为空）

| # | 事实 | 后果 |
|---|---|---|
| 1 | 服务端时间窗为「**左闭右开**」语义 | 同日窗口 `(D, D)` **返回 0 行**；必须用 `(D, D+1)` |
| 2 | `etf_minutes` 属大表 ⇒ **必须传 `row_limit`**；`etf_daily` 可不传 | 大表不传报错 |
| 3 | 单作业**行数硬上限 5,000,000**（请求更大值也恒返 500 万） | 超限**按最老优先静默截断** ⇒ 全市场当日对照需按时段/码分片 |
| 4 | 分钟表**末组数据滞后**（勘察日止于 2026-09-21） | 与 tdx 当日对照会"云端无数据"，非缺陷 |
| 5 | `fetch_page` 的 cursor 首页须传 `""`（非 `None`） | `None` 会校验失败 |
| 6 | 客户端 `create_export_job` **不支持 `ts_codes`** | 按码过滤须本地过滤 |

---

## 五、移交边界（本会话不做的事）

- ❌ 本会话**不建设**对拍防线（按裁定 C 并入 Trae）；
- ❌ 不修改任何代码（本件仅口径与脚本移交）；
- ❌ 不代 Trae 决定扫描频率/告警阈值/告警通道——由 Trae 按其巡检框架确定（本件只提供**基线值**与**判据口径**）。

---

## 六、建议的落实顺序（供 Trae 参考）

1. 先以内层（自洽率基线）跑通：用 §一基线值建立"当前值 → 突变告警"的最小闭环；
2. 再叠外层（tdx 抽码对拍）：**抽样策略建议**——固定码池（含本件 §一 的 `159327` 等分裂子集）+ 随机抽样（覆盖两表 `adj_factor` 不一致子集约 10%）；
3. 告警口径：内层突变即告警；外层**连续 N 次比值偏离 1 超容差**才告警（防单次数据延迟误报）；
4. 与上游请求（`docs/p1-upstream-clarification-request-design.md`）联动：上游修复落地后，本扫描器即为**修复验证手段**（§六 验收要点第 4 项）。
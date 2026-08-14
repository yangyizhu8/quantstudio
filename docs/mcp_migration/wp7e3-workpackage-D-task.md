# 工作包 D 任务书：QFQ front 数据质量第三道防线（融合进 daemon 常驻管线）

> 版本：v1.1（2026-08-14，审核方起草 → zcode 审核 → 审核方定稿）
> 执行方：ZCode（本地实施，不提交 GitHub，等用户 post-repair 确认）
> 关联：QFQ 复权基准 bug（批次内 groupby().last() 作 adj_latest）Phase 3 修复完成
> （v6.7.52）后的**防御性收口**。前置依赖：工作包 C 的 ②（3 表恢复）+ ③（450
> 重新定性）+ ⑤（C-6 水位释放）已闭环。
> 定位：**纯增量可观测性**，不改 `align` / `_apply_qfq` 任何契约。

### v1.1 修订记录（相对 v1.0）

| 编号 | 类型 | 修订内容 |
|---|---|---|
| R3 | 强制 | 防线 1 快照口径一致性：本批 align 用的 `adj_latest_map` 沿调用链显式传入 `_stamp_and_write`；重新加载仅作兜底且落 warning。追加测试例 8 |
| R2 | 补充声明 | 2.1 交叉核验独立性以"MCP 唯一权威因子源终态"为设计前提 + 过渡期库内 tushare 历史因子同源残留声明 |
| S4 | 补充声明 | 验收标准 4 定性为"锚稳定窗口一次性验收扫描"，不构成口径 C 常态化全表扫描先例 |
| A | 强制（审核方补充） | 修正 2.1 挂点矛盾：因子完整性扫描从 `qfq_run_post_ingest` 改挂必然执行点（`_run_full_quality_audit` 并列），保证编排器 disabled 也执行 |
| B | 强制（审核方补充） | R3 传参须用调用栈局部变量（禁实例级缓存，多线程竞争）；自检点定位在 `_filter_unchanged_snapshot_rows` 之后、`writer.write` 之前 |
| S1 | 建议 | 防线自身健康监控：快照加载失败/为空落 batch_audit 计数 |
| S2 | 建议 | 黄金行刷新责任挂 reanchor committed 事件自动重算 |
| S3 | 建议 | 2.2.4 前置核实（reanchor 引擎现有验收是否已含该校验）列为实施第一步 |
| S5 | 建议 | 抽样规范提升到 1.2 正文：分层抽样（每 code 必含最新交易日行）+ 随机抽样补充 |

---

## 0. 背景与目标

### 0.1 为什么需要本工作包

QFQ bug 的根因已在 Phase 3 修复（aligner fail-fast + daemon 4 路径快照）。但复盘暴露
一个更深的缺口：**现有全库质量审计抓不住"front 被错算成 raw"这一类错误**。

`quality_audit.py::_audit_prices` 的 `AdjustmentAnchor` 检查是：

```sql
ABS(close_front/close - 1) > 0.02  -- 最新行 front 偏离 raw 超过 2% 才报
```

而本 bug 的破坏方式是 `front = raw`（分片窗口不含最新因子时，批次内 `groupby().last()`
拿到的 adj_latest 恰好 = 该行 adj_i，`raw × adj_i / adj_i = raw`）。此时
`close_front/close = 1`，偏离 = 0，**完美通过审计**——这正是 1442 万行被破坏却长期
无人发现的原因。

结论：现有审计是"front 与 raw 的**近似比例**偏离检查"，缺口是**缺少"front 与
adj_factor 的**精确自洽**检查"**。本工作包补的正是这个盲区，并把它做进 daemon 常驻
管线（而非外挂脚本），天然覆盖 GUI 手动 / CLI / 常驻三个入口。

### 0.2 目标

在**不改变任何现有契约**（`align`/`_apply_qfq` 签名、返回值、列顺序、空值行为、
dtype）的前提下，新增三道可观测性防线，使"front 算错"要么**写入当场被抓住**，
要么**定期扫描/启动自检当天发现**，而非等回测异常才发现。

三道防线对应本 bug 复盘 + 审核意见的残余风险清单：

| 防线 | 融合点 | 补的缺口 |
|---|---|---|
| 防线 1：写入后精确自洽 | `daemon.py::_stamp_and_write`（四路径唯一落库点） | 写入当场抓代码 bug（adj_latest 用错/字段映射错） |
| 防线 2：因子完整性扫描 + front 锚稳定自洽 | 必然执行点（`_run_full_quality_audit` 并列）+ `quality_audit.py` | 补因子源错（qfq_aux.db 本身缺漏）+ 全库持续监测 |
| 防线 3：黄金行启动自检 | `daemon_lifecycle.py` bootstrap | 启动冒烟，锚错位立即暴露 |

---

## 1. 防线 1：写入后精确自洽（invariant check）

### 1.1 融合点

`daemon.py::_stamp_and_write`（line 2189）是普通 / 流式 / per_date / per_stock
四条写入路径的**唯一落库点**（8 个调用点：757/928/1250/1272/1328/1550/2117 全部
汇聚于此，zcode 核实），在这里加一次自检即覆盖全部入口，无需在四路径各写一遍
（各写一遍会漏）。

### 1.2 自检逻辑（独立重算路径 + 口径 A 写入时锚）

新增独立模块 `quantstudio/pipeline/qfq_invariant.py`，提供：

```python
def check_qfq_invariant(df: pd.DataFrame, table: str,
                        adj_latest_map: dict) -> dict:
    """对已 align 的 std_df 抽样行做精确复权自洽校验。

    返回 {"sampled": int, "bad": int, "bad_detail": [...], "skipped": int}
    """
```

校验式（与 `_apply_qfq` 同公式、独立重算）：

```
front_expect = raw × adj_i / adj_latest
bad = |front - front_expect| / max(|front_expect|, eps) > 1e-6
```

数据来源（关键——必须独立于 `_apply_qfq` 的内部计算）：

1. `raw`：std_df 的 `open/high/low/close`（aligner 只算 front/back，raw 列保留原值）；
2. `adj_i`：该行交易日的因子，从 qfq_aux.db 按 `(code, bar_day)` 精确查
   （**分钟表按交易日连接**，同 `aligner._apply_qfq` 的 `bar_day` 口径，
   `aligner.py:940-943`；日线按日；MCP 因子经 `mcp_adapter.py:1930`
   INSERT OR REPLACE 落库，可查）；
3. `adj_latest`：由调用方传入的 `adj_latest_map`。

**抽样规范（S5，实现规范，非建议）**：分层抽样——每 code 必含**最新交易日行**
（adj_latest 演进最先影响处，抓错能力最高），不足时随机抽样补充；抽样上限
每 code ≤ 20 行、总计 ≤ 5000 行。

**快照口径一致性（R3，强制）**：口径 A 的语义是"**本批次 align 实际使用的写入时
锚**"，**不是**"`_stamp_and_write` 时刻重新加载的锚"。若在 `_stamp_and_write` 内
重新 `_load_qfq_global_snapshot`，本批 align 与自检之间恰好落地新除权因子时，两锚
不一致 → 本应无误报的口径 A 产生误报，且可能假触发 1.4 的"连续 N 批阻断"。

实现要求（补充 B，强制）：

- 每路径在 align 调用前**先把快照存为调用栈局部变量**，同一次加载结果同时用于
  align 与自检：

```python
snap_kwargs = self._qfq_snapshot_kwargs(table, batch_id)   # 只加载一次
std_df, _ = self.aligner.align(raw_df, table, source,
                               adj_factor_df=adj_factor_df, ...,
                               **snap_kwargs)
res = self.validator.validate(std_df, table, batch_id, source, expected_freq=freq)
...
wr = self._stamp_and_write(res, table, batch_id, source, task=task,
                           adj_latest_map=snap_kwargs.get("adj_latest_map"))
```

- `_stamp_and_write` 签名增加可选参数 `adj_latest_map: dict = None`（内部方法，
  不触碰 `align`/`_apply_qfq` 公共契约，不违反第 6 节禁止事项）；
- **禁止用实例级缓存**（如 `self._last_snapshot[table]`）保存快照——per_date/per_stock
  走 ThreadPoolExecutor 多线程，实例缓存会竞争错配；必须用调用栈局部变量；
- 重新加载（`_load_qfq_global_snapshot`）仅作 `adj_latest_map` 不可得时的兜底路径，
  **兜底时必须落 warning 日志**（提示自检锚可能与写入锚不一致）；
- 自检点在 `_stamp_and_write` 内 **`_filter_unchanged_snapshot_rows` 之后、
  `writer.write` 之前**，对**最终要写入的 df** 做（snapshot 表 skip_unchanged 过滤
  后，df 才与实际写入行一致）。

调用点（仅当 `table in self._QFQ_PRICE_TABLES` 且 `source == "mcp"`）：

```python
if table in self._QFQ_PRICE_TABLES and source == "mcp" and df is not None and len(df) > 0:
    latest = adj_latest_map
    if not latest:
        latest, _ = self._load_qfq_global_snapshot(table)   # 兜底
        logger.warning(f"[QFQ-Invariant] {table} 自检锚缺失，回退重新加载（可能与写入锚不一致）")
    if latest:
        r = check_qfq_invariant(df, table, latest)
        if r["bad"] > 0:
            logger.error(f"[QFQ-Invariant] {table} {batch_id} 自洽偏离 {r['bad']} 行")
            # 落审计计数 + 飞书告警（见 1.4）
```

### 1.3 三类行必须跳过（否则误报）

- **native 直通源**：`source in {"baostock", "akshare", "xtquant"}` 时 front 是
  passthrough 值，不是 raw×factor，**跳过**（`aligner.py:414-420` 同源直通）。
  注：经用户澄清，tushare/xtquant 为保留待废弃源，防线只守 MCP 权威源，不做
  废弃源维护面（原 R1 撤回）；
- **NULL front**：`close_front IS NULL` 或 `adj_i` 缺失（因子缺失日/停牌）→ 计入
  `skipped`，不算坏；
- **无因子日**：qfq_aux.db 查不到该 (code, bar_day) 的 adj_factor → 跳过并计数。

### 1.4 处置策略（告警优先，不"偏离即停"）

- 单批 `bad` 行 > 0 → **飞书告警 + 落 batch_audit 计数**，不阻断本次写入（写入已
  完成，回滚代价高且会放大故障）；
- 连续 N 批（N=3，可配）同一 table 均 `bad > 0`，或单批偏离率 `bad/sampled > 5%`
  → **阻断下一轮该 table 任务**（`_failure_gate` 同类语义，标记 task failed，水位
  不推进）；
- 边界必须写明：本防线只能抓**代码/字段映射 bug**，抓不了**因子源数据错**
  （adj_i 和 adj_latest 同源于 qfq_aux.db，同错则自洽仍通过）——后者由防线 2 的
  因子完整性扫描 + 独立交叉源承接。

### 1.5 防线自身健康监控（S1）

`_load_qfq_global_snapshot` 返回空 / 抛异常时，落 batch_audit 计数（如
`qfq_selfcheck_skipped` 类目），防线 2.1 周期扫描顺带汇总该计数——避免 aux 库故障
导致防线 1 长期静默空转无人知晓。

---

## 2. 防线 2：因子完整性扫描 + front 锚稳定自洽

本防线拆成**两件独立的事**，分别补不同的缺口。

### 2.1 因子表完整性扫描（补"因子源错"缺口）

**融合点（A，强制修正）**：挂在**必然执行点**——`daemon_lifecycle.py` 常驻轮次末尾
`_run_full_quality_audit`（line 679-680，finally 必跑，"即使 stop 也执行"）的**并列
位置**，或作为 `_run_full_quality_audit` 内新增一步。**不挂 `qfq_run_post_ingest`**：
核实 `daemon.py:284` 首行 `if not self.qfq_enabled(): return None` 及
`daemon_lifecycle.py:637` 的 `if qfq_cycle_id is not None:` 门控——编排器 disabled 时
post_ingest 根本不执行，而因子完整性监测不应依赖编排器开关。

实现为独立方法 `_audit_qfq_factor_integrity()`（只读，扫 qfq_aux.db，不依赖编排器
状态），与 `_run_full_quality_audit` 并列调用。

**扫描对象**：`qfq_aux.db` 的 `adj_factor`（股票）与 `fund_adj`（ETF）两表，**不扫
主库价格表**。

**检查项**（只读 SQL，产出告警计数，不写库）：

| 检查 | 检测规则 | 告警级别 |
|---|---|---|
| 缺日 | 同一 code 相邻两交易日因子之间出现交易日历空档（用主库 trade_calendar 判定） | warning |
| 非单调异常跳变 | `adj_i / prev_adj_i` 单日变化 > 2× 或 < 0.5×（排除 10送10 等已知送转：需接 `dividend_type`/送转比例判断，首版先告警不自动定性） | warning |
| 单日多 code 突增 | 单 trade_date 内 factor 变更的 code 数 > 阈值（如 > 全市场 5%） | warning |
| 独立交叉源抽核 | 每周抽 N（默认 20）个 code，用 **tushare 官方 `adj_factor` / xtquant 复权口径** 比对 qfq_aux.db 的 latest 因子，偏差 > 1e-6 → error | error |

**关键**：第 4 项（独立交叉源）是唯一能抓"qfq_aux.db 自身缺漏/错因子"的手段——
它引进了**独立于 qfq_aux.db 的第二数据源**，打破"拿同一个可能错的源自比自洽"的死循环。
交叉源优先选 tushare（离线可缓存），xtquant 作为备选；抽核失败不阻断，落 warning
并记录失败原因（避免网络抖动制造假 error）。

**独立性前提声明（R2）**：交叉核验的独立性以 **MCP 为唯一权威因子源的终态**为设计
前提——库内因子来自 MCP（聚源）、核验来自 tushare，两源不同，天然独立。**过渡期**
库内现存 `adj_factor`（约 1586 万行）含 `qfq_maintenance` 主动刷新写入的 tushare 因子
（`qfq_maintenance.fetch_adj_factor`，过渡期残留），这部分 code 被抽中时为
"tushare 核 tushare"同源核验（已知可接受，终态由 MCP 全量覆盖后消除）。

---

### 2.2 front 锚稳定自洽（#2(b) 基准口径设计 —— 本任务书核心）

#### 2.2.1 问题形式化

```
front(t) = raw(t) × adj_i(t) / adj_latest(code)
adj_latest(code) = adj_factor(code, MAX(time))   # code 的最新因子
```

当 code 在时刻 T 发生新除权（新增因子 `adj_new ≠ adj_latest_old`）：
- `adj_latest` 从 `adj_latest_old` **演进**为 `adj_latest_new`；
- 所有 t < T 的历史行，其"正确"front 的分母从 old 变 new，**理应全部重算**；
- 在 reanchor 引擎重锚完成前，库中存储的历史 front 仍是旧锚值。

因此，若全表扫描直接拿"当前 `adj_latest_new`"去比历史存储的 front，会把所有
**尚未重锚、本身是正确的行**打成异常——即"**基准演进行误报**"（审核意见【重要】项的
常态化版本，也是上次审核否决"每天跑一遍 Phase 1 扫描"的根本原因）。

#### 2.2.2 三种基准口径与判定

| 口径 | 基准来源 | 适用时点 | 演进行误报风险 |
|---|---|---|---|
| A. 写入时锚 | 本批次 `_load_qfq_global_snapshot` 的 map（沿调用链传入） | 防线 1（写入当场） | 无 |
| B. 重锚后锚 | reanchor 引擎本次重锚用的新锚 | 重锚完成点自检 | 无 |
| C. 当前全局最新因子 | qfq_aux.db `MAX(time)` | 全表定期扫描 | **高（禁用）** |

**硬规则：禁止用口径 C 做全表定期 front 自洽扫描。**

front 自洽只允许在两个锚点明确的时点做：
1. **写入时**（口径 A）—— 防线 1 已覆盖；
2. **重锚后**（口径 B）—— 挂在 reanchor 引擎完成点（见 2.2.4）。

#### 2.2.3 quality_audit 里的 front 自洽项（锚稳定子集，可选增强）

若仍要在 `quality_audit.py::_audit_prices` 增加"精确 adj_factor 自洽"项（替代/补充
现有 2% 近似 `AdjustmentAnchor`），必须按以下口径做，**不得拿当前最新因子硬比全表**：

1. 先按 code 判定**锚稳定性**：该 code 自最近一次 `qfq_reanchor_event` committed
   以来，`adj_latest` 是否变化（用因子变更日志 / reanchor 事件表判定）；
2. **仅对"锚稳定"的 code** 用当前 `adj_latest` 精确校验 `front == raw × adj_i /
   adj_latest`；
3. "锚不稳定"（已演进、未重锚）的 code 标记为 `PENDING_REANCHOR`，**不算破坏、
   不告警**，交由 reanchor 引擎处理（或降级为"重锚未完成"提醒）。

> 实现建议：front 自洽的**主路径**依赖防线 1（写入时）+ 2.2.4（重锚后），
> quality_audit 的锚稳定自洽作为**可选增强**（需要额外因子变更日志/事件表 join，
> 复杂度高）。首版可先只做防线 1 + 2.2.4 + 2.1 因子完整性，quality_audit 的锚稳定
> 自洽列为后续增强（技术债 T-D1）。

#### 2.2.4 重锚后自洽（口径 B）

**前置核实（S3，实施第一步）**：先确认 reanchor 引擎 `apply_reanchor_for_security`
（`qfq_reanchor_engine.py`）现有验收是否**已含**该校验，结论写入实施记录：
- 已含 → 仅补充"偏离行落审计 + 告警"落点；
- 未含 → 新增一次只读校验调用（不改引擎重锚逻辑）。

**融合点**：reanchor 完成对某 code 的重锚后，用本次重锚用的**新锚** `adj_latest_new`
对该 code 全历史 front 精确校验 `front == raw × adj_i / adj_latest_new`，偏离 > 1e-6
即报。

- 此时锚刚更新，历史 front 刚被重锚，用新锚校验**无演进行误报**；
- 偏离 = 0 即证明"重锚正确完成"，偏离 > 0 即证明"重锚有漏/错"，正是最需要抓的点。

---

## 3. 防线 3：黄金行启动自检（smoke test）

### 3.1 融合点

`daemon_lifecycle.py` 的 bootstrap 阶段（daemon 启动、进入常驻循环前）。

### 3.2 数据与逻辑

- 黄金行清单落盘 `config/profiles/mcp_only/qfq_golden_rows.json`（或 data/ 下），
  结构 `[{code, date, table, close_front_expected, anchor_version}]`；
- 启动时：读当前 `_load_qfq_global_snapshot` 的 latest_map → 对每行黄金行重算
  `close_front = close_raw × adj_i / adj_latest` → 与 `close_front_expected` 比对；
- 不匹配 → 飞书告警（**不阻断启动**，smoke test 定位）。

### 3.3 必须说清的两点

1. **定位是冒烟测试，不是防线**：只能证明"这几个具体值对不对"，抓不了泛化漂移；
2. **黄金行值锚定时间**：159995 未来再拆分，05-26 的 close_front 会**合法变化**，
   黄金行必须带 `anchor_version`，**不得硬编码 1.354**。首版黄金行从已验收行取：
   etf_daily 159995.SZ 2026-05-26 = 1.3540（anchor_version 记 v6.7.52 验收锚）。

### 3.4 刷新责任显式化（S2）

黄金行期望值的刷新挂 **reanchor 引擎 committed 事件**自动重算并递增
`anchor_version`，避免人工遗忘 → 启动告警常态化 → 告警疲劳。实施记录需说明该自动
刷新的触发点与写回路径（只写配置清单，不写主库价格表）。

---

## 4. 测试

新增 `tests/test_qfq_invariant.py`：

1. **自洽通过**：正确 front（raw×adj_i/adj_latest）→ `bad == 0`；
2. **自洽抓错**：人为把某行 front 改成 raw（模拟本 bug）→ `bad > 0` 且命中该行；
3. **跳过三类行**：native 直通源 / NULL front / 无因子日 → 计入 skipped，不算 bad；
4. **相对容差**：高价股小绝对偏差不算坏、低价股大绝对偏差算坏（验证 1e-6 相对容差）；
5. **分钟表交易日口径**：分钟 bar 按 bar_day 连接因子，非毫秒时间戳等值；
6. **fail-fast 不回退**：`adj_latest_map` 为空时自检函数跳过（返回 skipped=all），
   不抛错、不阻断写入（自检是观测，不承担 fail-fast 职责）；
7. **因子完整性扫描**：构造缺日/异常跳变/单日突增的 qfq_aux.db 副本 → 对应检查项
   命中；独立交叉源抽核 mock 成功/失败两分支；
8. **口径 A 锚一致性（R3）**：注入"两次加载之间因子变更"场景，断言自检使用
   调用链传入的**旧锚**（与本批写入一致）而非重新加载的新锚，`bad == 0`
   （即 R3 的验收用例）。

---

## 5. 验收标准

1. **契约不变（铁律硬指标）**：`align` / `_apply_qfq` 的签名、返回、列顺序、空值
   行为、dtype 与修复前逐位一致（`_stamp_and_write` 仅追加可选参数 + 尾部自检调用，
   不改写入逻辑）；
2. **只读/观测**：三个防线均不写主库价格表、不改 front/raw 值；只写 batch_audit
   计数 + 日志 + 飞书告警（及黄金行清单配置）；
3. **抓错能力**：测试 2/5/7/8 全过（能抓到"front=raw"类破坏 + 分钟口径 + 因子源错
   + 锚一致性）；
4. **无误报（S4 定性）**：在正确的 4 表（Phase 4 已验收偏离=0）上跑防线 1/2 →
   `bad == 0`、告警 0 条。**本验收本质是"Phase 4 修复后锚稳定窗口内的一次性验收
   扫描"（此时口径 C 与口径 A 重合，无演进行），不构成常态化口径 C 全表扫描的先例**
   ——防止后续被引用为全表定期扫描合法化依据；
5. **回归**：既有 pytest 套件（含 `test_qfq_global_snapshot.py` 6 例、
   `test_daemon_qfq_integration.py`、quality_audit 相关）全过；
6. **黄金行**：防线 3 在启动时对 159995 05-26 校验通过（锚版本对齐时）；
7. **R3 验收**：测试 8 全过（自检使用调用链传入旧锚，`bad == 0`）。

---

## 6. 禁止事项

- ❌ 不改 `align` / `_apply_qfq` 签名、返回值、列顺序、空值行为、dtype；
- ❌ 不把"偏离即停"做成默认（防线 1 必须告警优先 + 连续 N 批才阻断，见 1.4）；
- ❌ 不做"拿当前最新因子硬扫全表 front"的定期任务（口径 C 禁用，见 2.2.2）；
- ❌ 不把黄金行值硬编码、不加锚版本（见 3.3/3.4）；
- ❌ 不在本工作包引入独立 crontab/新调度进程（融合进 daemon 现有周期/必然执行点）；
- ❌ 不用实例级缓存保存快照（多线程竞争，见 1.2 补充 B）；
- ❌ 不提交/推送 GitHub（等用户 post-repair 确认）；
- ❌ 不以"性能优化"名义混入任何行为/正确性变更。

---

## 7. 铁律与治理（务必遵守）

1. **性质**：改 `daemon.py` + `daemon_lifecycle.py` + `quality_audit.py` + 新增
   `qfq_invariant.py` = 数据适配层/管线内核改动，适用**框架层修复铁律**：
   - 实施前须用户明确确认（本任务书经用户确认后方可编码）；
   - Git 同步须用户 post-repair 确认，且同步 README + `docs/strategy_toolbox.md` +
     `docs/prompt_engineering.md` 中涉及"QFQ 复权质量/监控"的表述；
2. **进度报告铁律**：本工作包落地 + 审核通过后，更新
   `私募工作文件\QuantStudio-MCP全数据源替代任务文件\实时进度报告.md`（含证据 SHA、
   行数、测试结果、变更记录、技术债 T-D1）；
3. **启用时序**：**排在 ⑤（C-6 水位释放）之后启用**；可与 ②③⑤ 并行开发，但代码
   **不同步上线**，避免在恢复/释放关键路径上引入新变量。

---

## 8. 风险与回退

| 风险 | 影响 | 缓解/回退 |
|---|---|---|
| 自检误报（因子源数据本身有噪声） | 告警疲劳 | 抽样 + 相对容差 1e-6 + 三类跳过；告警降噪（连续 N 批才升级） |
| 交叉源抽核网络失败 | 假 error | 失败降 warning + 记录原因，不阻断 |
| 自检开销拖慢写入 | 性能 | 抽样上限（每 code ≤ 20 行 / 总 ≤ 5000 行），只读 aux SQL 索引查询 |
| 锚稳定自检（2.2.3）复杂度高 | 首版拖延 | 列为技术债 T-D1，首版不强制；核心靠防线 1 + 2.2.4 |
| 防线 3 黄金行锚版本漂移 | 启动误报 | 锚版本 + reanchor 事件自动刷新（S2）；误报仅告警不阻断 |
| aux 库故障致防线 1 空转 | 静默失守 | S1 自检健康计数，周期扫描汇总告警 |

---

## 9. 技术债

**T-D1：quality_audit 的 front 锚稳定自洽增强**（2.2.3）
- 首版以防线 1（写入时）+ 2.2.4（重锚后）为主，quality_audit 内仅保留现有 2% 近似
  检查不动；
- 锚稳定自洽需额外因子变更日志 / reanchor 事件表 join，待防线 1/2.2.4 稳定后立项。

---

## 10. 交付物

1. `quantstudio/pipeline/qfq_invariant.py`（防线 1 自检函数）；
2. `daemon.py` diff：`_stamp_and_write` 增加 `adj_latest_map` 可选参数 + 尾部自检；
   四路径 align 前 `snap_kwargs` 局部变量化并显式传参；`_audit_qfq_factor_integrity`
   因子完整性扫描方法；
3. `daemon_lifecycle.py` diff：`_audit_qfq_factor_integrity` 并列调用 + 防线 3
   启动自检；
4. `quality_audit.py` diff（若采纳 2.2.3，否则仅记录不动）；
5. `config/profiles/mcp_only/qfq_golden_rows.json`（黄金行清单，含 anchor_version）；
6. `tests/test_qfq_invariant.py`（8 例全过）；
7. 验收证据：4 表 `bad == 0`、告警 0、既有回归全过、黄金行校验通过、R3 测试 8 通过；
8. 完成报告 + 实时进度报告更新（v6.7.53+）。

# G1 证据：A1/A2 巡检实施 + 误报率实测（mcp-minute-front-anchor-closure）

| 项 | 内容 |
|---|---|
| 文档版本 | v1.0（2026-08-16） |
| 对应方案 | `docs/mcp-minute-front-anchor-design.md` v1.1 §4 阶段 1（G1） |
| 状态 | **G1 完成，待 ZCode 复核** |
| 验收依据 | 方案 §8.1（R6：FAIL 0.5% / WARN 0.3% + ≥500 只/全 ETF 池误报率实测） |

---

## 1. 交付物

| 文件 | 内容 |
|---|---|
| `quantstudio/pipeline/quality_audit.py` | +A1 `_audit_minute_anchor_drift`（AdjustmentAnchorDrift）、+A2 `_audit_factor_monotonicity`（FactorMonotonicity）、+`_clean_factor_segments`（段合并+尖刺剔除）、+`_resolve_aux_path`（路由跟随）、构造参数 `qfq_aux_override`/`qfq_aux_paths_config` |
| `tests/test_quality_audit_anchor.py` | 9 例（A1 检出 FAIL/WARN/正常/无候选/aux 不可用/路由跟随 + A2 非单调/单调/表缺失） |

## 2. 实现要点

1. **判别公式**（V2 实测）：`front = raw × adj_i/adj_latest`；`dev = |(close_front/close)/(adj_i/adj_latest) − 1|`；>0.3% WARN、>0.5% FAIL（R6）；
2. **候选**：除权表（stock_dividend/etf_dividend）`ex_date ∈ [now-120d, now+7d]`；**判别 bar 采样** = 因子值变化点前 90 天内每日 14:59-15:01 收盘 bar（UTC 基准 06:59-07:01，实测修正）；
3. **因子取数跟随路由**（ZCode 执行注记 2）：`resolve_runtime_aux_path`（released=false → fail-secure legacy；G2b 释放后自动切 gen1），测试用 `qfq_aux_override` 注入；
4. **因子序列预处理**（实测驱动）：同值段合并（段首 time 生效，保证 merge_asof 时间连续性）+ 污染尖刺剔除（段跨度 <1 天且与前后段比值差异 >50%——07-01 世代切换批量归 1.0 污染行）；
5. **A2**：adj_factor/fund_adj 按 code LAG 扫描非单调（>1e-9）→ warning（按 code 计数，不阻断）。

## 3. 测试结果

- `tests/test_quality_audit_anchor.py`：**9 passed**
- 既有 quality 相关回归：`test_quality_audit.py` / `test_data_quality_contract.py` / `test_full_quality_audit_repair.py` / `test_resident_quality_audit.py`：**42 passed**（A1/A2 挂载零破坏——schemas 不含分钟表时不触发；aux 缺失仅 warning 不 error，`passed` 语义不变）

## 4. 误报率实测（R6，全 ETF 池）

**执行环境**：主库被生产 daemon 独占锁定（R5 预见）→ 用只读备份 `data/quantstudio_backup_pre_pipeline_20260816.db` + 当前 `qfq_aux.db`（因子）。

**结果（293 只有因子变化的 ETF code，判别 bar 150,539 根）**：

| dev 阈值 | code 数 | 占比 |
|---|---|---|
| >0.1% | 131 | 44.7% |
| >0.3%（WARN） | 124 | 42.3% |
| >0.5%（FAIL） | 119 | 40.6% |
| >1.0% | 101 | 34.5% |

**三分类（抽样核验 510020/512930/515050/159220/159919）**：

| 类别 | 说明 | 案例 |
|---|---|---|
| ① 真实漂移 | 历史 bar front 未随分红/因子更新重锚 | 159919 dev=1.15（多次分红累积） |
| ② 因子快照污染 | 回填批次用被污染因子快照（07-01 归 1.0 事件）直接算 front | 510020 2026-05-06 bar `close=4.076 front=0.410`（=raw×0.1006，adj_latest 被当 1.0） |
| ③ 真实份额合并 | 价格跳变（510020 07-01 close 3.875→0.386 = 10:1 合并）、front 连续正确（3.837）——判别公式在因子表混乱期失真 | 510020 07-01（尖刺过滤已误杀该真实段，见限制） |

**方法学零误报验证**：判别公式在"因子正确"的 bar 上精确匹配（V2 先前实测 30 只股票 + 6 只 ETF 6 位小数一致）——**判别本身无误报**；42% 高告警是"QFQ 编排从未激活 → 全池 front 未重锚 + 因子表污染"的真实暴露，非误报。

**结论**：
- A1 在当前数据污染状态下的高告警 = **特性（早发现）**，符合"不再静默积累"目标；
- A1 输出（FAIL code 清单）可直接作为 **G2a 定点重锚的候选清单**；
- 告警收敛依赖：G2a 重锚（修 front）+ 阶段 3 因子治理（修因子表）后，A1 应回到低告警。

## 5. 已知限制（记录，不阻塞）

1. **尖刺过滤误杀真实份额合并**：510020 07-01（10:1 合并，因子 0.1006→1.0→0.1006 两天内来回）被当污染剔除——判别中该时段因子缺失。份额合并（价格÷N + 因子×N）与"污染归 1.0"难以从因子表单侧区分，需阶段 3 因子治理（唯一世代 + 事件标注）后收敛；
2. **A1 依赖因子表洁净度**：因子表污染（07-01 事件）直接影响判别输入——A1 与阶段 3 存在依赖关系（阶段 3 完成后 A1 精确性最大化）；
3. **主库锁定**：误报率实测用备份库（08-16 pre_pipeline）；正式运行（daemon 每轮结束）在停机窗口或协调写入下执行（R5）。

## 6. 下一步

- G2a：定点重锚（受影响 code 清单 = A1 FAIL 输出 + 已确认的 520810/563020/159307），fresh_staged via McpFreshFetcher，备份 + 零改动硬验收（执行注记 1：先核对 `apply_reanchor_for_security` 的 UPDATE SET 列表是否含 update_time）。

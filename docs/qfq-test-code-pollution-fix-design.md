# 错误二方案：MCP 测试码污染统一拦截与存量清理（六步①，2026-09-22）

- 性质：**框架层·数据契约缺陷修复**（正确性，非性能）。
- 独立立项；与错误一案互不捆绑，但本案是错误一 T4（收敛环恢复）的**运行前置**。
- 溯源注记：原报方声称已有 `docs/mcp-test-code-pollution-fix-design.md`——本机+副本+
  他仓全检索（限深 4）**0 命中**，维持「强证据指向虚报」措辞，待源会话标识后升级定性。

---

## 一、背景与动机

### 1.1 污染实测（本会话独立复核 + 报告账目合并披露）

| # | 位置 | 脏值 | 来源 | 本会话复核 |
|---|---|---|---|---|
| 1 | 落盘分片 `exp_etf_daily_*/j_*_etf_daily_part_00004.parquet.ts_code` | `TEST999.SH`（2 行） | 报告 | 未重开 parquet（可复核，非阻断项） |
| 2 | `qfq_aux.db.adj_factor.code` | `FIXTEST` | 报告 | ✅ **影子 aux/现 aux 只读实测命中** |
| 3 | 主库 `block_trade.ts_code` | `TEST.SH` | 报告 | ✅ 影子库命中 1 行 |
| 4-6 | `limit_cpt_list`/`limit_step`/`stk_factor_pro`/`top_list` | `TEST.SH` | 报告 | 主库持锁未逐一复核，读窗并入 T5 清点 |
| 7 | `sentiment_factor_daily.ts_code` | `GISISI_TEST`/`GISISI_TEST3` | 报告 | ⚠️ **该表列语义待前置查证**（疑似指标名域而非证券码域，处置见 T5 特判） |

### 1.2 双重实害（为何是「中高」而非「洁癖」）

1. **收敛环被卡死（实测耦合，错误一直接受损）**：9-22 **01:48:02**
   `[qfq_orch] 周期异常: 非法 code（非裸 6 位纯数字）: 'FIXTEST'`（daemon.log 实录）——
   `_discover` 整轮 ValueError ⇒ re-anchor 永久停摆 ⇒ 错误一的 53,925 trigger 积压
   （9-18 QualityAudit 实测）与 85-code 台阶**无法自愈**。
2. **重操作误触发**：`TEST999` 驱动 ETF 全历史因子冷启动（报告 §2.1，每进程一次的重导出）。
3. 统计污染：passthrough 表混入非真实证券记录（下游榜单/因子审计面）。

### 1.3 四点不一致（根因框架，报告 §2.3 经核）

| 环节 | 行为 | 代码锚点 |
|---|---|---|
| 映射表归一 | 非合规→None 丢行（干净） | `aligner.py` 58-66 |
| 落盘分片 | **原样保留** | `mcp_landing/*.parquet` |
| aux 注入 | **无校验入库**（FIXTEST 实锤） | `mcp_adapter._inject_adjfactor/_sync_factor_snapshot` |
| QFQ 校验器 | 抛 ValueError **中断整批/整轮**（非 fail-safe） | `qfq_reanchor_schema.py:150-163` + 调用方 |
| passthrough 写表 | **无 code 契约** | `writers.py` passthrough 通道 |

## 二、范围与边界

- **主拦截原则：形式契约即可覆盖全部 8 处实测**（TEST999/FIXTEST/TEST.SH/GISISI_* 均
  **非 6 位纯数字、非合法 `6位.交易所后缀` 形态**）⇒ 门=**契约校验**（归一后校验），
  **deny 关键词清单仅作「形态合法但占位/测试语义」的兜底**（如 999999 类，本期**不预设
  任何关键词拦截**，避免误杀语义未知数据）。
- **表列适用域**：契约**只对「列语义=证券 code」的表列生效**（配置化白名单），
  杜绝误伤指数/北交所/基金等合法 6 位码与非 code 语义列。
- 不改：re-anchor 数学、水位契约、策略层零触碰；`_normalize_code` 本体保持严格
  （fail-safe 上移到调用方）。

| 改动文件 | 内容 |
|---|---|
| `quantstudio/pipeline/code_contract.py`（**新增**） | `validate_sec_code(value) -> (ok, normalized, reason)`：裸 6 位 或 `6位.(SH|SZ|BJ)` 归一；纯函数零依赖 |
| `quantstudio/pipeline/sources/mcp_adapter.py` | `_inject_adjfactor`/`_sync_factor_snapshot` 入口契约拦截：reject + 隔离计数 + `factor_rejected` 审计行（**前移到冷启动触发之前**，消灭 TEST999 冷启动） |
| `quantstudio/pipeline/daemons 写路径`（landing→入库、`writers.py` passthrough） | 白名单表 code 列过滤：非契约行 reject+计数，**不阻断整表** |
| `quantstudio/pipeline/qfq_resident_orchestrator.py` | `_discover` per-code try/except：skip+计数+WARNING（脏码不再崩轮；与拦截双保险） |
| `scripts/cleanup_test_codes.py`（新增） | 存量清理（T5，dry-run 默认） |
| `tests/test_code_contract.py`（新增）等 | 行为测试 |

## 三、任务拆解

- **T1 契约模块**：`code_contract.py` 纯函数 + 单测（合法 SH/SZ/BJ/指数/基金 6 位全通过；
  8 处实测脏值全拒绝——**用实测样本做测试向量**）。
- **T2 aux 注入 + 冷启动入口拦截**（最高优先，直接解除错误一阻塞）：注入前校验；
  冷启动 code 集预过滤；拒绝=计数+审计行+原文进隔离（不静默）。
- **T3 passthrough 白名单表过滤**：先出「表×列 适用域」清单（读 `sources_config` 表配置 +
  各表实际列语义抽验，**含 sentiment 表定性查证**），enforce 前跑一轮 **audit-only**
  （只计数不拦截）比对：非契约行数 == 已知脏行数 ⇒ 再 enforce。
- **T4 调用方 fail-safe**：`_discover`/批处理处 ValueError→skip+计数（单脏码不再中断整轮）。
- **T5 存量清理**：备份（aux 文件 VACUUM INTO / 主库读窗备份）→ 精确 DELETE（WHERE 命中
  T1 拒收谓词，逐表逐值列出后执行）→ 复验零命中；**sentiment 特判**（T3 前置查证结论出来前
  不动该表）；主库锁 ⇒ 清理排在读窗。
- **T6 上游通知**：MCP 侧测试记录移除请求（外部依赖，登记 `BLOCKED(外部)` 不挂账本地修复）。

## 四、风险描述

| # | 风险 | 规避 |
|---|---|---|
| R1 | 误杀合法 code（新上市/特殊板块） | 契约=**形式**校验（位数+后缀），无关键词猜测；audit-only 先行一轮 |
| R2 | sentiment 等表列语义误判 | T3 前置定性查证；不确定表**不入 enforce 白名单** |
| R3 | 清理破坏引用完整性 | 备份 + dry-run + 逐值 WHERE + 前后行数账目（删的行数=审计计数，逐位对上） |
| R4 | 隔离审计热路径开销 | 每行一次纯函数比较，纳秒级；计数不逐行落盘（批量 flush） |
| R5 | enforce 与错误一 T4 时序纠缠 | 本案 T2/T4 先行落地即可解锁 orch（不等 T5 清理完成——拦截防增量，清理消存量，可并行） |

## 五、验收要点

1. **拦截完备**：8 处实测脏值样本重放（landing→aux→passthrough 三层）全部 reject 计数，
   合法 code 集（含 85 台阶码/北交所/指数对照样本）**零误杀**；
2. **收敛环实证解锁**：T2+T4 后 `qfq_orch` 连续 ≥3 周期无崩溃（与错误一 T4 联动验收）；
3. **冷启动不误触**：构造含 TEST999 的分片 → 线 1 冷启动**不触发**；
4. **清理账目**：逐表 `LIKE` 复验 0 命中；删除行数=事前审计行数（逐位一致，多删/少删即回退）；
5. **回归**：pipeline 相关测试套全绿；passthrough 合法行前后**逐位不变**（纯增益证明）；
6. **矩阵哈希**：不触 wrapper 模板，预期不变——实施时 `--check` 实测。

## 六、质量判据

- **纯增益**：被拦行在下游本就会抛/被丢（现状是崩或脏入库），拦截后合法数据路径
  字节级不变（行数账目+黄金对照）；
- 回滚：enforce→audit-only→off 三态配置开关，秒级回退；清理有备份；
- 提交纪律：写前快照、精确清单、三方核对、共享层同步门强制；六步②审计通过后实施。

# T2/T5 验收证据件 — writers-export-three-requests 方案件③实施

- 日期：2026-10-04
- 方案件：`docs/writers-export-three-requests-design.md`（②审计 PASS）
- 基线 commit：`a066dab3729a27c5553de4493bd1617da8bb5154`
- 回退点：`stash@{0}` = `7f0135f40ef35d11dbcb803c6610a428206febc0`（T2T5-preimpl-rollback-20261004）
- 本批范围：仅 **T2**（跳过一致行）+ **T5**（CLI 文档）；T1/T3 未实施（节后）

---

## 一、改动面（方案件 §四 精确清单子集）

`git diff --stat`（仅本任务文件）：

```
 README.md                       |  6 ++++++
 docs/customer-user-guide.md     |  5 +++++
 quantstudio/pipeline/writers.py | 47 +++++++++++++++++++++++++++++++++++++----
 3 files changed, 54 insertions(+), 4 deletions(-)
```

新增（untracked）：

- `tests/test_writer_upsert_skip_identical.py` —— T2 验收测试件（§四 第 6 项）
- `docs/evidence/writers-t2-t5-acceptance-20261004.md` —— 本证据件（§四 第 8 项）
- `docs/evidence/probe-t2-p5-art-throughput-20261004.py` —— T2 P5 ART 观测探针
- `docs/evidence/probe-t2-committed-gate-off-20261004.py` —— T2 提交态 gate 默认关探针

**未含**：§四 第 2/3/4/7 项（T1 的 writers.py 断点 DDL、daemon.py 接入、tests/test_batch_checkpoint.py）与 T3 的 qfq_invariant.py 探针 —— 均属节后批次，本批未触碰。

---

## 二、T2 验收

### 2.1 新测试件（P1-P4 + 开关）

命令：`python -m pytest tests/test_writer_upsert_skip_identical.py -q`

结果：**20 passed**（P1-P3+开关 19 + P4 全表覆盖 1；16.95s）

覆盖：P1 影子双跑逐位等价；P2 幂等重放（changed=0）；P3 NULL 矩阵 / NaN 极值 / ±0.0 登记边界；OFF SQL 形态；fail-closed 开关解析（参数化 12 例）；P4 全表覆盖（24 张 upsert 表跑 P1）。

### 2.2 相关回归（无新增红项）

命令：`python -m pytest tests/test_pipeline_guardrails.py tests/test_3a_equivalence.py tests/test_writer_channel_contract.py -q`

结果：**26 passed in 5.26s**

（`test_pipeline_guardrails` 含 `WriteResult(100,60,40)` 的 int/.new/.updated 兼容断言，验证新增 `changed` 字段不破坏既有契约。）

### 2.3 关闭态逐字节等同现状（核心硬约束）

探针（临时内联，未落盘）：默认关（`QS_UPSERT_SKIP_IDENTICAL` 未设置）时捕获**实际执行**的 upsert SQL，与「HEAD 版写法」基线逐字节比对：

```
SWITCH_DEFAULT_OFF: True
WriteResult_int: 1  changed: 0  new: 0  updated: 1
BYTE_EQUAL(OFF==BASELINE): True
SHA256_BASELINE: a6d7034e55fa543e0a973343e7ea780fc86673a46d50720421962c4988462fcf
SHA256_OFF_SQL : a6d7034e55fa543e0a973343e7ea780fc86673a46d50720421962c4988462fcf
```

基线 SQL（结尾无 WHERE）：

```
INSERT INTO stock_daily (code, time, close, volume, amount, preClose, pctChg) SELECT * FROM _tmp_write ON CONFLICT (code, time) DO UPDATE SET code=EXCLUDED.code, time=EXCLUDED.time, close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount, preClose=EXCLUDED.preClose, pctChg=EXCLUDED.pctChg
```

OFF SQL 与基线完全相同（未追加 WHERE）。

**结论**：开关关闭 ⇒ 生成的 upsert SQL 与现状逐字节相同；`WriteResult` 整数值与 `.new/.updated` 口径冻结；`changed` 默认 0 且为新增可选字段；`__repr__` 未改。

### 2.4 开关开启（T2 生效态）

开启态 SQL = 关闭态基线 + `" WHERE (cols) IS DISTINCT FROM (EXCLUDED.cols)"`（`test_off_sql_is_baseline_and_on_appends_where` 以 `startswith(基线)` 断言）。一致行不进 delete 相位；`RETURNING 1` 统计 changed = 实际变化（已存在且值不同）行数，新增行不计。

### 2.5 已知边界登记（R1）

±0.0：DuckDB 的 `IS DISTINCT FROM` 视 `-0.0` 与 `0.0` 为**不 distinct** ⇒ 开启态跳过该行、保留现有符号零，与关闭态覆写存在**符号零**位级差异（数值相等）。已由 `test_p3_negzero_documented_boundary` 显式断言并登记；行情价/量不出现 -0.0，不影响真实表逐位等价。此为方案件 §三 T2-② 假设（「若判为 distinct」）方向的实测修正，如实登记不假装等价。

---

## 三、T5 验收（CLI 手册勘误）

改动：

- `README.md`：在「数据采集」CLI 段后补记 `--config-dir` 默认值与生产 profile 口径；
- `docs/customer-user-guide.md` §3.1 后补「技术附注（命令行启动 daemon 时）」。

代码锚点核实（`daemon.py`）：

- `daemon.py:3632` `parser.add_argument("--config-dir", default=str(ROOT / "config"), ...)` ⇒ 默认 = profile **父目录**，非生产 profile；
- 生产 profile = `config/profiles/mcp_only/`（含 `data_config.json` / `collector_tasks.json` / `sources_config.json` / `alignment_rules.json`）；
- 失败模式实证（`config/` 下无 `data_config.json`、无 `collector_tasks.json`）：
  - **forever 模式**（README 示例）：`DaemonLifecycle._read_tasks_cfg` 读 `config/collector_tasks.json` 失败 → 空任务集（`daemon_lifecycle.py:524-531`）；
  - **once 模式**：目标库预校验读 `config/data_config.json` 缺失 → exit 2（`daemon.py:3684-3702`）。

⇒ 文档措辞「可触发 `--allow-non-main-target` 拒绝，或得到空任务集」与方案件 §一 附带 B「缺陷」行（经②审计 PASS）一致，未偏离 §四 文本。

---

## 四、文件哈希（SHA256）

| 文件 | SHA256 |
|---|---|
| `quantstudio/pipeline/writers.py` | `C98D613E00120C58055FC2563FB29C1651F89741C14C7A153907F3B5F794EB07` |
| `README.md` | `3672EF370C0AA958EA8C36A1237AF4C1C6030B4BDE95130BF1AB74EC6E7391B5` |
| `docs/customer-user-guide.md` | `21E61F8FAD82E90429EF726FAA9ACB74837C63AF9C0175E8AF776799AA37A1BC` |
| `tests/test_writer_upsert_skip_identical.py` | `D9308472BB9A8E95912EFC9D43855D0C1F3BA123AD070CA8CA5944302A1C0F1A` |

---

## 五、回退

- 精确路径回退（推荐，仅回本批文件）：`git checkout stash@{0} -- quantstudio/pipeline/writers.py README.md docs/customer-user-guide.md`（再删 `tests/test_writer_upsert_skip_identical.py`）。
- 整件回退：T2/T5 独立 commit 后按 §七工程标准 `git revert <commit>` 单件回退。

---

## 六、未决

- **T1**（批级断点：writers.py DDL + daemon.py 接入 + `tests/test_batch_checkpoint.py`）：未实施，须 fail-closed 断点铁律。
- **T3**（`qfq_invariant.py` 五段 RSS 探针 + 归因报告；归因报告须引 C 件 strftime 慢路径证据 `ISSUE-044/045 + §四〇三`，分「C 件前基线 vs C 件后现态」两段）：未实施。
- **⑤呈批 / ⑥推送+同步门**：待用户显式批准。

---

## 七、P4 全表覆盖（P1 影子双跑 × 全部 24 张 upsert 表）

- 命令：`python -m pytest tests/test_writer_upsert_skip_identical.py -q -k p4`
- 结果：**1 passed**（13.75s）—— 24/24 张 upsert 表逐位等价全通过。
- 覆盖表（与 `writers.py` upsert 主键表同源）：stock_daily / stock_minutes / etf_minutes /
  tick / fin_indicator / index_daily / stock_daily_valuation / etf_daily / etf_basic /
  stock_basic / trade_calendar / stock_float_share / index_constituents /
  index_constituents_snapshot_meta / balance_statement / income_statement /
  cashflow_statement / stock_dividend / etf_dividend / sw_industry /
  industry_classification / industry_membership / stock_namechange / stock_delist。
- 方法：每表构造「一致行 + 变更行 + 新增行」批；关闭态与开启态各写一份影子库；
  `SELECT * ORDER BY <pk>` 规范化后逐列逐位（含类型标记）比对，要求完全一致。

## 八、P5 ART / index-delete 观测（三档批大小）

- 探针：`docs/evidence/probe-t2-p5-art-throughput-20261004.py`（仅临时库，不触生产）
- 命令：`python docs/evidence/probe-t2-p5-art-throughput-20261004.py 50000 500000 3500000`

| 批规模（行） | 关闭态 行/h | 开启态 行/h | 倍率 | index-delete 报错 |
|---|---|---|---|---|
| 50,000 | 130,599,395 | 1,009,319,382 | 7.7× | 无 |
| 500,000 | 126,764,695 | 1,985,101,373 | 15.7× | 无 |
| 3,500,000 | 124,080,260 | 2,165,777,478 | 17.5× | 无 |

- 结论：三档均**未**出现 ART / index-delete 报错；开启态吞吐随批增大显著高于关闭态。
- 按方案件 §五 R3：**不**据此声称「消除 #23645 风险」——仅为观测登记。

## 九、提交态 gate 默认关实测（committed-state 探针）

- 探针：`docs/evidence/probe-t2-committed-gate-off-20261004.py`
- 运行环境：`git worktree`（T2 提交后的 HEAD 提交态，**非工作区**）
- 探针判据：①默认关 = True；②关闭态 SQL 无 `IS DISTINCT FROM`；③开启态 SQL = 关闭态基线 + `WHERE ... IS DISTINCT FROM ...`。
- 提交态结果：**见 T5 提交补录**（本文件随 T2 提交；T2 提交后以 worktree 实跑，结果补录于 T5 提交）。

## 十、探针脚本 SHA256

| 文件 | SHA256 |
|---|---|
| `docs/evidence/probe-t2-p5-art-throughput-20261004.py` | `EFFACA7770761AD3A65B7E26EBBE266228492FCA7CA7308B8DCE7906428B38DD` |
| `docs/evidence/probe-t2-committed-gate-off-20261004.py` | `7A45AA92F8070FDF2B8A16D471E3EB6F4D254DA032FE370EBABFBEEB01DAA738` |
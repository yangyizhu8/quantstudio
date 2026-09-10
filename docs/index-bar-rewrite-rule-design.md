# get_index_day_bar PTrade 重写规则方案（v3 · 复审修订版）

> 日期：2026-09-09 · 六步流水线步骤1 · 状态：**复审修订中（阻断项 1-4 本稿钉死，实施待 preclose 探针 v2 通过 + 本稿复审通过）**
> 前置证据：docs/evidence/d4-index-bar-probe-evidence.md（首轮 23 日）+ preclose 探针 v2（ptrade/probe_index_preclose_v2_ptrade.py，待平台执行）
> 修订历史：v1（2026-09-09 首稿）→ 复审意见四项阻断 → v3（本稿，全部钉死；v1 中 count+1 close 方案、缩水 fields、静默丢弃字段等旧内容**已否决废弃**，见 §八）

## 一、D4 探针实证（已确认范围——不得扩大解释）

**已实证可解锁的范围**：daily-bar-v1 + handle_data + 指数日线 close + include=True。
23 交易日（2026-07-01~07-31）平台实跑：
- get_history 支持 000001.SS 指数代码（close 3764~4112 指数点位，量级判定排除平安银行串码）；
- 日线 handle_data 内 include=True 23/23 日末根为当日；
- 平台合法字段集**不含 pctChg**（含 money/preclose/open/high/low/close/volume 等）；
- 平台 close 计算跌幅与本地 index_daily.pctChg 一致（07-16 = -1.849 / 07-17 = -3.046）；
- IDX3 为探针断言错误（RAW 数据足以证明 include 语义），不影响结论。

**未解锁（禁止扩大解释）**：分钟 profile、daily-open-close-proxy profile、盘前回调、任意 run_daily 时刻。

**preclose 数值探针（v2，待平台执行）**：v1 探针 P1（preclose 非空>0）/P2（preclose==前日close，4/4 精确）已 PASS；
P3 锚点对照（07-16/17）因窗口错位 MISSING——v2 修订为逐日累积（窗口 2026-07-15~17）。
**P3 全部 PASS 前，pctChg 实现不得写为已解锁 preclose 路径**；不通过则退 count+1 close 相邻计算，
且历史边界差异定性 approximation 重新履行确认（不得宣称完全同构）。

## 二、重写形式：DENY_SHIM 同构注入（策略源码零改动）

get_index_day_bar 从 LOCAL_ONLY_PASSTHROUGH_BLOCK **迁移**至 DENY_SHIM + SHIM_CONTRACT_REGISTRY：
- 转换时注入同名 shim（策略调用点零改动）；
- shim 委托既有 _QS_HISTORY_WRAPPER 能力（money→amount / pctChg 合成 / 三形态归一，2026-09-01 平台实证）——**禁止另写第二套字段翻译**。

## 三、shim 契约（完整 8 字段，与本地 API 逐项同构——审计阻断 1/3 钉死）

### 3.1 平台调用契约（产物内实际发出的请求）
```python
df = get_history(
    n,                                    # count-first（平台契约，security 不得放首位）
    frequency='1d',
    field=['open', 'high', 'low', 'close', 'volume', 'money', 'preclose'],
                                          # 平台合法字段（field 关键字单数；amount→money 由
                                          # wrapper 请求侧映射；preclose 为 pctChg 合成基列——
                                          # wrapper 注入，非本地对外字段）
    security_list=[security],
    fq='pre',
    include=True,                         # 仅受信任生成 shim 持有（哈希绑定例外，见 §五）
)
```
**产物验证钉死**：实际发给平台的 field 列表必须为
`['open','high','low','close','volume','money','preclose']`——**不得出现 pctChg/amount**
（本地契约字段，由 wrapper 请求侧转换 + 返回侧合成，shim 不得绕过 wrapper）。

### 3.2 本地契约同构（8 字段 + 异常 + 边界）
| 项 | 本地 API 行为 | shim 行为（必须逐项同构） |
|---|---|---|
| 返回形状 | DataFrame，行序时间升序 | 同 |
| index | trade_date（YYYY-MM-DD 字符串），index.name='trade_date' | 同——必须用既有 trade_date 列（wrapper 合成），禁止 RangeIndex 猜日期 |
| count 越界 | <1 或 >250 → ValueError | 同（消息含边界） |
| 非法 fields | ValueError（枚举外字段） | 同（消息含非法项与合法枚举） |
| fields=None | 完整 8 列：open/high/low/close/pctChg/volume/amount/trade_date | 同——canonical 列序 = ['open','high','low','close','pctChg','volume','amount']，preClose 合成中间列必须删除（不得泄漏到对外列） |
| fields=[] | 0 数据列 + 日期索引保留 | 同（空列表 ≠ 未指定） |
| fields 含 trade_date | trade_date 在 index 不在数据列（本地既有语义） | 同 |
| 数据不足 | 返回实际存在行（count=2 仅 1 行 → 返回 1 行） | 同 |
| 无数据 | 空 DataFrame | 同 |
| dtype | 数值列 float | 同 |

### 3.3 平台→本地字段映射（委托 wrapper，shim 零翻译）
- 平台 open/high/low/close/volume → 同名直取；
- 平台 money → 本地 amount（wrapper 既有映射）；
- 平台 preclose → preClose（wrapper 映射）→ 仅作 pctChg 合成基列，最终删除；
- pctChg → wrapper 合成：(close/preClose−1)×100（preclose 路径）或 close 相邻计算（fallback 路径，探针 v2 不通过时）；
- trade_date → wrapper 合成列 / 平台日期 index 规范化（优先既有 trade_date 列）。

## 四、转换层机器门禁（审计阻断 4：贯通真实入口链）

### 4.1 engine_profile 贯通（五层全链）
PyQt 转 PTrade tab：新增 profile 选择控件（daily-bar-v1 / minute-bar-v1 / daily-open-close-proxy-v1 / 未指定）
→ PtradeExportWorker(engine_profile=...)（QThread 参数）
→ orchestrate_source(..., engine_profile=...)（新增关键字参数）
→ convert_source(..., engine_profile=...)（新增关键字参数）
→ SourceConverter.__init__(engine_profile=...) → self._engine_profile
→ _scan_calls 后的门禁判定
CLI qs-compile import 同步暴露 --engine-profile 旗标。
profile 缺失 / 非法值 / 无法判定 → BLOCK，禁止默认 daily-bar-v1。

### 4.2 调用图可达性分析（新增前置收集，通用调用图追踪）
1. 收集函数定义名 → AST 节点映射；
2. 逐 FunctionDef 内扫描 ast.Call（Name 调用 + self.method 调用）→ 调用边 caller→callee；
3. run_daily 注册解析：run_daily(context, func, time='HH:MM') → (回调名, time 字面量)；time 字面量静态解析失败 → 该入口标记 UNPARSEABLE；
4. 入口集与判定：
   - initialize 可达 → BLOCK（初始化路径）
   - before_trading_start 可达 → BLOCK（盘前路径）
   - handle_data 可达 → 候选 SHIM（daily-bar-v1）
   - run_daily 回调可达：time 可解析且 >= '14:55'（收盘邻域，客户确认语义）→ 候选 SHIM；time < '14:55' 或 UNPARSEABLE → BLOCK
   - 同时被盘前类（initialize/before_trading_start/盘前 run_daily）与盘中类（handle_data/收盘 run_daily）可达 → BLOCK（双路径可达，审计钉死）
   - 不可达（死代码）→ SHIM（无运行时影响，防御性注入无害）
5. profile 判定优先级：minute-bar-v1 → BLOCK；daily-open-close-proxy-v1 → BLOCK（未探针）；daily-bar-v1 → 按 4；其他/缺失 → BLOCK。

### 4.3 判定矩阵（验收用例来源）
| engine_profile | 可达路径 | 判定 |
|---|---|---|
| daily-bar-v1 | 仅 handle_data（含 helper 链） | SHIM |
| daily-bar-v1 | 仅收盘 run_daily（time>=14:55） | SHIM |
| daily-bar-v1 | handle_data 与盘前双可达 | BLOCK |
| daily-bar-v1 | 仅 before_trading_start | BLOCK |
| daily-bar-v1 | 仅 initialize | BLOCK |
| daily-bar-v1 | run_daily(time='09:31') | BLOCK |
| daily-bar-v1 | run_daily time 无法解析 | BLOCK |
| minute-bar-v1 | 任意 | BLOCK |
| proxy-v1 | 任意 | BLOCK |
| 缺失/非法 | 任意 | BLOCK |

## 五、生成 shim 与静态校验交互（先做最小复现，实施前置）
1. DATALOAD-NO-INCLUDE-TRUE 例外：注入 shim 内 include=True 不得触发普通源码规则——仅 _QS_INDEX_DAY_BAR_EXT 模板（哈希绑定）享有受信任例外；普通策略源码 include=True 仍 BLOCK；实施时最小复现验证；
2. TARGET-LOCAL-EXTENSION-BAN 不重复拦截已注册注入 shim（产物含 def get_index_day_bar 为受信任注入）；
3. 单次平台调用断言：shim 内部仅一次平台 get_history 调用（无 wrapper 重入/重复 pctChg 合成）；
4. 三返回形状覆盖：平台 DataFrame / structured array / dict——委托 wrapper 归一后同构测试；
5. SHIM_CONTRACT_REGISTRY 与注入模板/转换报告/最终校验逐一一致。

## 六、验收标准（复审追加 11 项全纳入）
1. 平台 preclose P1/P2/P3 探针通过（v2，待执行）；
2. shim 平台调用契约正确（field 列表逐字断言，count-first，security_list，fq='pre'，include=True）；
3. 本地 API 与 shim 参数化同构测试：fields=None / 空列表 / 单字段 / 混合字段 / 仅 trade_date / 非法字段 / count 边界；
4. 平台 DataFrame / structured array / dict 三返回形状覆盖；
5. 断言内部仅一次平台原生 get_history 调用 + preClose 不泄漏到对外列；
6. 机器门禁判定矩阵全用例（§4.3 表）；
7. 6 canonical 策略重转产物 SHA-256 逐位一致（不含 get_index_day_bar 的策略产物字节级不变）；
8. 恐慌抄底 PyQt 转 PTrade → 转换成功（shim 注入）+ 完整 PTrade portability/agent validator PASS；
9. 状态 = IMPLEMENTED_AWAITING_PLATFORM_VALIDATION（不得提前关单）；
10. 用户平台复测：07-17 信号、入场、20 日锁仓、清仓、成交/持仓审计与本地一致后，D4 才闭环；
11. 文档同步：README + strategy_toolbox + prompt_engineering + 本设计 §3.3 状态；trading 副本跟随。

## 七、矩阵纪律
新增 _QS_INDEX_DAY_BAR_EXT 不属于 check_fund_matrix.py 哈希范围（仅 _QS_FUNDAMENTALS_EXT + _QS_INDUSTRY_EXT）→ 执行 --check PASS 即可，无需 --reverify；若同批改动上述两模板则必须同 commit reverify。

## 八、已否决历史方案（存档，防复活）
- count+1 close 相邻计算取 pctChg：preclose 探针 P1/P2 通过后优先 preclose 路径（无损首行）；仅当 preclose 探针 FAIL 时退回此路径，且历史边界差异须定性 approximation 重新确认；
- 缩水 shim（仅 close/pctChg/trade_date）：违反双端契约一致，已废弃；
- fields 缺失静默丢弃：违反本地 ValueError 契约，已废弃；
- engine_profile 缺失默认 daily-bar-v1：违反 fail-closed，已废弃。

## 九、边界（不变）
本地 QuantStudio 行为零改动（本地 API 原样）；策略源码零改动；不改引擎/撮合/数据层；重写 shim 语义 = 日线 close 回测（分钟/proxy 未探针，不支持）。

## 十、回退条件
回退 = 撤销 shim 模板/注册/DENY_SHIM 迁移/机器门禁/engine_profile 链/单测（全部加法）；LOCAL_ONLY_PASSTHROUGH_BLOCK 恢复含 get_index_day_bar（回到 BLOCK 门禁）；共享核心纪律执行。

## 十一、流程声明
本方案 v3（复审修订版）→ 复审通过 + preclose 探针 v2 通过 → 实施（步骤3）→ 验收（步骤4，§六全项）→ 用户确认（步骤5）→ 双仓库推送 + trading 跟随（步骤6）。
# B2 · POS 族探针 交易端操作说明（2026-10-19 启动）

> **件（v2）**：`docs/probe/qmt_shared_surface_pos_probe_v2-20261019.py`（94,094 B｜GBK 物理｜无 BOM｜首行 `#coding:gbk`｜compile OK｜**`after_init` ＋ `handlebar` 双钩子**）
> *（v1：`qmt_shared_surface_pos_probe-20261017.py` 全逻辑在 handlebar ⇒ 非交易时间不可靠，已由 v2 取代）*
> **目标格**：**POS-01..17**（共享面「QMT 锚点」列回填）
> **性质**：**只读探针**——不下单、不写文件、不改框架
> **语境**：**必须 QMT 交易端（模拟盘）**，**不是回测**（回测语境 `account` 为空 ⇒ 查询返回 0 条，属语境不适用而非缺陷）

## 一、前置条件（缺一不跑）

| # | 前置 | 校验方式 | 不满足的后果 |
| --- | --- | --- | --- |
| 1 | **QMT 交易端已登录模拟盘账户**（**非交易时间亦可**——v2 走 `after_init`，不依赖 bar） | 终端能正常显示资金/持仓 | 回测语境 ⇒ 全部 `n=0`，白跑 |
| 2 | **模拟盘账户至少有 1 个持仓** | 持仓页有非零记录 | `POSITION n=0` ⇒ **字段面不可得**（只能证语境可用，不能回填 POS-01..17）|
| 3 | 时点避开盘中 | — | 探针只读，但避免与当日委托/成交记录混淆 |

> **第 2 条是从 wrapper 实现推出的硬前置**：探针靠 dump「首条记录的属性全集」来判定字段是否存在；无记录 ⇒ 无属性可 dump。

## 二、操作步骤

```
1. 打开 QMT 终端 → 登录【模拟盘】账户
2. 确认持仓页 ≥1 条持仓（无持仓：先手动买 100 股任一标的，或用已有持仓的模拟账户）
3. 记事本打开 docs/probe/qmt_shared_surface_pos_probe-20261017.py → Ctrl+A → Ctrl+C
4. QMT 交易端：新建「Python 策略」（交易端策略，非回测）→ 粘贴全文（保留首行 #coding:gbk）
5. 挂载到该模拟账户 → 启动 → **`after_init` 立即执行探针**（非交易时间同样触发，见 §七）；几秒即出读数
6. 回抄日志中全部 [POS_PROBE] 行 → 停止策略
```

## 三、回抄什么（判据面）

| 标签 | 含义 | 判定用途 |
| --- | --- | --- |
| `C1 account=` | 解析到的账户号（`C.accountID` 或全局 `account`） | **语境判据**：非空 ⇒ 交易端语境成立 |
| `C1 biz=<POSITION/ACCOUNT/ORDER/DEAL> n=` | 各类记录条数 | `POSITION n>0` ⇒ 可回填字段面 |
| `C2 biz=… attrs=[…]` | **首条记录的属性全集** | **POS-01..17 逐格判存在性**（哪些字段平台真的提供） |
| `C3 biz=… <属性>=<值>` | 首条记录逐属性取值 | **语义对照**（持仓量/可用量/成本价 等与本地契约口径） |
| `C4 wrapper_path n=` | 生产路径 `_qs_get_trade_detail(C,'POSITION')` 条数 | **生产口径一致性**（应与 C1 POSITION 同数） |

## 四、读数分支与处置

| 读数形态 | 判定 | 处置 |
| --- | --- | --- |
| `C1 account=''` 且各类 `n=0` | **回测语境（语境不适用）** | 不判缺陷；按 §二 重跑到交易端 |
| `account` 非空、`POSITION n=0`、其它也为 0 | **语境成立但账户空仓** | 补持仓后重跑（仍不判缺陷） |
| `account` 非空、`POSITION n>0` | **可回填** | 按 `C2 attrs` 逐格判 POS-01..17；`C3` 取值做语义对照 |
| `C4 wrapper_path n` ≠ `C1 POSITION n` | **生产路径与裸查询不一致** | **登记 ISSUE**（可能 wrapper 过滤/映射差异），不自行改码 |

## 五、时点协调建议

| 窗口 | 建议度 | 理由 |
| --- | --- | --- |
| **今日盘后 15:30 之后**（T 日） | ★★★ 首选 | 模拟盘在线、持仓与成交均已落定，读数稳定 |
| **明日盘前 09:00–09:15**（T+1） | ★★ | 同上；但若隔夜有委托记录会混入 ORDER/DEAL 面 |
| 盘中 | ✗ 避免 | 委托/成交在变，读数不稳定 |

## 七、非交易时间可否运行（文档依据，2026-10-19 查证）

| 机制 | 文档依据（Context7 `/websites/dict_thinktrader_net`） | 结论 |
| --- | --- | --- |
| **`after_init`** | `innerApi/system_function.html`：在 init 执行完、handlebar 之前**调用一次**；「可用于一次性下单或**取数**操作；init 不支持的函数（如 `get_trading_dates`）可在 after_init 调用」 | ★★★ **v2 采用**——策略启动即执行，**与交易时段无关** |
| `run_time(name, period, start)` | 同上：`ContextInfo.run_time("f","5nSecond","2019-10-14 13:20:00")` 定时任务，自指定时刻起按间隔运行函数 | ★★ 备选（需自行起定时器） |
| `handlebar`（非交易时段） | `innerApi/question_answer.html`：非交易时段 handlebar 亦可能被调用（行情服务重启后重推**最新 bar** 触发），**仅最新 bar、时序不可控** | ✗ 不可依赖；v2 仅作补跑 |

> **实践建议**：非交易时间直接跑 v2 即可（`after_init` 命中）；若 `after_init` 阶段 `account` 尚未就绪（`C1 account=''` 且全 `n=0`），则**留待交易时段**跑一次（`handlebar` 会自动补跑），或改用 `run_time` 定时器版。

## 八、C1 专项（Position 键形态跨端差异 · 一次读数闭）

**件**：`docs/probe/qmt_shared_surface_pos_probe_v3-20261020.py`（95,503 B｜GBK｜无 BOM｜`#coding:gbk`｜compile OK）

| 项 | 内容 |
| --- | --- |
| A1 | wrapper 持仓字典键样本（`get_positions()` 的键 ＋ `sid`/`code`/`amount` 抽样） |
| A2 | QMT universe 键样本（`get_Ashares()`；若不存在则试 `get_all_securities()`） |
| A3 | `get_position(code)` **双形态命中对比**：实际键 / `.SS` / `.SZ` / 裸码 四种写法各是否 HIT |
| A4 | **自动判定**（探针自带 VERDICT 行） |

**操作**：QMT 交易端（模拟盘，账户有持仓）→ 粘贴 v3 全文 → 启动 → 回抄全部 `[C1_PROBE]` 行
（含 `A4 VERDICT=` 行）。`after_init` 即出读数，非交易时间同样可跑。

**判据**：
- `A4 VERDICT=端内自洽` ⇒ 持仓键与 universe 键后缀一致（均 `.SH/.SZ`）⇒ **C1 结案**，差异仅作跨端口径记录；
- `A4 VERDICT=真缺陷` ⇒ 两者后缀不一致 ⇒ 消费方按 universe 码取持仓**必 miss** ⇒ **走六步流水线修 wrapper 后缀映射**（`SH→SS`）；
- `A4 VERDICT=读数不足` ⇒ 账户无持仓或 universe 读不到 ⇒ 补持仓/补环境后重跑（**不判缺陷**）。

## 六、纪律

1. **编码**：文件已是 GBK＋`#coding:gbk`，**勿用编辑器另存为 UTF-8**。
2. **只读**：探针不下单、不撤单、不写文件；如策略被挂到实盘账户，**立即停止并切换到模拟盘**。
3. **回抄完整**：`C2 attrs` 与 `C3` 全行必须原样回抄（截断会导致字段面判不全）。
4. 读数到达后我侧动作：逐项对表 ⇒ 能锚定的 POS 格立即升 registry（附证据指针）；不能锚定的留「未验证」或记「已排查（缺口：…）」——**不以探针未覆盖冒充已排查**。

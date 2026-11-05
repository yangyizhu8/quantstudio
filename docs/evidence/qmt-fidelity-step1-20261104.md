# QMT 保真测试 Step 1 · 本地基线与管道自检（2026-11-04）

> 授权：用户 2026-11-04 20:11 指令「现在就起 Step 1」（默认参数照准：CANSLIM × 首选窗口 × minute-bar-v1）。
> 定位：L2 数值级保真对照的**本地侧准备**——不产生任何保真结论（保真判据须真机 QMT 日志，Step 2/3）。
> 关联：`docs/evidence/qmt-conv-correctness-baseline-20261103.md`（L1 转换正确性，`5cd6327`）｜
> `scripts/qmt_align_check.py`（对照器）｜`tests/qmt_stub.py` L7（桩边界声明）。
> 知识反哺：`知识反哺：[命中] QD-2026-0017（qd/D1，2026-11-03）`——交付完整性族（本件新发现的
> 「订单审计行缺失」属同族第三例：设计前提与实现间的缺口）；`知识反哺：[无命中] 订单审计行族
> （主库 knowledge/qd 无 wrapper 订单审计条目），2026-11-04`。

---

## 一、执行序与实测

| 序 | 动作 | 实测 | 结论 |
|---|---|---|---|
| 0 | **预检三核** | ①CANSLIM 源 grep `get_industry_stocks\|get_stock_name\|get_stock_blocks\|行业\|板块`＝**零命中**（P2 未修项 G/L/M 不产假差异）②runner＝`quantstudio/backtest/run_ptrade_strategy.py`（黄金件同款）③daemon 未运行（占用窗声明：本测全程隔离副本＋独立锁目录） | ✅ |
| 1 | 短窗基线回测 `2026-01-05..2026-01-16` | rc=0，150.1s，目录 `20261104_201538_CANSLIM突破成长选股策略`；**trades.csv 仅 5 笔且全在首日 2026-01-05**（月收益 +2.34%） | ⚠️ 样本量不足（<8，铁律 25 精神） |
| 2 | **改选长窗** `2026-06-01..2026-09-30` | 依据：CANSLIM 历史同策略窗口（`20261015_011021` 目录）86 交易日/29 笔；库内日线覆盖 `2018-01-01..2026-10-29` ✓；**须当前 HEAD 引擎重跑**（历史结果非同代引擎） | 后台执行中 |
| 3 | CSV→JSON 导出＋code 归一 | 5 条全过；字段映射 `datetime→date / action→side / volume→qty / commission→fee`；**code 归一＝`split('.')[0]`**（仓内先例：`source_import_qmt.py:823`） | ✅ |
| 4 | **合成日志管道自检** | 3 行合成（冻结词法 `[QS-QMT log.info] … date=… code=… side=… qty=… price=… op=…`）→ 对照器全链路：五维判定＋归因三分＋退出码 | ✅ 见 §三 |

## 二、⚠️ 关键发现 1：产物缺订单/成交审计行（阻断 Step 2/3 的判据面）

**盘点实测**（`output/qmt_import/CANSLIM突破成长选股策略.py`，GBK 全文正则）：

```
审计/诊断打印点总数 = 18
  11 [QS-QMT log.info]（QSFUNDHIST / HISTFOLD / HISTDBG3 等取数面）
   2 [QS_QMT_CODE_FORM_MISS]   各 1 [QS-QMT log.warn|warning|error|critical|debug]
订单类打印点 = 1 —— 且该 1 处是 QSFUNDHIST（财务历史取数审计），非订单事件
```

**下单链无审计打印**：`_qs_order`（`source_import_qmt.py:856`）→ `_qs_passorder`（`:850`）→ `passorder`（`:852-853`）——**全程零 print**。

**后果**：对照器（`qmt_align_check.py` L37-47 词法：date/code/side/qty/price，op=23/24 语义）的**订单侧输入在真机日志中无产出源** ⇒ Step 2 用户日志将只含数据面事件，五维对照的订单维/调仓日维/持仓维无米下锅。

**定性**：框架层缺口（M5.2 件2 设计前提「QMT 端策略运行日志中的审计事件行」含订单事件，实现只覆盖了数据面）——**不属本会话授权修复范围**（wrapper 模板串＝共享核心文件，铁律 10；改产物模板 ⇒ 六策略 SHA16 漂移 ⇒ 触发 `5cd6327` 基线的漂移判据）。

**修复方案草案（候②审，不擅动）**：
1. 落点：`source_import_qmt.py` 的 `_QS_QMT_ORDER_EXT` 模板串内，`_qs_passorder` 调用点前加一行审计 print：
   `[QS-QMT log.info] QS_QMT_ORDER op=%d code=%s qty=%d price=%.4f prType=%d`（date 取 `C.barpos` 对应交易日或调用方传入）；
2. 词法对齐对照器冻结基线（date=/code=/side 或 op=/qty=/price= key=value 形式）；
3. 验收门：①168 测试面全绿 ②六策略重转 SHA16 **预期漂移**（模板变更 ⇒ 新基线锚入册，旧六值作废并留痕）③`tests/test_qmt_align_check.py` 增订单行解析用例；
4. 判型：新增检测型（审计行新增＝开始报告真相；不改撮合/数据行为）。

## 三、管道自检实测（合成日志，非保真判据）

合成 3 行（①与本地首笔完全一致 ②qty−100 ③多余调仓日 2026-01-07 sell）：

```
维度 rebalance_dates status=diff in_tol=0 out_tol=1 na=0   ← 多余日被抓 ✓
维度 order_side_qty  status=diff in_tol=0 out_tol=7 na=0   ← qty 差被抓 ✓
维度 price           status=pass in_tol=1 out_tol=0 na=0   ← 一致行判 in_tol ✓
维度 position_qty    status=diff in_tol=0 out_tol=6 na=0
维度 fees            status=pass in_tol=0 out_tol=0 na=1   ← 无 fee 字段→na（不作坏行，与冻结词法一致）
归因: data_source=13 timing=1 fees=0 other=0
结论: PASS——一致或差异可归因（退出码 0）                    ← other=0 ⇒ exit 0 闸正常
```

⇒ 解析→归一→五维→归因→退出码**全链路可用**；差异注入均被捕获；`other≠0→exit 1` 的 fail-closed 闸待真数据验证。

## 四、qty 单位口径（已知风险，首轮对照预期内）

- 本地 `trades.csv` volume＝**股**（样例 1900/200/500/2000 均股数）；
- QMT 侧 `passorder` volume 单位＝**M5 实测项未冻结**（`orderType 1101 单股单账号股/手方式`，枚举语义 05-枚举常量.md:135-143 未摘录；`source_import_qmt.py:840-846` 原注释「【M5 实测项】」）；
- 另 QMT 行情面 `volume=手`（D-16a 修复注释，`:528`，04-数据结构.md:24）——**行情手/订单股两套口径并存**；
- ⇒ 首轮真机对照若 qty 维现系统性 ×100/÷100 ⇒ **归因单位口径（data_source 类）**，属预期发现而非事故；判据轮前不预设、不脑补（禁令②）。

## 五、产出清单（全部 `output/qmt-fidelity-20261104/`，gitignored）

| 件 | 内容 |
|---|---|
| `baseline_run.log` / `baseline_run_long.log` | 短/长窗回测日志 |
| `local_baseline_short.json` | 短窗基线（5 笔，管道自检用） |
| `synth_qmt_log.txt` / `synth_align_out/` | 合成日志与对照器输出（`qmt_events.jsonl`＋`align_report.json`） |
| `local_baseline_canslim_20260601_20260930.json`＋`baseline_meta.json` | **长窗正式基线**（候长窗回测完成补登） |

## 六、Step 2/3 前置状态（修正后）

| 步 | 前置 | 状态 |
|---|---|---|
| Step 2（用户真机跑产物） | 订单审计行补齐并重出产物（§二方案获批后） | ⏸ **暂缓判据轮**；可先跑一轮**冒烟**（验产物可驱动＋数据面审计行格式），不计判据 |
| Step 3（对照报告） | Step 2 日志（含订单事件）＋长窗本地基线 | 管道已就绪（§三） |

## 七、变更日志

| 时间 | 内容 |
|---|---|
| 2026-11-04 20:1x-21:0x | v1.0：预检三核/短窗回测/导出归一/合成管道自检；**发现阻断性缺口（订单审计行缺失）**并给修复方案草案；qty 单位口径风险登记 |
| 2026-11-04 21:1x | v1.1 长窗收口：2359s·29 笔（与历史同窗一致）·22 调仓日；基线 JSON sha16 \932eee70d9e40610\；对照器基线侧终验 \local_trades_count=29\·price 维 in_tol=1（一致行精确命中）⇒ **本地侧与对照器双向兼容** |

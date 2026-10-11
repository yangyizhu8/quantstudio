# M5.1a 修复批设计方案：D-10（current_dt=None）+ D-3（get_instrument_detail 签名）

- 日期：2026-10-10
- 状态：①方案（待②审计→③实施→④验收→⑤用户确认→⑥推送）
- 触发：M5 批2 六策略回测首跑实测（用户域，新建策略文件7 = CANSLIM GBK 产物）
- 判型：**数据修正型 + 新增检测型混合**——修复按正确口径变化（D-10：current_dt 由 None→真实 datetime；D-3：由 TypeError 崩溃→正常返回），影响面严格限定于产物 wrapper 两个机制点，diff 只能源于本方案列明的修正行集。
- 上游证据：`docs/evidence/qmt-m5-batch2-probe-20261010.md`（M5-8/M5-13 实测）+ 用户域回测报错原文（21:26:52，见 §1）

## 1. 问题定义（实测证据链）

### D-10：context.current_dt 为 None → daily_logic 崩溃（P0·崩溃级）

用户域回测报错（原样）：

```
File "<string>", line 1133, in handlebar
File "<string>", line 1141, in daily_logic
AttributeError: 'NoneType' object has no attribute 'strftime'
```

产物行（`output/qmt_import/CANSLIM突破成长选股策略_qmt.py`）：

```python
1140    today = context.current_dt          # ← None
1141    today_str = today.strftime('%Y%m%d') # ← 崩溃点
```

根因（source_import_qmt.py:346，_QmtContextView）：

```python
@property
def current_dt(self):
    return getattr(self._qs_C, 'bar_date', None)  # 【M5 实测项】→ 实测落锤：不存在
```

**实测结论：ContextInfo 无 `bar_date` 属性，getattr 返回 None。** 同理 `previous_date`（:351 `pre_bar_date`）亦为 None（尚未触发，同一根因）。

### D-3：get_instrument_detail 三位置参数 TypeError（P0·崩溃级）

M5-8 探针实测（原样）：

```
TypeError('get_instrument_detail() takes 2 positional arguments but 3 were given',)
```

产物现状（3 处）：`C.get_instrument_detail(code, False)`（产物 :180/:544/:572 ↔ 源码 :401/:789/:817）。
**实测语义**：`takes 2 positional (self + code)` → `iscomplete` 不是该方法的位置参数（文档 07:2446/:2459 记载的签名与实测不符，**实测优先**）。

## 2. 修复设计（框架层·通用·零策略改动）

全部落在 `quantstudio/strategy_compiler/source_import_qmt.py` 的 wrapper 模板串内；策略源码零改动；通过重转生效（铁律「仅限框架层」）。

### 2.1 D-10：current_dt / previous_date 由 get_bar_timetag 推导

依据（M5-13 实测）：`C.get_bar_timetag(C.barpos)` → 毫秒 int（`1725292800000` = 2024-09-03 00:00 北京时间）。

```python
@property
def current_dt(self):
    # M5-8 批2 实测：C.bar_date 不存在（getattr→None→daily_logic strftime 崩溃 D-10）。
    # 改由 get_bar_timetag(barpos)（M5-13 实测毫秒 int）推导 bar 时点 datetime。
    import datetime as _qs_dtc
    try:
        ts = self._qs_C.get_bar_timetag(self._qs_C.barpos)
        return _qs_dtc.datetime.fromtimestamp(ts / 1000.0)
    except Exception:
        return None

@property
def previous_date(self):
    # M5 实测：pre_bar_date 不存在；用 barpos-1 的 timetag 推导（首 bar 无前值→None，
    # 与 PTrade 语义一致：首日 previous_date 不可用）。
    import datetime as _qs_dtp
    try:
        bp = self._qs_C.barpos
        if bp is None or bp <= 0:
            return None
        ts = self._qs_C.get_bar_timetag(bp - 1)
        return _qs_dtp.datetime.fromtimestamp(ts / 1000.0)
    except Exception:
        return None
```

设计要点：
- 函数内局部 `import datetime`——不依赖 EXT 块注入顺序（context view 块在产物前部，`import datetime as _qs_dt` 在后部，不能引用）；
- `fromtimestamp`（本地时区）：QMT 交易端统一北京时间环境，timetag 为交易所本地日时戳（M5-13 实测核验 2024-09-03 00:00）；`strftime('%Y%m%d')` 产物消费面正确；
- fail-closed 保留：取不到 timetag → except 内 print 一行 QS_QMT_CONTEXT_DT_UNAVAILABLE（对齐本文件「失败必留审计行」惯例，:521/:759/:869 同款）后返回 None（不发明语义），由消费侧既有 None 分支处理；p<=0 正常首 bar 分支不打印（非错误）；
- `previous_date` 首 bar（barpos=0）→ None，符合 PTrade 首日语义。

### 2.2 D-3：get_instrument_detail 收敛为单位置参数

3 处 `C.get_instrument_detail(code, False)` → `C.get_instrument_detail(code)`（源码 :401/:789/:817；含注释更新：标注 M5-8 实测签名）。

## 3. 影响面

| 项 | 影响 |
|---|---|
| 改动文件 | 仅 `quantstudio/strategy_compiler/source_import_qmt.py`（模板串 2 个机制点）+ `tests/test_source_import_qmt.py`（新增测试） |
| 产物变化 | 六策略重转后 wrapper 的 current_dt/previous_date/get_instrument_detail 行更新；其余零变化（diff 验收） |
| 矩阵哈希纪律 | 「矩阵哈希追认同批纪律」绑定 `source_import.py`（PTrade 线模板）；本批改 `source_import_qmt.py`（QMT 线）不在该矩阵范围；实施后跑一次 `scripts/check_fund_matrix.py --check` 确认零漂移（预期不触发） |
| 既有功能 | 纯增益：stub smoke（S1–S7）**确实触及** current_dt 路径（六产物中 5 个在策略主体消费，每 bar 触达）——今天绿是因为 stub 自造 ar_date 属性（qmt_stub.py:618，真实 QMT 无此属性，即 D-10 逃过桩冒烟的根因）；修复后仍绿的机理是 stub ms 戳与 dates 同源同日（日期级等价），预期全绿 |
| stub 保真度分叉（登记） | D-10/D-3 逃到真实 QMT 才暴露的根因：stub 按文档口径实现（bar_date 属性 / iscomplete 形参）与实测口径分叉。本批只改模板不动 stub → 分叉继续存在；**处置**：新增测试断言产物渲染严格单位置实参（堵 D-3 类回归）；stub 签名对齐实测另立小批（登记待办） |
| docs 同步判断 | README / docs/strategy_toolbox.md / docs/prompt_engineering.md 经查无涉及 current_dt/get_instrument_detail 表述，本批无需更新（判断行留痕） |
| 不修项 | D-1（count 形态未来数据）/ D-2（get_trade_detail_data 全局形态已正确，待实测定）/ D-9（fundamentals Panel）——D-1/D-9 属数据正确性级，立 M5.1b 批另走流程；D-2 产物已是全局函数形态，本批不动、由六策略继续实测确认 |

## 4. 验收标准（④）

1. 既有 18 测试全绿：`python -m pytest tests/test_source_import_qmt.py -q`
2. stub smoke 全绿：`python -m pytest tests/test_qmt_stub_smoke.py -q`
3. 新增测试 ≥3：①current_dt 从 timetag 推导正确（stub C 返回 1725292800000 → 2024-09-03；**前置校验机器时区为 UTC+8**，不符即 fail 并注明，防跨机漂移）；②previous_date 用 barpos-1 timetag、首 bar 返回 None；③产物渲染的 get_instrument_detail 为**严格单位置实参**（断言 len(args)==1，堵 stub 分叉下的 D-3 回归）
4. 六策略重转成功，产物 GBK+AST 校验通过；**全部六产物** diff 仅限本方案列明内容模式行集（current_dt/previous_date 两个 property 块 + 3 处 get_instrument_detail 调用行；行号随产物漂移，按内容模式归因）
5. `scripts/check_fund_matrix.py --check` 零漂移
6. 用户域复测：覆写新建策略文件7 → 回测越过 :1141（D-10 消除）且 data view 快照不再报 3 参 TypeError（D-3 消除）；后续报错（若有）作为 D-2/D-9 实测证据继续收集

## 5. 回退条件

- 任一既有测试转红 / stub smoke 转红 / 矩阵哈希漂移 → 回退
- 用户域复测引入新崩溃且归因于本修复 → 回退
- 回退手段：`output/backups/m5.1a-20261010/*.bak` 文件级覆盖；git 侧 stash 基线（2a59c7b 工作区态）+ 精确 `git checkout -- <file>`

## 6. 变更记录

| 版本 | 时间 | 内容 |
|---|---|---|
| v1 | 2026-10-10 | 初稿（D-10+D-3 崩溃级二合一；D-1/D-9 分流 M5.1b） |
| v1.1 | 2026-10-10 | 按 ZCode 审计修订：R1（stub 论据改写+保真度分叉登记+docs 判断行）、R2（previous_date 首 bar 断言改写）、R3（except 审计行并入设计）、R4（消费面全集+六产物 diff+单实参断言）、R5（时区前提+新测试时区校验）。审计结论「机制无阻断缺陷，修订后直接实施」（session=sess_a1c58d74，116 万 token 审计） |

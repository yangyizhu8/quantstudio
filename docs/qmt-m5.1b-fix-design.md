# M5.1b 修复批设计方案：D-1（count 形态未来函数·P0）+ D-9（fundamentals Panel 解析·P0）

- 日期：2026-10-10
- 状态：①方案（待②审计→③实施→④验收→⑤用户确认→⑥推送）
- 判型：**数据修正型**——修复后回测信号按正确时点口径计算，diff 只能源于本方案列明修正行集；准确度只升不降。
- 上游证据：`docs/evidence/qmt-m5-batch2-probe-20261010.md`（M5-2 实测 + §6/§7）+ D-11 探针四行（2026-10-10 23:02 用户域，本方案 §1.2）

## 1. 问题定义（实测证据链）

### 1.1 D-1（P0）：count 形态取全局最新 N 根 → 回测未来函数

- 探针实测：`get_market_data_ex(['close'], ['510300.SH'], period='1d', count=221, dividend_type='front')` → `first_idx='20251110' last_idx='20261009'`（当日 2026-10-10）——**以数据全集尾部为锚**，不以回测 barpos 为锚。
- 后果：回测 2026-01-05 的 bar 上，M 闸门/选股/止损消费的是 2025-11→2026-10 序列（含回测日之后约 9 个月未来数据）——**全部信号失真**（本次 183 天 selected=0 即其产物：用 2026 全年下行数据在 2026 年初判断熊市）。
- 现状落点：`source_import_qmt.py` `_qs_fetch_bars`（count 形态调用）。

### 1.2 D-9（P0）：多代码 get_financial_data 返回 pandas.Panel → 解析全 miss

- M5-1b 探针实测（17:18）：单代码 → `DataFrame(index=date_str, columns=字段名)`；多代码 → **`pandas.Panel`**（items=codes × major_axis=dates × minor_axis=fields）。
- 现状 `_qs_fin_to_rows`：`hasattr(raw,'to_dict')` → `to_dict('index')` 三层嵌套 → 外层键命中代码形态误判 code_major → 字段投影全 miss → NaN → 筛选全挂。
- D-11 撤销登记：`selected=0` 实测证伪为 M 闸门真实 False（D-1 口径产物），非取数形态缺陷；`_check_m_factor` 取数链复核正常。

## 2. 修复设计（框架层·通用·零策略改动）

### 2.1 D-1：_qs_fetch_bars 改 start_time/end_time 显式窗口（barpos 锚定）

```python
def _qs_fetch_bars(C, codes, fields, fq, count, period):
    # D-1 修复：count 形态 = 全局最新 N 根（探针 23:02 实锤 first=20251110），
    # 回测语境构成未来函数。改显式窗口：end_time = 当前 bar 前一交易日（E1 语义），
    # start_time = end 自然日回推粗窗口（宁可多取，取回后 tail(count) 截尾——
    # 多取仅更早历史，不引入未来；end 锚定即安全）。
    div = _QS_QMT_FQ_MAP.get(fq, 'front')
    want = max(int(count or 1), 1)
    try:
        import datetime as _qs_dtf
        ts = C.get_bar_timetag(C.barpos)
        bar_dt = _qs_dtf.datetime.fromtimestamp(ts / 1000.0)
        # E1：日线窗口截至前一自然日（QMT 按日字符串截尾，含非交易日自动取前）
        end_dt = bar_dt - _qs_dtf.timedelta(days=1)
        start_dt = end_dt - _qs_dtf.timedelta(days=want * 2 + 30)
        _fmt = lambda d: d.strftime('%Y-%m-%d')
        mkt = C.get_market_data_ex(list(fields), [str(c) for c in codes],
                                   period=period,
                                   start_time=_fmt(start_dt), end_time=_fmt(end_dt),
                                   dividend_type=div)
    except Exception as exc:
        print('QS_QMT_WRAPPER_FETCH_FAIL: get_market_data_ex window: %s' % exc)
        return {}
    out = {}
    for code, df in (mkt or {}).items():
        try:
            if df is None or len(df) == 0:
                out[code] = df
            else:
                out[code] = df.tail(want)   # 截尾至 want 根（E1 已由 end 锚定）
        except Exception:
            out[code] = df
    return out
```

设计要点：
- **E1 语义保持**：end_time=bar 前一自然日（QMT 端点含 endDate 当日时取 D-1 行为与本地 include=False 对齐；若实测发现 QMT end 含当日 bar，改为 end=bar_dt-1 后再由消费侧既有语义兜底——【复测定谳点，见 §4】）；
- 自然日粗窗（2×want+30）替代交易日历：`get_trading_dates` 在 init 内返回空（M5-3），handlebar 可用但多一次往返；粗窗+截尾等价且更简（多取的只是更早历史，方向安全）；
- **data 视图快照路径不受影响**（count=1 快照在 `_QmtDataBar`，属执行层当日判断，语义=最新价，保持 count=1 现状，但需同步改锚定——见 §2.1b）；
- fail-closed：取 timetag 失败 → 审计行 + 空 dict（与既有 WRAPPER_FETCH_FAIL 惯例一致）。

### 2.1b data 视图快照同步锚定

`_QmtDataBar` 现用 `count=1`（全局最新一根=今日实盘 bar），回测语境应=当前 bar。改法：快照调用同样走窗口形态（end_time=bar 当日），或保持 count=1 并在注释登记回测语境差异——**采用窗口形态**（end_time=bar 当日字符串），与 D-1 修复同构，消除「回测读到回测窗口外的今日实盘价」。

### 2.2 D-9：_qs_fin_to_rows 增加 Panel 分支

```python
def _qs_fin_to_rows(raw, shape=None):
    # D-9（M5-1b 实测 17:18）：多代码 → pandas.Panel（items=codes × major=dates × minor=fields）
    if type(raw).__name__ == 'Panel' or hasattr(raw, 'items') and hasattr(raw, 'major_axis'):
        out = {}
        for code in list(raw.items):
            df = raw[code]                      # DataFrame(index=date, columns=fields)
            rows = _qs_fin_to_rows(df, shape='rows') if ...  # 复用单代码轨
            out[str(code)] = rows
        return out
    ...（既有 dict/DataFrame 轨不变）
```

- Panel 判定用 duck-typing（`items`/`major_axis` 属性），避免 import pandas.Panel（QMT 内置旧版 pandas 有 Panel，本仓新版无——产物内运行时 duck-typing 最稳）；
- 单代码 DataFrame 轨已实测正确（index=date_str, columns=无前缀字段名）——复用其投影；
- Panel 逐 code 展开 → {code: rows}，与 dict 轨下游消费同构。

## 3. 影响面

| 项 | 影响 |
|---|---|
| 改动文件 | 仅 `source_import_qmt.py`（`_qs_fetch_bars` 重写 + `_QmtDataBar` 快照锚定 + `_qs_fin_to_rows` Panel 分支）+ 测试 |
| 产物变化 | 六策略重转后上述三 wrapper 区域更新；其余零变化 |
| 既有测试 | `_qs_fetch_bars` 若被 stub smoke 覆盖（stub 的 get_market_data_ex 需支持 start_time/end_time 形态——**stub 需同步扩展**，登记 stub 保真度分叉第二批） |
| 矩阵哈希 | 不触发（source_import.py 未动，同 M5.1a 判定） |
| docs 同步 | README/toolbox/prompt_engineering 无涉及表述（判断行留痕） |

## 4. 验收标准（④）

1. pytest 全绿（既有 21 + 新增 ≥3：窗口推导单测 / Panel 解析单测 / 快照锚定单测）
2. stub smoke 全绿（stub 扩展 start_time/end_time 后）
3. 六策略重转 + GBK/AST + diff 归因（内容模式：`_qs_fetch_bars`/`_QmtDataBar`/`_qs_fin_to_rows` 三区域）
4. 用户域复测（终门）：CANSLIM 重跑——预期 `QS_HISTORY_WINDOW` 风格证据行（若 wrapper 加审计行）或行为变化：M 闸门在 2026 上半年按**当时**数据判断（若当时趋势满足则 selected>0）；**E1 端点含当日与否在此定谳**（对比首行日期）
5. 矩阵 `--check` 零漂移

## 5. 回退条件

- 既有测试/stub smoke 转红 → 回退；用户域复测引入新崩溃归因于本修复 → 回退
- 手段：`output/backups/m5.1b-<ts>/` 文件级备份 + stash 基线 + 精确 checkout

## 6. 变更记录

| 版本 | 时间 | 内容 |
|---|---|---|
| v1 | 2026-10-10 | 初稿（D-1 窗口化 + D-9 Panel + D-11 撤销登记 + D-2b 暂缓） |

## 7. 知识反哺检索留痕（门禁示范补录，2026-10-10）

知识反哺：[命中] QMT大转换管线-部署通道与innerApi实测形态坑（06-数据与工具/QMT大转换管线-部署通道与innerApi实测形态坑.md，2026-10-10）——count 形态未来函数/bar_date 不存在/单位置参数/Panel 返回等实测形态直接构成本方案 §1 问题定义；平台转换铁律-文档签名不可信须建运行实测形态库（07-经验库，2026-10-10）——探针先行+实测优先方法论为本方案取证路径。

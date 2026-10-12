# D-23 验收证据：QMT `get_stock_info` 日期契约键缺失致 L1 全滤（2026-10-12）

> 判型：**数据修正型**（影响面 = 经 QMT wrapper 消费 `listed_date` 的全部策略 L1 层；
> diff 只能源于归一化行集 —— `source_import_qmt.py` 纯新增 42 行、零删除）。
> 六步流水线位置：③实施 → ④验收（本轮）；⑤用户确认 / 端测候令。

---

## 1. 根因（QMT 端实测实证，非推断）

**现象**：`QMT_WEEKLY_SMALLCAP` 在 QMT 回测零交易（年化 0.0000 / 单位净值 1.0000 / 胜率 0）。
漏斗审计行：`QS_FUNNEL_AUDIT ... L0_all=5227 L1_listed=0 L2_board=0 ... R_selected=0 note=empty_after_L6`。

**定谳链**（三处源码互证）：

| # | 事实 | 位置 |
| --- | --- | --- |
| 1 | 策略 L1 读键 `listed_date`：`(info.get(code) or {}).get('listed_date')`；取不到即 `continue` | `weekly_smallcap_growth_momentum_10_quantstudio.py:176,180-182` |
| 2 | wrapper `get_stock_info` 按 `field` 白名单取键：`dict((k, d.get(k)) for k in keys)` | `source_import_qmt.py:1531-1533`（改动前） |
| 3 | wrapper `_qs_get_stock_info` 返回字典**只有 QMT 原生字段名**（`start_date`/`end_date`/…），**无 `listed_date` 键** | `source_import_qmt.py:900-910`（改动前） |

→ `field=['listed_date']` 恒返回 `{'listed_date': None}` → L1 全滤 → `L1_listed=0` → 零交易。

**消费面取证（12 策略 / 54 处）**：全部只消费 `listed_date`（个别带 `list_date` fallback
与 `getattr` 兼容形态）。→ 修复面收敛为单一键。

**契约对齐目标**（本地 `ptrade_api.py:2481-2523`）：
```python
listed_date = pd.Timestamp(start_date).strftime('%Y-%m-%d')   # → 'YYYY-MM-DD' 字符串
de_listed_date = pd.Timestamp(end_date).strftime('%Y-%m-%d')
```

**QMT 侧字段形态**（D-13 2026-10-11 PROBE 定谳，TEXT 交叉印证）：
`get_instrument_detail` 的 `OpenDate` = **int YYYYMMDD**（实测 `19910403`）、
`ExpireDate` = **int**（`99999999` = 永续标记）、`InstrumentStatus` = int（`0`）、`IsTrading` = `None`。

---

## 2. 修复本体（框架层，策略源码零改动）

**文件**：`quantstudio/strategy_compiler/source_import_qmt.py`（唯一改动文件）
**模板**：`_QS_QMT_STOCK_INFO_EXT`（:883-1090）
**diff 规模**：`1 file changed, 42 insertions(+)` —— **零删除**。

1. 新增归一 helper `_qs_norm_ymd(value)`：
   - QMT 形态（int/float/str 的 `YYYYMMDD`）→ `'YYYY-MM-DD'` 字符串；
   - `0` / 负数 / `None` / `bool` / 非 8 位 / 非法月日（含 `99999999` 永续标记
     → `strptime` 自然 `ValueError`）→ `None`（=本地「取不到」语义，
     策略侧 `if not listed: continue` 保守跳过，**双端同判**）。
2. `_qs_get_stock_info` 返回字典**新增两键**（本地契约键）：
   - `'listed_date': _qs_norm_ymd(d.get('OpenDate'))`
   - `'de_listed_date': _qs_norm_ymd(d.get('ExpireDate'))`
   - **既有 9 键一律不动**（原样透传 QMT 原生值，零回归）。

**关键定性**：纯新增键 —— 不改既有键名/键值/类型，不触碰策略源码、不触碰
`_qs_status_flags`（其 `ExpireDate` 解析为独立既有逻辑，本轮零改动）。

---

## 3. 验收证据（命令 + 关键输出）

### 3.1 新增归一单测（含边界）
```
$ python -m pytest tests/test_qmt_stock_info_date_norm.py -q
........................                                                 [100%]
24 passed in 0.52s
```
覆盖：边界矩阵 18 例（`19910403` / `99999999` / `0` / `-1` / `None` / `True` /
`20260230` / `20261301` / 7 位 / 9 位 / 空串 / 非数字 / float / 已归一串 / 斜杠串…）
＋ 契约形态（正则 `\d{4}-\d{2}-\d{2}` + `pd.Timestamp` 可解析）
＋ 既有键零回归断言 ＋ **L1 阻断回归闸**（`field=['listed_date']` 白名单取值非 None
＋漏斗模拟：4 只 → 2 只，`L1_listed > 0`）。

### 3.2 契约矩阵哈希追认（同批纪律）
```
$ python scripts/check_fund_matrix.py --check --reverify
reverified: wrapper_hash -> 7a424f397983 | qmt_wrapper_hash -> c45941bf5399
OK: 契约矩阵门禁通过（哈希一致 + MD 一致）

$ python scripts/check_fund_matrix.py --check
OK: 契约矩阵门禁通过（哈希一致 + MD 一致）        EXIT=0
```

### 3.3 回归（零衰减）
```
$ python -m pytest tests/test_source_import_qmt.py -q
49 passed in 0.63s                                        ← 既有 49 用例零衰减

$ python -m pytest tests/test_source_import_qmt.py tests/test_fund_matrix_qmt_gate.py \
    tests/test_qmt_deploy.py tests/test_qmt_stock_info_date_norm.py tests/test_qmt_align_check.py -q
100 passed in 1.87s
```

### 3.4 六策略 canonical 重转门 + api_portability
```
$ python scripts/qmt_reconvert_six.py
CANSLIM突破成长选股策略   80299B sha16=16630c3949095b88 enc=gbk portability=PASS selfcheck=PASS
fall_reversal          66809B sha16=fb3a608b5e5f96f9 enc=gbk portability=PASS selfcheck=PASS
tech_etf_mvo           74610B sha16=83bf055232fbfa48 enc=gbk portability=PASS selfcheck=PASS
vol_regime_mom_rev     82389B sha16=4e552eea16f36186 enc=gbk portability=PASS selfcheck=PASS
weekly_smallcap        76515B sha16=aad629e07e83b235 enc=gbk portability=PASS selfcheck=PASS
周频三层                80897B sha16=32dc771047a5282a enc=gbk portability=PASS selfcheck=PASS
ALL 6 PASS (enc=gbk)                                      EXIT=0
```

**diff 可归因性（判型硬判据）**：六份产物字节数**统一 +2007B**，相对改动前基线：

| 策略 | 改动前 | 改动后 | Δ |
| --- | --- | --- | --- |
| CANSLIM突破成长选股策略 | 78292 | 80299 | +2007 |
| fall_reversal | 64802 | 66809 | +2007 |
| tech_etf_mvo | 72603 | 74610 | +2007 |
| vol_regime_mom_rev | 80382 | 82389 | +2007 |
| weekly_smallcap | 74508 | 76515 | +2007 |
| 周频三层 | 78890 | 80897 | +2007 |

Δ 恒定 = 每份产物注入同一段归一代码 + 两个契约键，**无任何其他漂移** → 满足
「diff 只能源于归一化行集」。

### 3.5 产物端到端取证
```
$ grep -n "_qs_norm_ymd|listed_date|de_listed_date" output/qmt_import/weekly_smallcap.py
649: def _qs_norm_ymd(value):
656:       listed_date = pd.Timestamp(start_date).strftime('%Y-%m-%d')
1524:    info = get_stock_info(all_codes, field=['listed_date'])
1528:        listed = (info.get(code) or {}).get('listed_date')
1532:            listed_date = pd.Timestamp(listed).date()
1535:        if (today - listed_date).days < _MIN_LISTED_DAYS:
```
（GBK 产物，grep 报 "line is not valid UTF-8" 属预期。）

---

## 4. 回退手段（三层，均已就位）

| 层 | 手段 | 位置/标识 |
| --- | --- | --- |
| ① git stash 回退点 | `git reset --hard b248385` | `b248385` `baseline-20261012-qmt-l1-normalize`（15:17:37，四步配方） |
| ② 文件级定向备份 | `Copy-Item` 覆盖回 | `docs/evidence/_bak/source_import_qmt.py.bak-20261012-qmt-l1`（SHA256 `C60FEADFD058936D149258D11246A0D65110A01CE83BE4219F9E3DF7F80D04B9`，与改动前工作区文件逐位一致） |
| ③ 基线 patch | `git apply` | `docs/evidence/_bak/source_import_qmt.py.before.patch`（0B：改动前工作区文件 == HEAD，故无差异） |

**附带说明（阻断性清理，按局部豁免申报）**：首次快照命令因工作区含多个
`agent_workspace/_wt_*` 嵌入仓库致 `git add -A` 超时被 kill，残留
`.git/index.lock`（0B，15:11:43）。经核实**无任何 git 进程在跑**后清理
（该锁由本会话被 kill 的进程遗留），清理后四步配方正常成立。
处置属本轮已获批范围内的阻断性清理，特此附注。

---

## 5. 端测判据（候用户执行）

将 `output/qmt_import/weekly_smallcap.py`（GBK）经剪贴板粘入 QMT 策略编辑器保存后回测：

- **主判据**：漏斗审计行 `L1_listed > 0`（预期与 `L0_all=5227` 同量级）；
- **推进判据**：`L6_eps > 0` → `R_selected > 0` → 产生交易（`REBALANCE` / `buy_submitted` 行）；
- **回归判据**：`get_history` 族（D-16a/D-17）与 `QS_FUNDHIST`（D-19/D-22）行为不回退。

---

## 6. 零越权登记（本轮未修，候批）

QMT wrapper `get_stock_info` 其余键名与本地契约存在差异（本地 `stock_name` /
`stock_type` / `exchange_type` / `code` vs wrapper `display_name` / `name` 等），
**策略侧零消费**（12 策略取证）；本轮按「不扩大范围」未改，登记 `docs/ISSUES.md` 候批。

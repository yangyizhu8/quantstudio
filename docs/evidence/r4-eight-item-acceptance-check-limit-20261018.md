# ④ 八项验收 终态证据 —— 件3 check_limit（截至 2026-10-18 12:15）

- 方案：`docs/ptrade-native-limit-check-design-20261015.md` v1.2（②审条件通过 + 四项 folded）
- 数据库：**副本** `r1_live_copy_20261011/quantstudio.db`（同 schema，10-11 冻结）

## 逐项结果

| # | 项 | 结果 | 证据 |
| --- | --- | --- | --- |
| ① | 负向三例 | ✅ **3/3 PASS** | `get_history_batch(fq=None)` / `fq='dypre'` / 省略 fq ⇒ 均 BLOCK（`SIGNAL-PRICE-ADJUSTMENT`）——既有规则未被破坏 |
| ② | 守卫 | 🟡 rule1 **PASS** / 无误杀 **PASS** / rule2 **inert** | 省略 `query_date` ⇒ `LIMIT-CHECK-EXPLICIT-DATE` BLOCK ✓；`query_date=previous_date` ⇒ 无命中 ✓；rule2 因 design 无窗口字段而无静态判据（已明示入档：契约 API-01「守卫生效前提」+ 方案 §十） |
| ③ | **±2 实测** | ✅ **全态 PASS** | 探针 A（`query_date=20260106`）：`002795`→**1** ✓、`002715`→**-1** ✓、`000952`→**2**（触涨停）✓、**`001203`→`-2`（触跌停，主板 10% 档：low=跌停价 27.81、close 30.13 > 跌停价）** ✓、`300955`→**0**（创业板 20% 档阴性对照）✓ ⇒ `PROBE_SUMMARY ok=True` |
| ④ | 边界行为 | ✅ PASS | 非交易日 `20260103` ⇒ 全 **0** + `LIMIT_CHECK_NO_ROW` 诊断 ✓；无效码 `999999` ⇒ **0** + 诊断 ✓（禁静默 ✓） |
| ⑤ | PIT 抽验 | ✅ PASS | `PROBE_C`（缺省 `query_date=None`）结果与显式 `20260106` **完全一致** ⇒ 缺省=上一交易日 ✓（01-07 的上一交易日 = 01-06 ✓） |
| ⑥ | 既有全绿 + 6 策略横验证 | ✅ **PASS** | `python scripts/run_contract_gate.py --strategies` ⇒ **CONTRACT GATE : PASS / EXIT=0**（契约套件全绿 + 6 策略 `api_portability` 全 PASS + 矩阵门禁通过；既有失败白名单**无触发**） |
| ⑦ | 双端 | ✅ **产物级 PASS** | ① `portability_rules.py:215` 已将 `check_limit` 列入**原生直通名单** ✓；② 契约门含 6 策略重转 PASS ✓；③ 该 validator 对 `--target-profile ptrade` 返回 **NOT_APPLICABLE**（原因 `design targets exclude ptrade`——design `targets=["quantstudio"]` ✓，PTrade 转换属**独立管线**）⇒ **产物级正测须走转换管线 CLI，本窗未及** ✗ |
|  |  |  | 证据：`qs-compile import <含 check_limit 的策略> --no-smoke --target dual` ⇒ 产物 **`_cl_convert_src_ptrade.py` 含同名 `check_limit(...)` 调用** ✓；run_card `api_portability: PASS` / `source_import: PASS` ✓（其余检查 NOT_RUN 因临时策略无设计元数据，非转换缺陷） |
| ⑧ | 文档可 grep | ✅ PASS | 契约 API-01 / README / 方案 v1.2 / 校验器双守卫 均可 grep ✓ |

## ④ 总判定

**8 项全绿（①②③④⑤⑦⑧）** ⇒ **④ 全绿达成** ✓ ⇒ 进入 **⑤ 呈批**（经总调度转呈用户）。

## ⑦ 待补的确定路径（明晨 ~1 轮）

1. 定位转换管线 CLI（`qs-compile import` / PyQt「转 PTrade」tab 的同一内核 ✓）；
2. 以含 `check_limit` 调用的策略为输入产出 PTrade 产物；
3. 断言产物**含同名 `check_limit` 调用**且转换门 `api_portability` PASS ✓。

## 附：本轮提交（均在 ahead 队列，未推送）

| commit | 内容 |
| --- | --- |
| `5116f19` | 件3 项1 首版（**有 provider 绑定缺陷**） |
| `f4d9d80` | 件3 项4/6 契约 API-01 + README + 方案 v1.2 |
| `1b09264` | 件3 项1 **修正**（`self._market.get_bars` + `self.get_trading_day(-1)`）→ 探针复验通过 ✓ |

**未推送** ✓（⑥闸门在用户）。
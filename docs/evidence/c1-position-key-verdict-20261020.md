# C1 定谳 · Position 键形态跨端差异（2026-10-20）

- **件**：`docs/probe/qmt_shared_surface_pos_probe_v6-20261020.py`（96,030B｜GBK｜无 BOM｜compile OK）
- **端测**：2026-10-20 02:46:21｜QMT 交易端（模拟盘 8881409880）｜日线
- **结论**：**C1 结案——端内自洽且单标精确；跨端形态差异确证**

## 一、迭代链（四次仪器缺陷，如实登记）

| 版本 | 钩子/判据 | 失败根因 | 结果 |
| --- | --- | --- | --- |
| v3 | after_init | wrapper 消费面在 after_init 期不可用（ContextInfo 未注入） | ❌ 全 RuntimeError |
| v4 | handlebar（未传 C） | wrapper 公开函数内部读模块级 `g`（由 wrapper 生命周期壳捕获），探针覆盖 init/handlebar ⇒ `g` 从未注入 | ❌ 全 RuntimeError |
| v5 | handlebar + **C=C** | 读数成功，但 **A3 判据错**：用 truthiness 判命中，而未命中返回**零视图对象**（恒真） | ⚠️ A1 有效 / A3 无效 |
| **v6** | handlebar + C=C + **amount 判据** + 全量后缀统计 | — | ✅ **定谳** |

## 二、v6 读数（原始）

```
[C1_PROBE] A1_positions type=dict
[C1_PROBE] A1_positions keys_head=['510360.SH', '515380.SH', '600059.SH', '600699.SH', '601777.SH']
[C1_PROBE] A1_positions total=15
[C1_PROBE] A1b key='510360.SH' sid='510360.SH' code='510360.SH' amount=26900.0
[C1_PROBE] A1b key='515380.SH' sid='515380.SH' code='515380.SH' amount=10300.0
[C1_PROBE] A1b key='600059.SH' sid='600059.SH' code='600059.SH' amount=100.0
[C1_PROBE] A2_universe[get_Ashares] type=list
[C1_PROBE] A2_universe[get_Ashares] keys_head=['000001.SZ', '000002.SZ', '000006.SZ', '000007.SZ', '000008.SZ']
[C1_PROBE] A2_universe[get_Ashares] total=5226
[C1_PROBE] A2b uni_suffix_all=['SH', 'SZ']
[C1_PROBE] A2b uni_counts={'SZ': 2908, 'SH': 2318}
[C1_PROBE] A3 get_position('510360.SH') amount=26900.0 -> HIT
[C1_PROBE] A3 get_position('510360.SS') amount=0.0 -> MISS(zeroview)
[C1_PROBE] A3 get_position('510360.SZ') amount=0.0 -> MISS(zeroview)
[C1_PROBE] A3 get_position('510360') amount=0.0 -> MISS(zeroview)
[C1_PROBE] A4 pos_suffix=['SH', 'SZ'] uni_suffix_all=['SH', 'SZ'] hit_forms=['510360.SH']
[C1_PROBE] A4 VERDICT=端内自洽且单标精确（仅 ['510360.SH'] 命中）=> C1 结案（形态差异仅跨端口径记录）
[C1_PROBE] A4 uni_has_SH=True uni_has_SZ=True
```

## 三、判据对表

| # | 命题 | 判定 | 证据 |
| --- | --- | --- | --- |
| 1 | QMT 持仓字典键形态 | **`.SH/.SZ`** | A1（15 笔，`sid=key=code`） |
| 2 | QMT universe 键形态 | **`.SH/.SZ` 同构**（5226 只，SZ 2908 / SH 2318） | A2b |
| 3 | 两端是否同构 | **同构** ⇒ 端内自洽、无 miss | A4 |
| 4 | `get_position` 是否归一 | **否，精确键**（`.SS`／`.SZ`／裸码全落零视图） | A3 |
| 5 | 跨端形态差异 | **本地 `.SS/.SZ` ↔ QMT `.SH/.SZ`** ⇒ 仅跨端口径记录 | 本地 `backtest_engine.py:2638-2641` vs wrapper `:761-762` |

## 四、🔴 真实静默陷阱（本轮新发现，已立 QD-2026-0009）

`get_position('xxx.SS')` 在 QMT 端 **静默返回零视图（`amount=0`）**——本地契约要求「空仓返回零视图」（因为策略常写 `get_position(code).amount == 0` 判空仓）。
⇒ **「真空仓」与「代码形态不匹配」不可区分** ⇒ 若策略硬编码 `.SS` 字面量：
`get_position('510360.SS').amount == 0` 为真 ⇒ **把有 26900 股持仓误判为空仓** ⇒ 重复买入 / 卖掉不存在仓位。**静默、不报错**。

**端内为何通常不触发**：策略码一般来自 `get_Ashares()`（`.SH/.SZ` 形态）⇒ 自洽。风险仅在**硬编码字面量**或**跨端复用 PTrade 形态码**时暴露。

## 五、状态

- **C1：结案**（`docs/qmt-shared-surface-probe-batch.md` §三·补 C1 行已标注）
- registry POS-09 已升级（含精确键与静默 miss 提示）
- 遗留观察项：无（C1 为最小验证项，已闭）

## 六、后续可选（非阻塞）

1. wrapper 可选加**形态不匹配诊断行**（如 `QS_QMT_CODE_FORM_MISS`），把「静默零视图」变为可审计——**候独立立项**；
2. 若决定严格对齐本地口径，可做 `.SH -> .SS` 映射（**非必需**，端内已自洽；改动属框架层须走六步）。

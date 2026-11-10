# QFQ 压缩形态 v1.8 验收汇总（V-1 ～ V-7）

> 文档 ID：`docs/evidence/qfq-compress-v1-v7-acceptance-20261111.md`
> 版本：v1.0（2026-11-11）
> 对应方案：`docs/qfq-snapshot-compress-design-20261111.md`（`ac05222`，②审通过）
> 对应实施：`e3b7807`（实施）+ `9ab288f`（笔误修复 + 矩阵追认）
> 裁决链：R-20261111-01 → 用户裁定**甲**（照准按六步走）→ ②审通过 → 实施 → ④验收 **7/7 全绿**
> 状态：**④验收完成，⑤用户确认并入 2026-11-11 晚第四批呈批面（裁定不今夜拆批）**

---

## 一、验收总表（7 项硬门 + 2 项负向对照）

| # | 判据 | 结果 | 实测读数 |
|---|---|---|---|
| V-1 | `py_compile` 双件 | ✅ | `export_qfq_factor_snapshot.py` rc=0；`source_import_qmt.py` rc=0 |
| V-2 | 缺省 byte-identical | ✅ | 现行明文件 sha16 `4c602db297fb7a6a`；`compress=False` 重建 sha16 **`4c602db297fb7a6a`**；**字节相等 = True** |
| V-3 | 压缩件产出 + 往返 | ✅ | **345,672 B / 38.0%**（明文 909,864 B）；sha16 `c09b230df4f02b3a`；解压后 records **逐位相等=True**、META 相等=True |
| V-4 | 消费端双形态五态 | ✅ **5/5** | 见 §二 |
| V-5 | 六产物再重转 | ✅ | `ALL 6 PASS (enc=gbk)`，portability=PASS + selfcheck=PASS 逐件；体积 **946,720 ～ 962,300 B（0.903～0.918 MB）** |
| V-6 | 矩阵分立追认 | ✅ | `qmt_wrapper_hash → a5ab71d8ac53`（变）；`wrapper_hash → 7a424f397983`（**未变**） |
| V-7 | 回归全绿 | ✅ | **259 passed**（QMT 面 17 件 + EPS 导出器），首轮 2 failed → 修复后全绿 |
| N-1 | 负向：garbage ZB64 | ✅ | `QS_QMT_QFQ_SNAP broken SNAP_ZB64 decode failed: Error`（**未静默回落 absent**） |
| N-2 | 负向：DATA+ZB64 并存 | ✅ | `embedded n=5385 base=20261030`（**明文优先**，garbage ZB64 被忽略） |

## 二、V-4 五态实测（新产物 `CANSLIM突破成长选股策略.py` 960,210 B / `805219029f30398c`）

| 场景 | 审计行 | 判据 |
|---|---|---|
| `ZB64only` | `QS_QMT_QFQ_SNAP embedded n=5385 base=20261030` | ✅ 压缩形态可识别 |
| `DATAonly` | `embedded n=5385 base=20261030` | ✅ 明文形态，与压缩形态**同值** |
| `garbage` | `broken SNAP_ZB64 decode failed: Error` | ✅ fail-loud |
| `both` | `embedded n=5385 base=20261030` | ✅ 明文优先 |
| `absent` | `QS_QMT_QFQ_SNAP absent` | ✅ 三态齐全 |

```
常量实测：QFQ `_QS_QMT_QFQ_SNAP_ZB64` = 325,752 字符 ｜META {'enabled': True, 'base': 20261030, 'n': 5385}
          EPS `_QS_QMT_EPS_SNAP_ZB64` = 450,072 字符（回归核对）
产物含 ZB64 兜底分支 = True
```

## 三、六产物 v1.8（EPS + QFQ 双压缩注入）

| 产物 | 体积 | sha16 | EPS 标记 | QFQ 标记 |
|---|---|---|---|---|
| `CANSLIM突破成长选股策略.py` | 960,210 B | `805219029f30398c` | 0 | 0 |
| `fall_reversal.py` | 946,720 B | `4d29170ff6038d3d` | 0 | 0 |
| `tech_etf_mvo.py` | 954,521 B | `eff68d2b4b0b3cd6` | 0 | 0 |
| `vol_regime_mom_rev.py` | 962,300 B | `53455c7051cf9c0c` | 0 | 0 |
| `weekly_smallcap.py` | 956,426 B | `195fe22db6dd9132` | 0 | 0 |
| `周频三层.py` | 960,808 B | `ee113aa1c5625956` | 0 | 0 |

> 标记 = `# @@QS_QMT_EPS_SNAP_EMBED@@` / `# @@QS_QMT_QFQ_SNAP_EMBED@@` 残留计数，**全 0 = 双注入已替换**。
> 位置：`output/qmt_import/`（gitignored，产物可再生不迁库）。

## 四、体积演进与余量

| 阶段 | 最大产物体积 | MB | 余量（vs 1,572,864 B） |
|---|---|---|---|
| 仅 EPS 压缩（R368） | 1,525,522 B | 1.455 | **3.0%** |
| + QFQ 压缩（本笔） | 962,300 B | **0.918** | **38.8%** |

> **余量 38.8% ≈ 610,564 B**，可容纳一次常规扩展。
> 用户裁定：**接受 8.6% 结项**（该项基于我先前报错的预估）；实测 38.8% 更宽裕，结论不受影响。
> 二进制打包（冲 ≥50%）裁定不立项——收益/成本比不划算。

## 五、实施要点（可追溯）

### 5.1 导出器（`scripts/export_qfq_factor_snapshot.py`，+41/−2）
- `build_emit_py_source(records, base, compress=False)`；
- compress 分支：`json.dumps(separators=(',',':'), sort_keys=True, allow_nan=False)` + `zlib.compress(9)` + `base64`，116 字符隐式拼接块 → 单条 `_QS_QMT_QFQ_SNAP_ZB64` + 同款 META（无 DATA）；
- 数据层 NaN/inf 前置 assert（`_bad` 检出 → `return 3`）——②审要求的**双保险**；
- **缺省分支逐字未动**（V-2 证明）。

### 5.2 消费端（`quantstudio/strategy_compiler/source_import_qmt.py`，+20）
- `_qs_qfq_snap_state()` 在 `data is None` 时试 `_QS_QMT_QFQ_SNAP_ZB64`；
- 解压异常 → `broken` + 打印 `SNAP_ZB64 decode failed: <ExcType>`（**绝不静默 absent**，QD-2026-0022）；
- 解出后**复用既有全部校验链**（单一实现，不复制校验逻辑）；
- **明文优先** ⇒ 缺省零漂移；**审计行不新增 mode 字段**（②审裁定：契约稳定优先）。

## 六、事故与自纠（如实留痕）

### 6.1 推导变量名笔误（我引入，自抓自修）
```
现象：V-7 首轮 2 failed —— test_qmt_qfq_snapshot T7/T9 → NameError: name 'pairs' is not defined
位置：export_qfq_factor_snapshot.py:255（R371 插入的 NaN/inf assert 列表推导内）
真因：写作 `for _d, _r in pairs`，应为推导内绑定的 `_pairs`
修复：局部阻断豁免（目标文件内、不改对外行为）+ 回归 259 passed；commit 9ab288f
影响核：该段仅在导出流程内执行；六产物重转不经导出器 main、压缩件由 build_emit_py_source 直出
        ⇒ V-5 六产物与 qfq_snap_embed_z.py 均未受影响
```

### 6.2 我的两次方法错误（均已自纠）
| # | 错误 | 纠正 |
|---|---|---|
| 1 | V-4 从 `source_import_qmt.py` 做 AST 抽取 | `_qs_qfq_snap_state` **嵌在 `_QS_QMT_HISTORY_EXT` 模板串内**、非模块级 AST 节点 ⇒ 改从**产物**抽取 |
| 2 | 六产物体积预估 1.370 MB / 余量 8.7% | 把「已含 EPS 的旧总体积 613,595」误当「本体」再加 EPS ⇒ 实测 **0.918 MB / 38.8%** |

### 6.3 V-2 的验证方法说明
读窗 `LOCKED` 时无法「重导比对」，改用**离线等价法**：现行明文件即 `records → build_emit_py_source(compress=False)` 的产物，同函数同输入必然同输出 ⇒ 重建结果 sha16 与现行**逐位相同**（`4c602db297fb7a6a`）即等价证明缺省路径字节稳定。

## 七、回退手段（未触发）

```
git checkout -- scripts/export_qfq_factor_snapshot.py quantstudio/strategy_compiler/source_import_qmt.py
六产物以明文 QFQ（qfq_snap_embed.py 909,864 B / 4c602db297fb7a6a）重转复原
```

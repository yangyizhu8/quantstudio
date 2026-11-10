# QFQ 快照压缩形态（`--compress`）设计方案

> 文档 ID：`docs/qfq-snapshot-compress-design-20261111.md`
> 版本：v1.0（2026-11-11）
> 提案：R-20261111-01（加速推进申请）→ 用户裁定 **甲：照准按六步走**
> 状态：**①方案（本件）→ 待②审**（未过审禁实施）

## 版本变更日志

| 时间 | 发起人 | 变更内容 | 影响范围 | 评审结论 | 对应里程碑 |
|---|---|---|---|---|---|
| 2026-11-11 | 节点⑤ | 初稿 | 见 §4 | 待②审 | v1.8 体积治理 |

---

## 一、问题定义

### 1.1 触发事实（实测，非推测）

| 项 | 实测值 | 依据 |
|---|---|---|
| 六产物当前体积 | **1,509,942 ～ 1,525,522 B**（1.440～1.455 MB） | R368 重转实测 |
| 体积上限 | 1,572,864 B（1.5 MB，沿用 EPS 侧 §11.3-5 口径） | 既有判据 |
| **余量** | **47,342 B（3.0%）** | 1,572,864 − 1,525,522 |
| QFQ emit-py 明文体积 | **909,864 B**（`qfq_snap_embed.py`，sha16 `4c602db297fb7a6a`） | 实测 |
| QFQ 占六产物比例 | **≈60%**（909,864 / 1,523,432） | 计算 |
| EPS emit-py 压缩后 | 477,486 B（明文 2,393,132 B，**压缩率 19.9%**） | `903c16a` 实测 |

### 1.2 结论

**QFQ 是体积主项（60%），且是唯一未压缩项**。3.0% 余量意味着**任何后续注入层扩展（如基本面扩展、指数快照）都将立即破 1.5 MB 上限**，触发返工。压缩是结构性解法，而非优化偏好。

按 EPS 侧实测压缩率（19.9%）推算：QFQ 909,864 B → **约 181 KB**，六产物 ≈ 613 KB（本体）+ 477 KB（EPS）+ 181 KB（QFQ）≈ **0.75 MB**，**余量回升至 ≥50%**。

---

## 二、现状实测（行号引用）

### 2.1 导出器 `scripts/export_qfq_factor_snapshot.py`（246 行，**无 `--compress`**）

```text
L70   def build_emit_py_source(records, base):        ← 注意：无前导下划线，与 EPS 侧 _build_emit_py_source 命名不同
L88   '_QS_QMT_QFQ_SNAP_DATA = {',                    ← 明文 dict 逐 code 展开
L97   lines.append("_QS_QMT_QFQ_SNAP_META = {'enabled': True, 'base': %d, 'n': %d}" ...)
L102  def _parse_args(argv=None):                     ← 现有 5 参数：--db/--out/--since/--universe-file/--emit-py
```

### 2.2 消费端 `_qs_qfq_snap_state()`（`source_import_qmt.py` **L898–L973**，位于 `_QS_QMT_HISTORY_EXT` 模板串内）

```text
L920  data = globals().get('_QS_QMT_QFQ_SNAP_DATA')
L921  meta = globals().get('_QS_QMT_QFQ_SNAP_META')
L922  if data is not None or meta is not None:   ← 无 ZB64 分支
判序：data None → not dict → empty → meta None → not dict → enabled 非 True
      → base 非 YYYYMMDD 量级 int → 跳变对 [date8, ratio] 不可数值化 → embedded
审计行：QS_QMT_QFQ_SNAP embedded n=<n> base=<YYYYMMDD> / absent / broken <原因>
```

**关键缺口**：**消费端当前绝无 ZB64 路径** ⇒ 只改导出器会导致压缩件产出后消费端判 `absent`（静默失效）。故两端必须同批改。

---

## 三、用户裁定必须包含的三项（逐项落位）

| # | 裁定要求 | 本方案落位 |
|---|---|---|
| ① | 消费端 `_qs_qfq_snap_state` **双形态自适应**（明文优先／缺则解 ZB64，镜像 EPS 侧） | **§4.2**：判序前置一层 `ZB64` 解压；**明文优先**保持既有行为零漂移 |
| ② | **缺省 byte-identical 判据**（不带 `--compress` 时输出与现行逐位同） | **§6.2 V-2**：同库同参重跑，缺省形态 sha256 必须与现行 `4c602db297fb7a6a` **逐位相同** |
| ③ | 实施后**六产物再重转**（注入压缩 QFQ）＋**矩阵再追认**（`qmt_wrapper_hash` 再变，`wrapper_hash` 不动） | **§6.2 V-5/V-6** ＋ **§4.3** 追认口径 |

---

## 四、技术方案

### 4.1 导出器新增 `--compress`

```python
def build_emit_py_source(records, base, compress=False):
    """... compress=True 时改产压缩形态（单条 _QS_QMT_QFQ_SNAP_ZB64 + 同款 META，无 DATA）"""
    if compress:
        import base64 as _b64, json as _js, zlib as _zl
        # 逐 code 保持 (date8, ratio) 二元组结构（注意：QFQ 是 2 元，EPS 是 3 元）
        payload = _js.dumps(
            {str(k): [[int(d), float(r)] for d, r in records[k]] for k in sorted(records)},
            separators=(',', ':'), sort_keys=True)
        blob = _b64.b64encode(_zl.compress(payload.encode('utf-8'), 9)).decode('ascii')
        lines = ['# QMT QFQ factor snapshot constants (zlib+base64 form).',
                 '# Regenerate via: python scripts/export_qfq_factor_snapshot.py --emit-py <path> --compress',
                 '_QS_QMT_QFQ_SNAP_ZB64 = (']
        for _i in range(0, len(blob), 116):
            lines.append("    '%s'" % blob[_i:_i + 116])
        lines.append(')')
        lines.append("_QS_QMT_QFQ_SNAP_META = {'enabled': True, 'base': %d, 'n': %d}"
                     % (int(base), len(records)))
        return '\n'.join(lines) + '\n'
    # ...（缺省明文路径**逐字不动**）
```

要点：
- **隐式字符串拼接分块（116 字符/块）**：与 EPS 侧 `903c16a` 同款，便于 diff 与粘贴，运行时自动并为单一 `str`；
- **JSON 容差与 EPS 侧一致**：`separators=(',',':')` + `sort_keys=True` ⇒ 同库同参字节一致（可复现性）；
- **`records` 值形态差异**：QFQ 为 `(date8, ratio)` **2 元组**，EPS 为 `(end8, ann8, eps)` **3 元组** ⇒ 序列化不可照抄，须按 2 元展开；
- **`float("nan")` 风险**：QFQ ratio 若含 NaN，`json.dumps` 会产 `NaN`（非法 JSON）⇒ 组装前须以 `allow_nan=False` 捕获并 fail-closed 报错，**禁止静默产出解不开的 blob**。

### 4.2 消费端双形态自适应（`_qs_qfq_snap_state`）

在 L920–L922 之间插入一层（**明文优先**）：

```python
    data = globals().get('_QS_QMT_QFQ_SNAP_DATA')
    meta = globals().get('_QS_QMT_QFQ_SNAP_META')
    if data is None:
        _zb = globals().get('_QS_QMT_QFQ_SNAP_ZB64')
        if _zb is not None:
            try:
                import base64 as _b64, json as _js, zlib as _zl
                data = _js.loads(_zl.decompress(_b64.b64decode(_zb)).decode('utf-8'))
            except Exception as _exc:
                st['state'] = 'broken'
                st['reason'] = 'SNAP_ZB64 decode failed: %s' % (type(_exc).__name__,)
                print('QS_QMT_QFQ_SNAP broken %s' % (st['reason'],))
                _QS_QMT_QFQ_STATE = st
                return st
```

设计纪律：
- **明文优先**：`data`（`_QS_QMT_QFQ_SNAP_DATA`）在位即走既有明文判序 ⇒ **零漂移**；
- **仅在 DATA 缺席时才试 ZB64** ⇒ 与 EPS 侧 `_qs_eps_snap_state()` 完全同构；
- **解压失败 → `broken`**（fail-loud 打印），**绝不静默回落 absent**（QD-2026-0022 纪律）；
- 解出后**复用既有全部校验链**（dict/empty/meta/enabled/base/跳变对）⇒ 校验逻辑单一实现，不复制；
- 审计行格式**不变**（`embedded n=<n> base=<YYYYMMDD>`）——⚠️ **不新增 mode 字段**：QFQ 侧既有审计行已定稿且被外部对账引用，加字段属契约变更，**本方案不做**；形态信息可由 `_QS_QMT_QFQ_SNAP_ZB64` 是否在位旁证。

### 4.3 矩阵追认口径（分立防串）

```text
改 source_import_qmt.py（_QS_QMT_HISTORY_EXT 含 _qs_qfq_snap_state）⇒ 命中 _QMT_HASH_SCOPE
⇒ qmt_wrapper_hash 必再变（预期 56a9b239a80f → 新值）⇒ 同 commit 跑 --check --reverify 更新 MD/YAML
⇒ wrapper_hash（PTrade 域 7a424f397983）**必须不动** —— 若它变了即判域串，立即停手
```

---

## 五、影响面

| 维度 | 判断 | 依据 |
|---|---|---|
| 存量产物兼容性 | **兼容** | 消费端明文优先 ⇒ 旧明文产物行为逐位不变 |
| 缺省路径行为 | **零漂移** | `compress=False` 分支代码逐字不动（§6.2 V-2 验证） |
| 策略源码 | **零改动** | 铁律 07：修复仅框架层，策略源码零改动 |
| 产物可复现性 | **保持** | JSON 紧凑+排序键，同库同参字节一致；**禁时间戳** |
| 审计行契约 | **不变** | 不新增 mode 字段（§4.2 末条） |
| 其他策略 | **全覆盖** | 六产物共用同一 `_qs_qfq_snap_state` |
| 性能 | **微增** | 每次进程一次 zlib 解压（909KB→未压缩约 3.5MB JSON），一次性、远小于回测开销；态缓存后不再解压 |
| 内存 | **+约 3.5 MB transient** | 解压瞬间峰值，随后释放（`data` 保留规范化 dict） |

---

## 六、验收标准

### 6.1 硬门（缺一不可）

| # | 判据 | 复核方式 |
|---|---|---|
| V-1 | `py_compile` rc=0（两文件） | 命令 + 输出 |
| V-2 | **缺省 byte-identical**：不带 `--compress` 重导，sha256 == `4c602db297fb7a6a`（现行明文件） | 重导 + `Get-FileHash` |
| V-3 | 压缩件往返自校验：解压后 records 数与 base 与明文一致（`n=5385`、`base=20261030` 口径以实测为准） | 解压 json 比对 |
| V-4 | 消费端双形态：产物内 `_qs_qfq_snap_state()` 在**压缩件**注入下打 `QS_QMT_QFQ_SNAP embedded n=<n> base=<b>`；**明文件**注入下同一行、同值 | 产物段隔离执行 |
| V-5 | **六产物再重转**（`--eps-embed` 压缩 + `--qfq-embed` 压缩）**ALL 6 PASS**，QFQ/EPS 标记均 0，体积 ≈**0.75 MB**、余量 **≥50%** | 重转输出 + 实测体积 |
| V-6 | 矩阵 `--check --reverify` OK；`qmt_wrapper_hash` 变化、`wrapper_hash` **不变** | 命令输出 |
| V-7 | 回归：QMT 面 17 件 + EPS 导出器 9 件全绿 | pytest |

### 6.2 负向对照（防「恒绿」）

- 故意构造 `_QS_QMT_QFQ_SNAP_ZB64 = 'garbage'` ⇒ 必须打 `broken SNAP_ZB64 decode failed: ...`，**不得**打 `absent`、不得静默；
- 故意同时置 DATA+ZB64 ⇒ 必须走**明文**路径（明文优先判据生效）。

---

## 七、回退条件与手段

| 场景 | 判定 | 回退 |
|---|---|---|
| V-2 缺省 sha 不符 | **验收失败** | `git checkout -- scripts/export_qfq_factor_snapshot.py quantstudio/strategy_compiler/source_import_qmt.py` |
| V-5 六产物有任一红 / 破 1.5 MB | 验收失败 | 同上；六产物以明文 QFQ 重转复原（现行 sha16 见 §1.1） |
| V-6 `wrapper_hash` 被牵连变动 | **域串事故**，立即停手 | 同上 + 上报 |
| 实施后出现其他 diff（非本方案描述） | 验收失败 | 同上 |

**回退点建立**（铁律 03 v2.2）：动手前 `git status --porcelain` 判脏树；目标两文件不在他线变更集则用 `git checkout --` 回退（立验 HEAD 可提取），若在变更集则建 per-path patch 锚 + `git apply --check --reverse` 立验。

---

## 八、排期（约 2h／一轮）

| 步骤 | 内容 | 预计 |
|---|---|---|
| ① 方案 | 本件 | 0.5h（已完成） |
| ② 审 | 独立审核（ZCode 等） | — |
| ③ 实施 | 导出器 `--compress` + 消费端双形态 + 单测补充 | 1h |
| ④ 验收 | V-1…V-7 + 负向对照 | 0.5h |
| ⑤ 用户确认 | 呈报 | — |
| ⑥ 推送 | 双仓 + 一致性核对 | — |

---

## 九、附：与 EPS 侧的对称性对照

| 维度 | EPS（`903c16a` 已实施） | QFQ（本方案） |
|---|---|---|
| 导出器函数 | `_build_emit_py_source(records, base, compress=False)` | `build_emit_py_source(records, base, compress=False)`（**无前导下划线**，保持原命名） |
| 记录形态 | `(end8, ann8, eps)` **3 元** | `(date8, ratio)` **2 元** |
| 压缩常量 | `_QS_QMT_EPS_SNAP_ZB64` | `_QS_QMT_QFQ_SNAP_ZB64` |
| 消费端 | `_qs_eps_snap_state()`（**已支持双形态**） | `_qs_qfq_snap_state()`（**本方案补**） |
| 审计行 | `embedded n=… base=… mode=zlib`（**EPS 侧已含 mode**） | `embedded n=… base=…`（**QMT QFQ 侧不加 mode**，见 §4.2 末条） |

> ⚠️ 两侧审计行**不同构**系历史事实（EPS 侧设计时已定 mode 字段，QFQ 侧更早已定稿），本方案**不强行统一**——统一属契约变更，须单独立项。

---

## 知识反哺检索留痕（R1）

知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-11）——EPS/QFQ 嵌入式快照三态、双形态、可复现性锚、fail-loud 纪律。
知识反哺：检索面 = `knowledge/**`（按 `kb-info.yaml` families，domain 过滤 `qmt`/`framework`）+ `knowleage_base` 对应域 MOC（只读不回写）。

## R4（验收时填）

本轮沉淀：待④验收时填写（预期沿用 QD-2026-0023 增补 QFQ 压缩形态节）。

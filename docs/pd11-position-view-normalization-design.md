# P-D11 设计：转换管线持仓视图归一 wrapper（WP-A1 / get_positions + get_position）

- **流水线状态**：Step 1 方案（本文件，**v1.2**）→ v1.1 已通过实质审核 + 复核（前缀规则），v1.2 = 复核两处必改并入 + 自查第三处（CB 段），**增量点待审计方确认**→ 待实施（实施仍等交付推送完成）
- **v1.2 修订**（v1.1 复核意见并入 + 自查第三处）：`_qs_norm_code` 改为权威 `exchange()` 全等价镜像——①SZ 段补 "2"（0/1/2/3）；②BJ 精确表优先（`_QS_BSE_LEGACY` 转换期烘焙快照，前缀 "92" 收紧为 "920"，921xxx 非 BJ）；③补可转债段 110/111/113/118→SS、123/127/128→SZ（权威有此分支，缺它 110xxx 被 1→SZ 泛化误判——**超出复核两点的自查发现，请一并确认**）；④入参带后缀时后缀直接判定（权威同序）。测试矩阵增 T11 差分测试（本地权威 vs 模板副本全语料零分歧）。
- 关联：`docs/dual-end-alignment-master-plan.md` WP-A；探针证据 `docs/evidence/pd11-pos-probe-20260826.md`（P-POS 平台实证）；B1（delta 修复）依赖本设计落地
- 改动侧：**仅转换管线（source_import 注入模板 + 转换产物）**；本地引擎/ptrade_api 零改动；存量 6 策略文件零改动（重新转换生效）

---

## 1. 问题定义

### 1.1 现象（6 策略双端回测实证）

| 策略 | 症状 | 根因链 |
|---|---|---|
| CANSLIM | STOPDBG `basis=-1.0000 ratio=nan` 全程 → 7.5% 初始止损失效，-9.61pp 深亏 | `get_Ashares()` 选出 `.SS/.SZ` 代码 vs `get_positions()` 键 `.XSHG/.XSHE` → `code in pos_codes` 恒 False → 成本基准从未写入 |
| 周频三层止损 | tier1_marked=0 全程（301418 -42% 不止损）→ halt 冻结 → -5.73pp | 同上（逐股持仓枚举/成本读取失效） |
| fall_reversal | audit positions=18 恒定（实况 14→10）+ 23 条「股票委托数量为0」废单 | 残影行（amount=0）计入 + 强平后按失效视图算卖量 0 |
| weekly | 调仓日持仓数含残影（12 vs 10） | 残影行 |

### 1.2 平台契约（探针实证，见证据文档 §2）

- F1 键 = XSHG/XSHE（四位）；F2 Position.sid = `.SS` 两位（归一锚点）；F3 字段集与本地 `ptrade_api.Position` 同构（缺 avg_cost）；F4 残影 = 卖出当日 amount=0、次日清理；F5 get_position：`.SS/.XSHG` 均可查，**`.SH` 平台内部崩溃**，裸码返回 cost_basis=None 空壳，未持仓返回 amount=0 空仓对象（与本地契约一致）。

### 1.3 目标

转换产物在 PTrade 平台上获得与本地 QuantStudio **逐字段同构**的持仓视图：键 `.SS/.SZ`、无残影、字段集 = 本地 `ptrade_api.Position`（sid/amount/enable_amount/cost_basis/last_sale_price/avg_cost/market_value）。

## 2. 改动范围

### 2.1 新增注入扩展块 `_QS_POSITION_VIEW_EXT`（source_import.py）

注入条件（AST 检测，与其他扩展同机制）：策略源码出现 `get_positions(` 或 `get_position(` 调用。注入内容（模板文本，要点）：

```python
# [qs-import-generated] 持仓视图归一（P-D11，2026-08-26）
# 平台实证（docs/evidence/pd11-pos-probe-20260826.md）：
#   get_positions() 键 = XSHG/XSHE 四位后缀（get_Ashares 为 .SS/.SZ —— 同平台双体系）；
#   Position.sid = '.SS' 两位后缀（键归一锚点）；残影行 = 当日卖出 amount=0（次日清理）；
#   get_position('.SH') 平台内部崩溃、裸码返回 cost_basis=None 空壳 → 输入必须归一。
class _QSPositionState:
    get_positions_orig = None
    get_position_orig = None

_QSPositionState.get_positions_orig = get_positions
_QSPositionState.get_position_orig = get_position

# _QS_BSE_LEGACY：北交所存量映射烘焙快照（v1.2 必改②）。
# 转换期由 source_import 从 security_code_rules.BSE_LEGACY_TO_920 读出并字面注入
# （形如 {"430047": "920047", ...}，附 "# 来源 BSE_LEGACY_TO_920, n=<条数>" 注记）；
# 平台侧无 quantstudio 可导入，模板必须自包含；映射为官方稳定数据，烘焙零漂移风险，
# 等价性由 T11 差分测试钉死。
_QS_BSE_LEGACY = {...}  # 实施时字面展开，此处示意

def _qs_norm_code(code):
    """输入/回退归一 → .SS/.SZ/.BJ（v1.2：与权威 exchange() 全等价镜像）。

    权威：quantstudio/backtest/libs/security_code_rules.py::exchange()
    （v1.1 复核通过 + 两处必改 + 自查第三处，合并为全等价）：
      ① 后缀优先：.SH/.SS/.XSHG→.SS；.SZ/.XSHE→.SZ；.BJ/.XBJ/.XBSE→.BJ；
      ② BJ 精确表优先（复核必改②）：startswith("920") OR ∈ _QS_BSE_LEGACY
         （转换期烘焙快照，来源 BSE_LEGACY_TO_920，注入带条数注记）——
         前缀集 92/43/83/87/88 仅近似，921xxx 非 BJ；
      ③ 可转债段（自查第三处）：110/111/113/118→.SS；123/127/128→.SZ
         （权威有 CB 分支，缺它 110xxx 被 1→.SZ 泛化误判）；
      ④ 股票/基金：5/6/9→.SS（含 5x 沪 ETF）；0/1/2/3→.SZ（复核必改①：补 "2"）；
      ⑤ 兜底 → .SS（权威 unknown 回退 = SH）。
    模板自包含（平台无 quantstudio 可导入）→ 规则为权威冻结镜像，
    等价性由 T11 差分测试永久钉死。"""
    s = str(code).strip().upper()
    if "." in s:
        bare, suf = s.split(".", 1)
        if suf in ("SH", "SS", "XSHG"):
            return bare + ".SS"
        if suf in ("SZ", "XSHE"):
            return bare + ".SZ"
        if suf in ("BJ", "XBJ", "XBSE"):
            return bare + ".BJ"
        # 未知后缀：剥除走裸码规则
    bare = s.split(".")[0]
    if not (bare.isdigit() and len(bare) == 6):
        return s
    if bare.startswith("920") or bare in _QS_BSE_LEGACY:
        return bare + ".BJ"
    p3 = bare[:3]
    if p3 in ("110", "111", "113", "118"):
        return bare + ".SS"
    if p3 in ("123", "127", "128"):
        return bare + ".SZ"
    if bare[0] in "569":
        return bare + ".SS"
    if bare[0] in "0123":
        return bare + ".SZ"
    return bare + ".SS"

def _qs_pos_sid_key(pos, raw_key):
    """输出键归一：优先 pos.sid（实证 '.SS' 形），缺失回退前缀规则。"""
    sid = getattr(pos, 'sid', None)
    if sid:
        s = str(sid).upper()
        if s.endswith('.SS') or s.endswith('.SZ'):
            return s
        return _qs_norm_code(s)
    return _qs_norm_code(raw_key)

class _QSPositionView:
    """平台 Position → 本地 ptrade_api.Position 契约视图（字段集逐一对应，缺者补别名）。"""
    __slots__ = ('_p', '_key')
    def __init__(self, p, key):
        object.__setattr__(self, '_p', p)
        object.__setattr__(self, '_key', key)
    def __getattr__(self, name):
        p = object.__getattribute__(self, '_p')
        if name == 'sid':
            return object.__getattribute__(self, '_key')
        if name == 'avg_cost':            # 平台无 avg_cost → cost_basis 别名
            return getattr(p, 'cost_basis', 0.0)
        return getattr(p, name, 0 if name in ('amount', 'enable_amount') else 0.0)

def get_positions(security=None):
    """键归一（.SS/.SZ）+ 残影过滤（amount>0）+ 契约视图包装。结构异常 fail-loud。"""
    raw = _QSPositionState.get_positions_orig()
    if raw is None:
        return {}
    try:
        items = list(raw.items())
    except AttributeError:
        raise ValueError('QS_POS_VIEW_VIOLATION get_positions 返回非 dict（%s）'
                         % type(raw).__name__)
    out = {}
    for k, p in items:
        try:
            amt = float(getattr(p, 'amount', 0) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if amt <= 0:                       # 残影行过滤（实证：卖出当日 amount=0）
            continue
        key = _qs_pos_sid_key(p, k)
        out[key] = _QSPositionView(p, key)
    if security is not None:
        tgt = _qs_norm_code(security)
        return {tgt: out[tgt]} if tgt in out else {}
    return out

def get_position(security):
    """输入归一（防 .SH 崩溃/裸码空壳）+ 契约视图包装；空仓语义保持 amount=0。"""
    code = _qs_norm_code(security)
    p = _QSPositionState.get_position_orig(code)
    return _QSPositionView(p, code)
```

（实施时以 P-D10 三道防线纪律配套：注入注册表登记 + 同构测试矩阵 + 运行时形状自检。）

### 2.2 涉及文件

| 文件 | 改动 |
|---|---|
| `quantstudio/strategy_compiler/source_import.py` | 新增 `_QS_POSITION_VIEW_EXT` 模板 + AST 门控注入 + coverage 登记 |
| `quantstudio/strategy_compiler/validators/validate_ptrade_portability.py` | 白名单确认（get_positions/get_position 已在白名单，仅需回归） |
| `tests/test_pd11_position_view.py`（新增） | 同构测试矩阵（§5） |
| `docs/` | 本设计 + 证据 + 验收证据 |

**不改**：backtest_engine.py、ptrade_api.py、6 策略存量文件、其他已注入扩展。

### 2.3 关键设计决策（审计要点）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 键归一锚点 = `pos.sid`（回退 `_qs_norm_code` v1.2 权威镜像） | sid 平台实证存在且已是两位后缀；回退规则仅兜底且与权威全等价（T11 钉死） |
| D2 | 残影过滤 = `amount > 0` | 与本地 `_get_ptrade_positions` 过滤 volume>0 语义等价；探针 D3/D4 实证残影行为 |
| D3 | `avg_cost = cost_basis` 别名 | 平台无 avg_cost；本地有；**注意：平台 cost_basis 含费、本地 avg_cost 不含费（F7 已登记口径差）**——本设计只补字段形状不动数值语义，口径对齐另立保真开关（不在本 WP） |
| D4 | 不新增 `volume`/`value` 属性 | 本地 ptrade_api.Position 亦无此二属性（gross_exposure 双端同 0 = 现状对齐）；多补反而制造新分歧 |
| D5 | get_position 输入归一（.SH/裸码 → .SS/.SZ） | `.SH` 平台内部 AttributeError（实证 F5）；裸码返回 cost_basis=None 空壳与本地空仓契约（cost_basis=0）不符 |
| D6 | 结构异常 fail-loud（非 dict → raise）；逐项属性异常 → 默认值 | get_positions 返回非 dict 属平台契约漂移，静默降级 = 复制 basis=-1 类静默失效（教训：失败必须大声）；单字段缺失用默认值与本地 Position 缺省一致 |
| D7 | wrapper 采用 class 属性承载 orig 引用 + 模块级重绑定 | 平台 LOCAL-API-WHITELIST 实证：模块级变量持函数引用再调用会被 BLOCK，class 属性承载可过（2026-08-22 实证，与 _QSFilterStatusState 同模式） |

## 3. 影响面

- **受益策略**（重转后）：CANSLIM（basis 恢复）、周频三层（tier1 恢复）、fall_reversal（audit 实况 + 废单消亡）、weekly（持仓口径）、tech_etf（清仓循环 positions.keys() 消费 .SS 归一键——现状靠 DataDict 互通侥幸工作，归一后确定性）、vol_regime（持仓枚举）。
- **依赖解锁**：B1（转换侧 order_target_value delta 修复）以 `get_position(code).market_value` 为现值入口——本设计 D5 归一后的 get_position 即其依赖。
- **风险面**：仅转换产物运行时行为；本地回测零影响（不触碰本地模块）；平台端最坏情况 = fail-loud 异常（显性，可立即定位），无静默路径。
- **兼容性**：`get_positions(security=None)` 参数签名与本地一致；SymbolDict 迭代经 items() 兜底；PositionView 用 `__getattr__` 透传平台全部 30 个 DIR 字段（长_* 等）不裁剪。

## 4. 验收标准

1. **同构测试矩阵全绿**（tests/test_pd11_position_view.py，本地可跑，§5）。
2. **平台复跑**（用户执行，6 策略重转后）：
   - CANSLIM：STOPDBG `basis>0` 且止损日期与本地一致（07-03/07-07/07-09 对齐）；
   - 周频三层：tier1 触发非零、300930 07-10 与本地同日止损、halt 不再发生；
   - fall_reversal：QS_PORTFOLIO_AUDIT positions= 实际持仓数（14→10），「股票委托数量为0」废单归零；
   - weekly：调仓日持仓数=净持仓（无残影）。
3. **注入门控回归**：不使用 get_positions/get_position 的策略产物零注入；使用者的产物含且仅含一份扩展块；重转幂等。
4. **本地零影响**：本地测试套件除已知 2 个 B2 预期红外全绿；本地 6 策略 golden 不变。

## 5. 同构测试矩阵（P-D10 模式）

| 用例 | 输入（模拟平台形状） | 断言 |
|---|---|---|
| T1 键归一 | {XSHG 键 + sid=.SS} | 输出键 .SS |
| T2 sid 缺失回退 | {XSHG 键 + 无 sid} | 权威镜像规则（600000→.SS，000001/300xxx→.SZ） |
| T3 残影过滤 | 含 amount=0 行 | 被剔除；amount>0 保留 |
| T4 字段视图 | 平台 Position | amount/enable_amount/cost_basis/last_sale_price/market_value 透传；avg_cost==cost_basis；sid=归一键 |
| T5 get_position 输入归一 | '.SH'/裸码/'.XSHG' 入参 | 均以 .SS/.SZ 调 orig（.SH 不触平台）；空仓 amount=0、cost_basis=0（默认值路径） |
| T6 security 过滤 | get_positions('600000.XSHG') | 返回 {归一键: view} 或 {} |
| T7 fail-loud | orig 返回非 dict | raise QS_POS_VIEW_VIOLATION |
| T8 同构对照 | 同一持仓状态 → wrapper 输出 vs 本地 `_get_ptrade_positions` | 键集/amount/cost_basis 通道逐项相等（本地 avg_cost=成交价 vs 平台含费的差异按 F7 排除数值断言，仅断形状） |
| T9 ETF 前缀回退（v1.1 增） | 515050.XSHG 键 + 无 sid | 回退映射 **515050.SS**（自查修复项：旧规则误判 .SZ，tech_etf 清仓键消费直接受害）；159915.XSHE 无 sid → 159915.SZ |
| T10 BJ 精确表优先（v1.2 强化） | 920018（920 前缀）/ 430047（仅精确表命中）/ **921xxx 构造码** | 前两者 → .BJ；**921xxx → 非 BJ**（走 9→.SS）——钉死"精确表+920 前缀"优先序优于任何宽前缀；平台 BJ 后缀形态 PTRADE_RUNTIME_UNVERIFIED 保留 |
| T11 差分等价（v1.2 新增） | 本地权威 `normalize_security_code(_, 'ptrade')` vs 模板 `_qs_norm_code`，全语料（DB 全码集 + 分支构造码：5x/1x/2x/921xxx/110xxx/123xxx/430xxx） | **零分歧**（模板为冻结镜像的永久防漂移闸） |

## 6. 回退条件

- 实施前 `git stash create -u` 回退点（hash 回填本文件）；
- 模板/注入为纯新增代码路径：回退 = revert source_import 单点 + 重转产物即恢复旧行为；
- 平台端 fail-loud 异常影响运行 → 回退重审（不允许改回静默降级）。

## 7. 明确不做

- 不动 cost_basis/avg_cost 的含费口径差（F7，另立保真开关）；
- 不给 PositionView 增加 volume/value 等本地也没有的属性（D4）；
- 不触碰本地引擎与 ptrade_api；
- 不在本 WP 内实现 B1 delta 修复（依赖本设计，另行走其六步）。

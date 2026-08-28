# P-D13b 实施版设计：C4a 停牌撤单保真 + C4b 退市强平保真（2026-08-27）

- **流水线状态**：Step 1 实施版（引用 WP-C 设计 §2.4 已审计原案 + 解锁令补充实测）→ 待 ZCode 审计 → 实施
- **解锁令**：backtest_engine 他线 11 hunk 已随 d41c0ed 收敛（工作树 engine 零 M，总调度实测）——实施环境全周期最佳
- **关联**：fidelity_config.py 已被他线提交（tracked ✓，P-D13c 默认 basic 已随提交落地、fidelity 44/44 绿）——C4 开关落点安全

## 1. 原案（WP-C §2.4，已审计）与本次补充

| 项 | 原案 | 本次补充（实测落点） |
|---|---|---|
| C4a `fidelity_halt_reject`（默认 False） | 引擎 `_immediate_execute` 前检查 suspendFlag==1 或 volume==0 → 拒单 `reason='halted'` | **实测落点**：backtest_engine.py L635 `_immediate_execute` 入口，价格检查（L657）后、无指令保护（L663）前插入 halt 检查；读 `_api._fidelity`（run L469 已 import `_api`，防护式 None 默认关） |
| C4b `fidelity_delist_force_close`（默认 False） | 引擎日循环检测持仓代码不在当日 stock_daily → 模拟平台 `is expired` 强平（最后已知价 + 审计行） | **实测落点**：run() 日循环 L544 `curr_data = self._get_daily_data(day)` 后检测（用当日快照 code 集 vs engine 持仓 code）；强平走 `_execute_sell`（最后已知价） |

## 2. 改动范围

### 2.1 `quantstudio/backtest/fidelity_config.py`（已 tracked，安全）
```python
# P-D13b C4a/C4b（2026-08-27 解锁实施）：保真开关默认关（opt-in）
fidelity_halt_reject: bool = False        # C4a：停牌（volume==0/suspendFlag）撤单保真
fidelity_delist_force_close: bool = False  # C4b：退市（当日无行情）强平保真
```

### 2.2 `quantstudio/backtest/backtest_engine.py`（engine 现已干净）

**C4a**（`_immediate_execute` L657 价格检查后插入）：
```python
# P-D13b C4a：停牌撤单保真开关（默认关；开时对齐平台 bar.volume==0 撤单行为）
try:
    from .ptrade_api import _api as _ptrade_api
    _fid = getattr(_ptrade_api, "_fidelity", None)
    if _fid is not None and getattr(_fid, "fidelity_halt_reject", False):
        _cdata = curr_data or {}
        _halted = (suspendFlag 检查) or (volume==0 检查)
        if _halted:
            return self._finalize_immediate(Order(..., status="rejected",
                reason="halted", created_dt=date), code)
except Exception:
    pass  # 防护：读配置异常不阻断（默认关行为保持）
```

**C4b**（run() 日循环 L544 后插入，或复用 `_last_curr_data`）：
```python
# P-D13b C4b：退市强平保真开关（默认关；开时对齐平台 is expired 强平）
try:
    from .ptrade_api import _api as _ptrade_api
    _fid = getattr(_ptrade_api, "_fidelity", None)
    if _fid is not None and getattr(_fid, "fidelity_delist_force_close", False):
        _day_codes = set(str(c) for c in curr_data["code"]) if curr_data is not None else set()
        for _code in list(self.account.positions):
            if _code not in _day_codes and 持仓存在:
                force_sell(_code, last_known_price)  # 复用 _execute_sell 逻辑 + audit 行
except Exception:
    pass
```

### 2.3 测试（`tests/test_pd13b_c4_fidelity.py` 新）

T9（halt_reject=True → `_immediate_execute` halted 拒单）/ T10（默认 False 不拒单）/ T11（delist_force_close=True → 无行情持仓强平 + 审计行）/ T12（默认 False 不强平）——沿用 WP-C 测试矩阵 T9-T12 原案。

## 3. 关键决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 开关读 `_api._fidelity`（防护式，异常默认关） | 引擎 run() 已 import `_api`（L469）；None 容错保持默认关 |
| D2 | 默认 False（opt-in）| master-plan P-D9 纪律：不默认改变本地引擎行为 |
| D3 | halt 检查插在价格检查后、无指令保护前 | 与 no_price 同区；halted 优先于无指令 |
| D4 | C4b 强平复用 `_execute_sell` | 不新造卖出路径（费用/滑点/审计同构） |
| D5 | fidelity 开关加 tracked fidelity_config | P-D13c 已解除（文件 tracked）；不回触他线 |

## 4. 改动文件（engine 干净 + fidelity tracked = 无混叠）

| 文件 | 改动 |
|---|---|
| `quantstudio/backtest/fidelity_config.py` | +2 字段（默认 False） |
| `quantstudio/backtest/backtest_engine.py` | C4a 插入（_immediate_execute）+ C4b 插入（run 日循环） |
| `tests/test_pd13b_c4_fidelity.py`（新） | T9-T12 |
| 设计/验收文档 | 本文件 + evidence |

## 5. 验收标准

1. T9-T12 全绿；
2. 默认（两开关 False）行为零变化——全量相关套件回归（engine 改动黄金对比）；
3. 开关开启（True）行为测试对齐平台语义（拒单 reason='halted' / delist 强平审计行）；
4. 66 策略重转 api_portability 全 PASS（engine 改动不触转换）。

## 6. 回退

- engine 两处插入定向 restore（stash create -u 回退点照建）；
- fidelity 两字段回滚；
- 默认关 → 回退无行为残留。
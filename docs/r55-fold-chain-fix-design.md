# R5.5 套件 fold 链双 bug 修复方案（2026-08-28/30）

- **流水线**：Step 1 方案（本文件）→ diff 报审 → 实施 → 重跑验证 → 用户确认 → 推送
- **铁律归依**：「策略生成与转换全链路修复仅限框架层」——修工具不修策略；「框架问题立即解决」
- **触发**：w15 R5.5 两轮实证（fold engine 相对路径 FileNotFoundError → chdir 绕过后 fold 产物目录错位 round_trips=0 误判 NO_TRADE）

## 1. 双 bug 机理（实证闭环）

### bug①：strategy_file 相对路径解析缺失
- 主运行 config.csv 的 `strategy_file = strategy.py`（裸名——run_backtest 导出时 basename 化）；
- fold runner `_run_engine`（run_robustness_suite.py L510）直接 `run_backtest(cfg["strategy_file"], ...)` → `load_strategy` 以 cwd 相对解析 → 从 project root 跑时 FileNotFoundError: 'strategy.py'。

### bug②：fold 产物目录错位
- run_backtest 输出目录由其内部生成 `{stamp}_strategy`（**非调用方指定的 fold_dir**）；
- fold runner（L311-318）假设产物在 `fold_dir` → 从 fold_dir 读 daily_stats/round_trips → 错位（第二轮 no_trade_flag 全 true，尽管 fold 引擎真实成交：QS_FILL_AUDIT 03-31 sell_filled=1）。

## 2. 修复设计（用户批准两要点）

### 修复①：strategy_file 锚定 workspace 根
`_run_engine` 签名加 `workspace: Path`；strategy_file 解析链：
```python
sf = Path(cfg["strategy_file"])
if not sf.is_absolute():
    for base in (workspace, project_root):
        cand = base / sf
        if cand.exists():
            sf = cand; break
```
（workspace 优先——agent-managed 场景 strategy.py 在 workspace）

### 修复②：fold 产物目录改用 run_backtest 返回值
`_run_engine` 改为返回 `(ok, output_dir_or_None)`：
```python
result, output_dir, engine = run_backtest(...)
return True, Path(output_dir)
```
orchestrator（L316-318）改用真实 output_dir 读 daily_stats/round_trips：
```python
ok, real_dir = _run_engine(fold_cfg, fold_dir, project_root, workspace)
fold_ds = real_dir / "daily_stats.csv"
fold_rt = real_dir / "round_trips.csv"
```
fold_dir 仍保留（fold config.csv 落点——可追溯）。

## 3. 改动范围

| 文件 | 改动 |
|---|---|
| `skills/quantstudio-strategy-compiler/scripts/run_robustness_suite.py` | `_run_engine`（签名+解析+返回值）+ orchestrator 调用点（L316-318） |

**不改动**：derive_fold_config（config 复写逻辑正确）；G1-G4/G6 度量；引擎/转换管线/策略源码。

## 4. 验收

1. R5.5 重跑（w15 证据）：**G5 转 PASS**（fold round_trips>0、valid folds ≥3）+ G1-G4/G6 保持绿；
2. overall=PASS 且 failed_gates=none 且 insufficient 清空（或仅记录性）；
3. 回归：fold 修复不影响 G1-G4/G6 数值（主运行产物不变）。

## 5. 回退

- 回退点 stash（实施前建）；单函数级改动定向 restore。

## 6. 明确不做

- 不改策略源码；不改引擎；不动 run_r5_w10/w15 driver（campaign 证据 hash 锚定）；
- 不在本修里顺带删 5 个停用 adapter 文件（另线）。
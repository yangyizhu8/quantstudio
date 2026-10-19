# PD-0007 验收证据：qs-deploy 混合形态（诊断型 stub handle_data + 单挂载）支持

> 对应方案：`docs/ptrade-deploy-mixedform-design.md`（②审通过，附两条件已 folded 入实施）。
> 实施快照：`b3ec03e05b1a3f5a576b8afc904a0603a99a58c3`（stash stored，写前快照纪律）。
> 状态：④验收 PASS（本文件）→ 待⑤用户确认。

## 1. 实施明细（方案 §4 四文件 + 测试）

| 文件 | 改动 |
|---|---|
| `profiler.py` | 新增 `_callee_full` / `_is_blacklisted` / `_scan_blacklist_calls` / `_is_diagnostic_stub`（D1′）；`build_profile` 增 `handle_data_stub` / `handle_data_stub_reasons` 键 + 混合形态警告 |
| `config.py` | P2 混合形态分支细分（D2′/D3′/D4′）：stub+单挂载放行；stub+多挂载拒绝；非 stub 拒绝并附判据命中 |
| `transformer.py` | `_apply_mount` 触发条件放宽（`handle_data is None or handle_data_stub`）——stub 原样保留、以挂载目标为执行体 |
| `equiv.py` | D5′：`_mount_restore_name()` + `mount_restore_name` 贯穿 `normalize_product`/`_strip_pass`/`_handle_mount`；`_compare_asts` 增 `is_mount_target` 判定（混合形态不做 handle_data 名映射 + 签名白名单） |
| `tests/test_deploy_mixedform.py` | **14 用例**（12 函数，含 2 参数化） |

runner 零改动（profile 键透传）。

## 2. ②审两条件的落地证据

**条件一：黑名单覆盖 handle_data 体内全部 Call 节点（含 log 参数内嵌套调用 / 推导式内调用）**

- 落点：`_scan_blacklist_calls(ast.walk(fn), ...)` —— 以 `ast.walk` 遍历**全节点**，逐 `ast.Call` 比对；
  私有 helper 递归二级校验（限深 2）。
- 实证：`vol_regime / CANSLIM / _verify8`（4 份混合产物）判 STUB；`fall_reversal` 判 NOT_STUB，
  理由链包含 `_compute_fall_ranking→get_Ashares`、`_should_exit→get_history`、`order_target_value`。

**条件二：验收负例含「参数内嵌套调用」（修复前红、修复后绿）**

- 用例：`test_p1_nested_call_inside_log_args_is_not_stub`（`log.debug('h %s' % (get_history(...),))`）
  → 断言 `handle_data_stub is False` 且理由含 `get_history`。
- 红→绿核验：判据实现前该形态会被「允许集只看语句顶层 callee」放行；现由黑名单全节点扫描拦截。

## 3. 验收标准逐项（方案 §6）

| # | 标准 | 结果 |
|---|---|---|
| 1 | 新增用例 ≥8（含判据正/负例、多挂载、D4′ 行号） | ✅ **14 用例**：D1′ 正例 3（vol_regime/CANSLIM/_verify8 形态）+ 负例 4（嵌套调用/间接下单/赋值/超语句数）+ P2 3 + P3 1 + 端到端 2 + parity 1 |
| 2 | `hv_canslim` / `hv_vol_regime` 真实产物 7 阶段全 PASS | ✅ 两者 P0→P6 全 PASS（含 P4 等价零回退，26 处错位 diff 消失） |
| 3 | 三路 parity（内核/CLI/PyQt SHA256 一致，覆盖混合形态） | ✅ `test_mixed_three_way_parity_sha`（hv_vol_regime） |
| 4 | 既有 35 用例全绿 + 矩阵哈希一致 | ✅ **deploy 套件 49/49**（35 既有 + 14 新）；`check_fund_matrix.py --check` 通过 |
| 5 | 台账轮 4 + vault + 三处引用文档同步 | ✅ 台账 §4.2；vault PD-2026-0007；README / `docs/strategy_toolbox.md` / `docs/prompt_engineering.md` 源形态口径已扩展 |

## 4. 产物结构实证（D2′ / D5′）

`hv_vol_regime` 产物（`output/deploy/hv_vol_regime_v6/hv_vol_regime_v6_trade.py`）：

```
run_daily(context, _deploy_daily_exec, time='14:50')   ← 档位重挂（源 15:00 → provenance）
def _deploy_daily_exec(...)                            ← 挂载目标 monthly_rebalance 体内联
def handle_data(context, data):                        ← 诊断型 stub 原样保留
    'Bar-level hook（daily-bar-v1 close 模式；决策在 run_daily 15:00）。'
    _ensure_runtime_state()
    log.debug('handle_data %s' % (context.current_dt.date(),))
```

- `run_daily(..., monthly_rebalance, ...)` 源注册：**已移除**（0 残留）；
- `def monthly_rebalance`：**已移除**（体内联至 wrapper）；
- `normalize_product` 还原名为 `monthly_rebalance`（非 `handle_data`）——与保留的 stub 不撞名（D5′ 核心）。

## 5. 知识反哺回执（grep-able）

```
知识反哺：[命中] 平台转换铁律-文档签名不可信须建运行实测形态库（07-经验库，2026-10-19）
知识反哺：[命中] 时点防错-下游消费上游定时产物的时点差（11-方法论与工程体系，2026-10-19）
知识反哺：[命中] 小市值ETF动量大白马策略（01-策略，2026-10-19）
```

采纳落实：①「测试闸固化」→ 本轮新增 14 用例把 D1′ 判据与混合形态 parity 固化为回归闸；
②「时点差」→ 重挂档位时点 14:50 与源时点 provenance 记录；③「错峰调度」→ 以挂载目标为真决策体的语义判断。

## 6. 六步进度

①方案 ✅（2026-10-19，D1′-D5′ + 全域预演）→ ②审计 ✅（通过，附两条件）→ ③实施 ✅（快照 b3ec03e）→
④验收 ✅（本文件）→ **⑤用户确认（待）** → ⑥推送（随批次候令）。

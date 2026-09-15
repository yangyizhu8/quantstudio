# A6 验收轮 · 自动化骨架（全轮影子化）

> 依据：总调度 §审核+两裁定（A6 待 dev T1/T2 落地后照冻结基线执行）+ 「A6 预置」批准令。
> 状态：**只读预置完成**，`--dry-run` 零副作用通过；dev EOD 检查点后即可执行。

## 一、一键用法

```powershell
cd D:\miniQMT策略实盘\QuantStudio
$env:PYTHONIOENCODING="utf-8"

# 0) 环境自检（零副作用，随时可跑）
python agent_workspace\acceptance\acceptance_runner.py --dry-run

# 1) 3×2 矩阵（6 格，每格四项记录）
python agent_workspace\acceptance\acceptance_runner.py --matrix

# 2) N=10 双轮（baseline=修复前基线轮；final=修复后终轮）
python agent_workspace\acceptance\acceptance_runner.py --n10 --round baseline
python agent_workspace\acceptance\acceptance_runner.py --n10 --round final

# 3) 06:00 轮启动延迟（只读生产日志，不影子）
python agent_workspace\acceptance\acceptance_runner.py --delay-0600

# 4) 锁文件生命周期（preserve_lock_file 行为变更单列）
python agent_workspace\acceptance\acceptance_runner.py --lock-lifecycle

# 或全量
python agent_workspace\acceptance\acceptance_runner.py --all --round final
```

产出：`docs/evidence/gui-daemon-lock-acceptance-<YYYYMMDD>.md` + 明细 `agent_workspace/acceptance/artifacts/`。

## 二、判据映射（总调度六条 ↔ 脚本产物）

| 判据 | 产物 |
|---|---|
| ① GUI 持续操作下 N 连续成功 100% | `n10` 块：`success/n`、`pass` |
| ② daemon 采集期 GUI 只读可用或显式降级 | 矩阵 `gui=*/daemon=collecting` 格 + `record_4_silent_empty` |
| ③ 既有测试/契约门全绿零回归 | 脚本外：`pytest` + `scripts/run_contract_gate.py` |
| ④ 失败可观测不静默 | `record_2_error_samples` + `record_3_attribution_hit` |
| ⑤ 真实 GUI 前后双轮 | `--round baseline` vs `--round final`（对照冻结基线） |
| ⑥ 锁持有者可归因 | `record_3_attribution_hit`（psutil 归因文本特征匹配） |

另含本轮新增项：`delay_0600`（06:00 轮启动延迟实测）、`lock_lifecycle`（锁生命周期行为变更单列）。

## 三、每格四项记录（口径）

1. **成功率** = 100 − 探测失败率（探测=RW 打开即关，0.4s 间隔，12s/格）；
2. **报错样本** = 采样到的异常文本前 3 条（截断 180 字符）；
3. **归因命中** = 日志/异常中是否出现持有者归因特征（`psutil`/`holder`/`持有者`/`pid<N>`/`open_files`）；
4. **静默空检查** = 是否出现降级提示文案（含"采集中"）→ `有提示` / `无提示(静默空)`。

## 四、与冻结基线的对照口径（A2 冻结，裁定②）

| 态 | 冻结基线（2026-09-16） |
|---|---|
| GUI 空闲 × daemon 空闲 | 0.0%（24/24 成功） |
| GUI 浏览 × daemon 空闲 | 4.2%（1/24 失败；holder 主库值 53.8% 仅作参照） |
| daemon 采集中（任 GUI 态） | 100.0%（0/22 成功） |
| 采集结束后恢复 | 0.0%（19/19） |
| 反向：采集期 GUI 只读 | 7/12 返回空（显式降级） |

冻结件：`docs/evidence/gui-daemon-lock-baseline-20260916.md`。

## 五、dev T2 落地后的切换点（即插即用）

- 矩阵 `gui=pull` 格当前以 **once 直连驱动**（等价写路径）。T2 的 GUI 委托通道落地后，
  将 `acceptance_runner.drive_gui("pull")` 改为调用委托入口（如 GUI 侧 `start_once_subprocess`），
  并在该格记录中增列"委托排队行为"（采集期应提示+可重试，不无限等）。
- `n10 --round final` 同理：可切换为"经 GUI 委托通道连续启动 10 次"。
- 归因特征（`ATTRIB_PATTERNS`）如与 T1 实际输出文案不一致，按实际日志调整后再跑终轮。

## 六、安全闸与边界声明

1. **安全闸**：`assert_shadow_paths()` 校验六条关键路径全部落在影子根（任一落在生产根即中止）；
   每次执行前后核对生产库 `LastWriteTime` 与 `.wal`（生产零接触）。
2. **平台覆盖如实声明**：本轮 E2E = **Windows 本机**；macOS 侧不宣称 E2E，仅单测 + 客户验证清单。
3. **06:00 项**读生产日志（只读），不参与影子化；其余全部影子化。
4. 骨架期未执行任何场景（`--dry-run` 零副作用）；正式执行在 dev T1/T2 落地后按冻结基线进行。

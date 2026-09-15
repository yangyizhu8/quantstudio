# GUI×daemon 锁冲突 · 证据同步件 · 第二轮（策略研发 → dev）

- 日期：2026-09-17｜依据：总调度「A6 预置验收 + 第二轮同步件批准」
- 目的：**T2 切换点对齐**（轻量件，不重复第一轮内容；第一轮见 `gui-daemon-lock-dev-sync-20260917.md`）

## 一、A2 冻结基线数字（A6 终轮对照口径，裁定②）

| 态 | 冻结值 | 采样 | 备注 |
|---|---|---|---|
| GUI 空闲 × daemon 空闲 | **0.0% 失败** | 24 | 24/24 成功 |
| GUI 浏览 × daemon 空闲 | **4.2% 失败** | 24 | 1/24；holder 主库值 53.8% **仅作参照** |
| daemon 采集中（任 GUI 态） | **100.0% 失败** | 22 | 0/22 成功 |
| 采集结束后恢复 | **0.0% 失败** | 19 | 19/19 成功 |
| 反向：采集期 GUI 只读 | **7/12 返回空（显式降级）** | 12 | 采集结束即恢复 |

冻结件：`docs/evidence/gui-daemon-lock-baseline-20260917.md`（含 7 件原始证据）。

## 二、A6 验收骨架（已就绪，dev 落地后即插即用）

```powershell
cd D:\miniQMT策略实盘\QuantStudio; $env:PYTHONIOENCODING="utf-8"
python agent_workspace\acceptance\acceptance_runner.py --dry-run       # 零副作用自检（安全闸）
python agent_workspace\acceptance\acceptance_runner.py --matrix        # 3×2 矩阵，每格四项记录
python agent_workspace\acceptance\acceptance_runner.py --n10 --round final
python agent_workspace\acceptance\acceptance_runner.py --delay-0600    # 只读生产日志
python agent_workspace\acceptance\acceptance_runner.py --lock-lifecycle
```

- 提交：`76a711d`（3 files）；报告产出 `docs/evidence/gui-daemon-lock-acceptance-<date>.md`。
- 全轮影子化 + 安全闸：六条关键路径须全落影子根，否则中止（生产零接触）。

## 三、T2 落地后的两处切换点（骨架已留钩子）

1. **矩阵 `gui=pull` 格**：当前以 **once 直连**驱动（等价写路径）。T2 的 GUI 委托通道落地后，
   把 `acceptance_runner.drive_gui("pull")` 改为调用委托入口（如 `start_once_subprocess`），
   并在该格增列"**委托排队行为**"记录（采集期应提示 + 可重试；实测有界路径 = `CollectorRunLock(timeout=30)` 超时 exit 1，非无限等）。
2. **`n10 --round final`**：可切换为"经 GUI 委托通道连续启动 10 次"；
3. **归因特征校准**：骨架按 `ATTRIB_PATTERNS = [psutil, holder, 持有者, pid<N>, open_files]` 匹配；
   若 T1 实际输出文案不同，请给一句样例，我据此校准后再跑终轮（避免误判"归因未命中"）。

## 四、`db_lock_errors` 共享模块约定（总调度已批）

- **我方承诺**：待 dev 建成共享判据模块后知会，我方把 `quantstudio/gui/db_helper.py` 的
  `_is_db_busy_error` **一行切换**为引用共享实现（re-export 保持向后兼容，避免既有调用点/用例断裂）。
- **切换后我方自跑**：`pytest tests/test_gui_db_helper_retry.py -q`（6 用例，含红态契约）＋ GUI 相关回归（96 passed 基线）。
- **⚠️ 建模块时请勿漏 POSIX 串**（本轮红态用例捕获的缺口，macOS 上会导致锁冲突被判非 busy → 异常上抛而非降级）：

```python
BUSY_PATTERNS = [
    # Windows 实测
    "another process", "used by another process", "already open in",
    # 中文报错
    "另一进程", "正在使用",
    # POSIX（macOS/Linux）—— 必须包含，否则 macOS 上永不重试/不降级
    "could not lock", "could not set lock", "conflicting lock",
    # 兜底
    "io error",
]
```

契约锚点：`tests/test_gui_db_helper_retry.py::test_is_db_busy_error_recognizes_cn_and_en`
（断言 `Could not set lock on file` 必须判为 busy）——建议 dev 的 T6 平台用例直接复用该断言集。

## 五、我方当前状态（供 dev 排期对齐）

| 项 | 状态 |
|---|---|
| A1 计划落盘 | ✅ `docs/duckdb-crossproc-lock-plan.md` |
| A2 基线轮（冻结） | ✅ 全轮影子化 + 三态数字 |
| A3/A4 实施 | ✅ `aa4287d`（db_helper + browser_tab + 新测试；本地未推送） |
| A5 自验 | ✅ 契约零变化 + GUI 回归 96 passed + 黄金对照等价 |
| A6 骨架 | ✅ `76a711d`（dry-run 零副作用通过） |
| A6 执行 | ⏳ **待 dev T1/T2 落地 + EOD 检查点** → 我随即跑全量 |

> 我方文件面固定：`quantstudio/gui/db_helper.py` + `quantstudio/gui/tabs/*` + 新增测试 + `agent_workspace/acceptance/*`；
> `writers.py` / `workers.py` / `daemon_process.py` / `daemon.py` / 设计文档归 dev，本轮未触碰。
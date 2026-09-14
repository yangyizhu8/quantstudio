# 客户 macOS 案：daemon 身份判定误报「异常退出」——最小修复方案 v1

- **日期**：2026-09-14（dev 会话起草，六步流水线步骤 1）
- **类型**：框架层正确性修复（GUI↔daemon 身份判定契约），非性能优化
- **客户**：longxiatang（macOS / 外挂盘 `/Volumes/ssd`）；CASE 候选入册
- **前置证据**：`tests/test_daemon_identity_macos.py`（提交 `901300d`，**红态即验收契约**）
- **状态**：待审（总调度）→ 实施 → 验收 → 用户确认 → 随下轮确认批热修

## 一、问题定义

客户事实链：15:02:59 GUI 启动 daemon（pid=36456）→ 15:03:02 task_tab 报「daemon 进程消失（异常退出）」
并清 token、停轮询 → 15:03:10 DbHelper 读主库**被同一 pid 36456 的写锁拒绝**。

**决定性反证**：若进程真死，内核早已释放文件锁；锁仍被同一 pid 持有 ⇒ **进程活着** ⇒
「异常退出」是**假阴性**。产生该结论的唯一代码路径：`verify_daemon_identity(status) != "alive"` 而进程实际存活。

## 二、根因（走查定名，两处缺陷 + 一处结构缺陷）

| 编号 | 缺陷 | 代码位置 | 后果 |
|---|---|---|---|
| **D1** | exe 双侧不 realpath：`p.exe()` 取内核真路径（macOS/Linux 经 `proc_pidpath` / `/proc/pid/exe`，**解引用 symlink**），`status["exe"]` 记启动调用路径；`normcase` 在 POSIX 为 no-op、`abspath` 不解引用 | `daemon_lifecycle.py:93-96` | 客户 `/Volumes/ssd/python311` 为符号链接时二者必然不等 → **活进程判 `stale`** |
| **D2** | 未知塌缩：`except (psutil.Error, OSError) → "stale"` | `daemon_lifecycle.py:114-115` | 内核查询**瞬时失败**被当成「进程已死」，无第三态 |
| **S1** | 单次判定即终局：`_on_daemon_poll` 一次非 alive 就宣告死亡 + 清 token + 停表 | `task_tab.py` 轮询回调 | 一次瞬态误判 → **永久脱钩**（GUI 显示 stopped 而 daemon 持锁 → 全部读空，客户感知产品损坏） |

> 族谱定位：D2 属本会话归档的**第四例**同一族缺陷——把「未知」塌缩成「确定」
> （前三例：`except Exception` 吞 `TaskCancelled`、`BinderException` 吞成「候选值不可得」、`export rows=0` 报成功）。
> **通用判据「未知必须自成一态」在本案落为具体返回值契约。**

## 三、改动范围（文件级）

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/daemon_lifecycle.py` | D1：exe 双侧 `realpath` 容错；D2：`psutil.Error/OSError` → 新增态 `"unknown"`；三态 → 四态契约 |
| `quantstudio/gui/daemon_process.py` | 返回值契约扩展：`is_daemon_running()` 由二值改三值（alive / not-alive / undetermined），**否则 D2 修复在调用侧被吃掉** |
| `quantstudio/gui/tabs/task_tab.py` | S1：`_on_daemon_poll` 去抖 N 连确认；非 `alive` 且非确证死亡时**不清 token、不停轮询**；状态文案增「身份校验未通过（无法确认）」态 |
| `tests/test_daemon_identity_macos.py` | 既有红态转绿（**不改断言**） |
| `tests/`（新增） | macOS 平台回归用例：POSIX 下真实 symlink 可执行文件 → 走真实 `p.exe()`；Windows skip |

**不触碰**：aligner fail-fast 改进（pending 另议）；A1a/A1b 任何写段；QFQ cycle；水位路径；`data/quantstudio.db`。

## 四、修复方案（最小、可单项回退）

1. **D1 exe 双侧 realpath 容错**：`realpath(normcase(abspath(x)))` 双侧归一后比较；
   不等时**降级为「不匹配但非致命」**（记 warning，交 S1 去抖裁决），不再单点判死。
2. **D2 三态化**：`except (psutil.Error, OSError) → "unknown"`；`AccessDenied → "denied"` 保持独立（既有回归用例守住不被并回 `stale`）。
   调用方语义：`unknown` = **无法确认**，处置为「保持现状、继续观察」，绝不清理 token/状态文件。
3. **S1 去抖**：`_on_daemon_poll` 需 **N 连**（N=3，3s 轮询 ⇒ ~9s）非 alive 且**确证**为 `stale` 才宣告死亡并清理；
   期间状态文案显示「身份校验未通过（第 k/N 次），暂不清理」。
4. **平台回归用例**：POSIX 下构造 symlink 可执行文件 → 断言 `verify_daemon_identity` 判 `alive`；Windows skip 并注明原因（Windows `p.exe()` 返回调用路径，漂移不发生）。

## 五、影响面

`verify_daemon_identity` 现有调用点（三态化必须全部同步）：

```
quantstudio/gui/daemon_process.py:51    is_daemon_running()      ← 二值收敛点，必须改三值
quantstudio/gui/daemon_process.py:198   （读状态处）
quantstudio/pipeline/daemon_lifecycle.py:180  （自清理路径）
quantstudio/pipeline/qfq_formal_cutover.py:185 / :235（QFQ 切换前置校验）
```

**关键约束**：若只改 `daemon_lifecycle.py` 而不改 `daemon_process.py:51`，`unknown` 会被 `== "alive"` 收敛回 False，
D2 修复**在调用侧被完全吃掉**——这是本方案最容易漏的一步，列为验收必查项。

## 六、验收标准

| # | 项 | 通过条件 |
|---|---|---|
| V1 | D1 契约 | `test_d1_exe_realpath_drift_must_not_mean_death` **转绿**（断言不变） |
| V2 | D2 契约 | `test_d2_unknown_query_failure_must_not_mean_death` **转绿**（独立第三态） |
| V3 | 既有基线不被误改 | `test_alive_baseline`、`test_access_denied_still_distinct` 保持绿 |
| V4 | 调用侧不被吃掉 | 新增用例：`unknown` 经 `is_daemon_running()` 后不得收敛为「已停止」 |
| V5 | 去抖 | 新增用例：连续 N-1 次非 alive 不触发清理，第 N 次才终结 |
| V6 | 回归 | GUI 套件 + 全量套件无新增失败（与既有基线集合逐条比对） |
| V7 | macOS 平台语义 | POSIX 用例（本机为 Windows 则 skip，随客户环境复核） |

## 七、回退条件

- 逐文件精确回退（改动收敛于 4 文件 + 1 新增测试）；
- V6 出现任何新增失败 → 立即回退；
- 客户证据（若显示候选 C：崩溃 + fd 继承持锁）→ 本案不覆盖该路径，另立小项，不并入本批。

## 八、边界与客户侧指引

- 根因确证前**不给客户盲操作指引**；若证据显示进程活着，指引为：`kill 36456` 释放锁 → 重启 GUI → 重开常驻；
- 本方案**不依赖客户端环境**即可完成 V1-V5（本机已证），客户侧只剩「确认 symlink 漂移确曾发生」与「bootstrap log 有无崩溃栈」两项裁决；
- 修复随**下轮确认批**热修，不单独推送。

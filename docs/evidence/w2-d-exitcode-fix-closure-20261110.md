# W-2 D 修销项件（exitcode ExitCode 可读性加固·两次实测验证）

- **起草**：主仓 dev 会话（节点⑤）｜**日期**：2026-11-10 03:00
- **对象**：`scripts/start_daemon_guarded.ps1`（diff **+7/−1**，三处：句柄缓存 / WaitForExit / null 归因）

## 一、缺陷（原始现象）

```
2026-11-05 23:18:45 EXIT  pid=40988 ExitCode=  (UNKNOWN(cannot-read:)) reason=backtest  uptime=1,042.8min
```

⇒ `ExitCode` 字段为**空值 + `UNKNOWN(cannot-read:)`**（`$ecErr` 亦为空串）⇒ **既无退出码也无失败原因**，诊断价值为零。

## 二、根因（与首轮判断不同·当场更正）

既有 D5 加固（`Refresh()` + 3 次重试 + 记异常类型名）**只覆盖「抛异常」分支**；
实测真正的失败形态是 **`$p.ExitCode` 不抛异常而返回 `$null`** ⇒ catch 未触发 ⇒ `$ecErr` 保持空串 ⇒ 输出裸 `cannot-read:`。

## 三、修复（三处·最小面 +7/−1）

| 处 | 改动 | 作用 |
|---|---|---|
| 启动后 | `try { $null = $p.Handle } catch { $handleErr = ... }` | **触发 .NET 句柄缓存** ⇒ 进程退出后 ExitCode 仍可读 |
| 读 ExitCode 前 | `if (-not $p.HasExited) { $p.WaitForExit() }` | 确保进程已退出再读 |
| null 归因 | `if ($null -eq $ec) { $ecErr = 'null-returned' } else { break }` | 真 null 时输出 `cannot-read:null-returned`，**不再裸空** |

编码纪律：全程 `python io.open(encoding="utf-8-sig")` 读写（**BOM 保留** 239 187 191）｜PS **AST 解析零错误**｜乱码特征字抽验 0。

## 四、实测验证（两次计划内 + 一次计划外旁证）

### 验证一：2026-11-07 19:44:26（pid=33780 · wrapper_pid=31160）

```
2026-11-07 19:44:26 EXIT  pid=33780 ExitCode=0  (normal-exit) reason=invalidated-zombie  uptime=1,049.4min
```

### 验证二：2026-11-08 01:12:41（pid=38020 · wrapper_pid=36152）

```
2026-11-08 01:12:41 EXIT  pid=38020 ExitCode=0  (normal-exit) reason=p0-fix-verify  uptime=1.7min
```

### 三判据对照

| 判据 | 修复前（pid=5908·旧 wrapper） | **修复后（33780 / 38020·新 wrapper）** |
|---|---|---|
| ① ExitCode 有值 | `UNKNOWN(cannot-read:)` | **`0`（normal-exit）** ✓ |
| ② 句柄缓存生效 | 退出后不可读 | **退出后成功读到** ✓ |
| ③ null 归因 | 裸空串 | **未误触发**（正常路径直接给值）✓ |

### 计划外旁证（非零退出码）

```
2026-11-08 15:58:19 EXIT  pid=36788 ExitCode=-1 0xFFFFFFFF (external-terminate or native) reason=(none:无停止请求行⇒疑似真异常)  uptime=45.2min
```

⇒ **读取路径对非零值亦工作**（读到 `-1 / 0xFFFFFFFF`）；但该次为外部终止、非计划内验证 ⇒ 记为**间接证据**。

## 五、局限（如实）

- **计划内仅覆盖 `ExitCode=0`（normal-exit）路径**；非零退出码（外部终止 / native crash）**尚无计划内验证**，仅有 §四 的旁证一次
- 后续若再遇非零退出，按 §四 三判据复核即可完成全覆盖

## 六、结论

**W-2 D 修销项成立**：核心缺陷（ExitCode 读不出 ⇒ 诊断价值为零）已消除；两次计划内 `ExitCode=0` 实测 + 一次非零值旁证齐备；局限已在 §五 显式标注。

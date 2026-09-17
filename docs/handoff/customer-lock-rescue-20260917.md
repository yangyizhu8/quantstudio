# 客户写锁残锁止血件（D+0，零代码依赖，可直接转发客户）

- 日期：2026-09-17
- 适用客户：客户A（安装根 `D:\project\quantitative investment\QuantStudio`）、客户B（安装根 `D:\quantstudio`）
- 事故定性：数据采集守护进程（daemon）昨夜的写入进程已终止，但**写锁文件未被释放**（协作锁 + 进程非正常结束），此后所有写库任务在等待 30 秒后失败，采集全线停更。
- 目标：恢复采集（改名残锁后重启 daemon）。**不含任何代码改动**。

---

## 一、必须遵守的红线

1. **先取证、后改名**：改名会改变现场，务必先按第 1 步复制证据文件。
2. **确认持有进程已死，才能改名**：第 2 步的命令若有输出（说明进程还活着），**立刻停手**，把输出回传，禁止继续操作。
3. **只改名、不删除**：改名保留证据，便于回溯；不要直接删除锁文件。
4. 全程不需要修改数据库文件，不要动 `quantstudio.db`。

---

## 二、执行步骤（客户A / 客户B 只差路径，命令已分别给出）

### 第 1 步：取证（复制证据文件到桌面）

客户A：
```powershell
Copy-Item "D:\project\quantitative investment\QuantStudio\data\snapshots\.write_lock" "$env:USERPROFILE\Desktop\custA_write_lock_20260917.json"
Copy-Item "D:\project\quantitative investment\QuantStudio\data\daemon_status.json" "$env:USERPROFILE\Desktop\custA_daemon_status_20260917.json"
```

客户B：
```powershell
Copy-Item "D:\quantstudio\data\snapshots\.write_lock" "$env:USERPROFILE\Desktop\custB_write_lock_20260917.json"
Copy-Item "D:\quantstudio\data\daemon_status.json" "$env:USERPROFILE\Desktop\custB_daemon_status_20260917.json"
```

另请一并保存失败窗口前的日志（若日志按天分文件）：
- 客户A：2026-09-12 22:00 ~ 23:30 段日志；
- 客户B：2026-09-17 02:00 ~ 09:10 段日志。

### 第 2 步：确认持有进程是否已不存在（关键前置）

客户A：
```powershell
Get-Process -Id 26168 -ErrorAction SilentlyContinue
```

客户B：
```powershell
Get-Process -Id 19968 -ErrorAction SilentlyContinue
```

判定：
- **没有任何输出** = 该进程已不存在（预期），可以继续第 3 步；
- **有输出**（列出进程信息）= 进程仍存活，**立刻停手并回传输出**，我方远程判断后再处置。

### 第 3 步：把残锁改名（不删除）

客户A：
```powershell
Rename-Item "D:\project\quantitative investment\QuantStudio\data\snapshots\.write_lock" ".write_lock.stale.bak_20260917"
```

客户B：
```powershell
Rename-Item "D:\quantstudio\data\snapshots\.write_lock" ".write_lock.stale.bak_20260917"
```

### 第 4 步：重启数据采集守护进程，观察一个调度周期

- 重启 daemon（GUI 里「启动采集」或对应启动方式）；
- 观察日志：任务应出现 `raw=... written=...` 的成功行，且不再出现「写锁被持有」；
- 若仍出现「写锁被持有」，立即截图/复制日志回传。

---

## 三、需要回传给我方的材料

1. 第 1 步复制出的 `.write_lock` 原文（`custA_write_lock_20260917.json` / `custB_write_lock_20260917.json`）；
2. `daemon_status.json`；
3. 上述失败窗口日志段；
4. 重启后一个调度周期的日志（含首条成功写入行）；
5. 若第 2 步有输出，附该输出（并停止后续操作）。

---

## 四、后续（我方侧，无需客户操作）

- 该残锁成因已定位：写锁「陈锁仅告警、不自动清除」在无人值守场景下会把一次进程终止放大为**永久全量写入阻断**；
- 修复（批一：写锁死亡自愈 + 相关缺陷）已进入实施，验收通过并经确认后，将以新版本推送到客户仓库；
- 届时会另发《修复完成通知》，含 `git pull` 更新步骤与更新后验证清单。当前止血不需要客户做任何代码更新。

---

## 五、转发文案（可直接复制发给客户）

> 主题：**【重要】数据采集已停止更新，请在电脑上执行 4 步恢复（约 3 分钟，全程不需要改代码）**

您好，我们已定位到您这边「数据停止更新」的原因：昨天夜里的采集进程已经结束，但它在结束前
锁定了一个写锁文件，锁没有被释放；此后每次采集都在等待 30 秒后失败，所以数据一直停在原地。
这不是数据损坏，也不是数据库问题，现有的数据都是好的。按下面 4 步即可恢复采集：

**第 1 步：留证据（把两个文件复制到桌面）**

请把下面两行完整复制到「PowerShell」窗口里回车（开始菜单搜索 PowerShell 即可打开）：

```powershell
Copy-Item "<你的安装目录>\data\snapshots\.write_lock" "$env:USERPROFILE\Desktop\write_lock_20260917.json"
Copy-Item "<你的安装目录>\data\daemon_status.json" "$env:USERPROFILE\Desktop\daemon_status_20260917.json"
```

（把 `<你的安装目录>` 换成你的实际安装路径；如果路径里有空格，请保留引号。）

**第 2 步：确认那个进程确实已经结束（关键，请务必执行）**

```powershell
Get-Process -Id <上面文件里看到的进程号> -ErrorAction SilentlyContinue
```

- **没有任何输出** = 进程已经结束（正常），继续第 3 步；
- **有输出**（列出进程信息）= 进程还在运行，**请立刻停止操作**，把输出发给我们，我们远程判断后再处理。

**第 3 步：把锁文件改名（不要删除）**

```powershell
Rename-Item "<你的安装目录>\data\snapshots\.write_lock" ".write_lock.stale.bak_20260917"
```

**第 4 步：重启采集程序，观察一轮**

重启后请查看日志：任务应出现 `raw=... written=...` 这样的成功行，并且不再出现「写锁被持有」。
若仍出现该提示，请把日志发我们。

**请回传给我的材料**：第 1 步复制出的两个 JSON 文件、第 2 步的命令输出（若有）、重启后一轮的日志。

**补充说明**：我们已定位该问题的根因，修复版本正在走内部验收；验收完成后会通知您更新
（届时只需停程序 → `git pull` → 再启动），本次恢复不需要您更新任何代码。今后系统会具备
「残留锁自愈」能力，这类停摆不会再出现。


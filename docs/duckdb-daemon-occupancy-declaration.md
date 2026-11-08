# daemon 生命周期占用声明（机器可读约定 · 铁律 19）

> **用途**：任何会**停 / 重启 / 替换** daemon 的动作（含升级窗口、dev 线维护、⑳阶段执行），
> 动作方须**先写声明、窗后撤声明**；我方首周观测脚本据此把「声明窗内的 daemon 缺席」判为
> **计划内停机**而非供血故障（避免误报 FAIL），**无声明时的缺席仍如实判失败**（不迁就现实）。

## 一、落点与格式

**文件**：`data/daemon_occupancy.json`（**运行态文件，不入库**——各线实时改写，入库必冲突；已在 `.gitignore`）

```json
{
  "occupied": true,
  "window": { "start": "2026-11-08T14:00:00", "end": "2026-11-08T16:00:00" },
  "operation": "停 daemon + 升级运行解释器",
  "owner": "duckdb 升级线（节点⑲）",
  "purpose": "duckdb 1.4.5 → 1.5.6 首跳升级窗口",
  "expected_recovery": "2026-11-08T15:00:00",
  "rollback": "降级 duckdb==1.4.5 + 门禁复核 + schtasks 拉起",
  "note": ""
}
```

- `occupied=false`（或删除文件）= **无进行中声明**（默认态，我方仓库已置此态）；
- `window` 用**本地时区 ISO 8601**（无时区偏移）；窗口内 ⇒ 豁免；**字段缺失 ⇒ 保守豁免**（声明方须补）。

## 二、动作纪律

1. **动作前**：写声明（含时段、操作类型、预期恢复时点、影响面、回退点）⇒ 再执行；
2. **动作中**：声明保持 `occupied=true`；
3. **动作后**：还原为 `occupied=false`（或删除文件），并在窗口记录里写明声明期与实际起止；
4. **跨线协调**：若两个动作方窗口重叠 ⇒ 声明的 `owner`/`operation` 须写清，避免观测归因错乱。

## 三、观测侧消费方

- `scripts/check_duckdb_firstweek_obs.py`（我方 T049 每日观测）——读本文件，声明窗内 `daemon_alive` 判据通过；
- 退出码语义不变：0 全绿 / 1 有判据未达 / 2 采集异常。

## 四、关联

| 用途 | 路径 |
|---|---|
| 观测脚本 | `scripts/check_duckdb_firstweek_obs.py` |
| 实测记录（真伪双验） | `docs/evidence/duckdb-1.5-upgrade-window-evidence.md` §7.9 |
| 我方窗口实施卡（含铁律 19 声明模板） | `docs/handoff/duckdb-window-execution-card-20261030.md` §五 |

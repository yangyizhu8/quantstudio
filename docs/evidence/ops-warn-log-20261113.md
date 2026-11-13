# ops warn 试点·每日扫描台账（2026-11-13 起）

> 试点：B89-U1 甲裁；仅 ops `placeholder_gate="warn"`，`since=2026-11-13T20:56:02+08:00`
> 执行安排：每日 ≥1 次**全 ops 全量** + 条目/检测代码/配置变更后复核
> 窗止：**2026-11-20T20:56:50+08:00**（期满按已批配方仅 ops 回 `off` + 清 `since`，不自动延长、不升 strict）
> 格式：时间 | 配置锚(HEAD / kb-info sha256) | 文件清单 | 档位 | fake/mention/dup | 处置

| 时间 | 配置锚 | 已检清单 | 档位 | 命中 | 处置 |
|---|---|---|---|---|---|
| 2026-11-13T20:56:50+08:00 | `35a2b7c` / `ef2c3dfc…` | 5 件（0001~0005） | warn | fake=0｜mention=0005.contract_row｜dup=0 | 首轮；mention 为引述，仅记录 |
| 2026-11-13T21:00:39+08:00 | `5b86742` / `ef2c3dfc718e` | 5 件（0001.md、0002.md、0003.md、0004.md、0005.md） | warn | fake=0｜mention=OPS-2026-0005.md.contract_row｜dup=0 | 无（mention 为引述，按 §5 仅记录） |

# 通知 v6 · 终审呈报件（2026-11-17）

编制：节点⑲（DRIFT-N19-r16）｜性质：**呈审件**，不新增执行、不改判据逻辑
收件：审核方（终审）｜**下发闸**：`T041` 仍需用户明确批准，临期不延期

---

## 一、本次呈审的两件制品

| 件 | 路径 | bytes | 行 | sha256 |
|---|---|---|---|---|
| 通知 v6 | `docs/handoff/customer-notice-5-duckdb-1.5.6-upgrade-v6-20261115.md` | 121642 | 1837 | `5C60A4DDA2F0E213DA367716782E0D28E0302C3DDF89E1D422051697E85143B3` |
| 终审索引 | `docs/evidence/duckdb-v6-u1-u32-final-review-index-20261116.md` | 27057 | —— | `EB6FE062F9B8BE5CFA75AC324A4E3E9F781E9AA047F93A05CF49FB67B31092C6` |

---

## 二、本轮对通知的实质改动（仅措辞，未碰代码段）

| # | 位置 | 改动 |
|---|---|---|
| 1 | **新增 §2.4.12**（L1682，`## 四` 之前） | 「判据实测状态总表」：逐组列 **设计 / 实测状态 / 客户侧含义**，覆盖 H2（U1–U12 未实测）、H3（U13–U17 未实测）、H4（U24/936 未实测）、**H6（U18 7/7 + U26 8/8 跨轮合并已测；U16/U17 跨环境复制未测）**、H5（62 GB 未跑）、L1（junction 设计拒绝、未实测）；附「三条规定不得反过来读」 |
| 2 | **§六 头条**（L1730） | 「⚠ **不作承诺**」：明文不承诺 62 GB 真实库的耗时与峰值内存；数据仅来自合成库（200 万行 / 59.8 MB / `memory_limit='128MB'`）与设计推理 |

> 索引侧另有摘要勘误 2 处：未测列表由 `U1–U24、U30` 改为 **`U1–U17、U19–U24、U30`**（U18 已证，不再混入）。

---

## 三、硬证：冻结段 6/6 逐字节一致

按**驱动自身抽取算法**（逐行字节偏移 + 切片含行尾 `\n`；行尾实测 LF）在**修订后**通知上复现：

| 段 | 行范围 | bytes | 结果 |
|---|---|---|---|
| F1 `Assert-AbsoluteDrivePath` | L326–L336 | 749 | MATCH |
| F2 `Test-ChainNoReparse` | L338–L361 | 1384 | MATCH |
| COLLECT（`$PY_COLLECT` 体） | L365–L375 | 545 | MATCH |
| RT（`$PY_RT` 体） | L395–L402 | 271 | MATCH |
| PARSERS（8a） | L546–L553 | 723 | MATCH |
| EQATTR（8b+8c） | L558–L578 | 1498 | MATCH |

⇒ 措辞新增**全部落在冻结段之外**，六个内容 pin 未变。

---

## 四、pin 同步与静态核（本轮执行）

### 4.1 pin 同步（三级历史）

| 字段 | 值 | 含义 |
|---|---|---|
| `$KNOWN_NOTICE` | `5C60A4DD…43B3` | **当前**：ZCode 终审补粗体后（2026-11-18 r24），文字未改 |
| `$KNOWN_NOTICE_PREV` | `771D20A4…49D2` | 上一版：L1726 后补「回退 1.4.5 属已过期上游支持」（r22） |
| `$KNOWN_NOTICE_HIST` | `96773A27…1DDE8F` | 史前版：新增 §2.4.12 + §六不作承诺后（r15–r21）；`598741EF…`/`288AB994…` 已移出三槽链，存于证据台账 |

### 4.2 一处**订正**（我方上一轮判断有误）

r15 呈报写「下次运行驱动时 `NOTICE` 阶段会因 pin 不符 **FAIL**」——**该判断错误**。
实读代码：`$KNOWN_NOTICE` 不符**只置告警**（`NOTICE-SHA-DRIFT(仅告警;内容级由 pin 把关)`），**不 throw**；
真正 fail-closed 的是 `EXTRACT` 的六段内容 pin（`throw ('EXTRACT-PIN-<N> 漂移 …')`）。
故 pin 同步的作用是**让审计字段恢复 `MATCHES-KNOWN`**，不是「修一个会让运行失败的缺陷」。

### 4.3 静态核（仅 NOTICE/EXTRACT，plan 空，未触任何断言组）

驱动 `38088` B `722EAF8BA6C5EFEBA99E3902B321E175D36B54FCB57FD0C29EBA93F212F1F99B`；`Parser::ParseFile` **errors=0**（7157 tokens）；
运行根 `duckdb-v6-u18-u26-run-20261117T141023Z`，**rc=0，verdict=PASS**。

```text
notice_sha256    = 96773A27D1D08BFAE64149A2D987871B0382D74BD7B0A2735B913C95DC1DDE8F
notice_sha_prev  = 598741EF4881D71E594FA2A709DD28471FDEE78CC3B09DDF5640503C54859451
notice_sha_note  = MATCHES-KNOWN
scope            = NOTICE
verdict          = PASS

name	rc	note
BOOTSTRAP	0	evDir=D:\miniQMT策略实盘\QuantStudio\docs\evidence\duckdb-v6-u18-u26-run-20261117T141023Z cnData=D:\miniQMT策略实盘\QuantStudio\docs\evidence\duckdb-v6-u18-u26-run-20261117T141023Z\中文 目录\data
NOTICE	0	bytes=121438 lines=1836 
HOST-ID	0	ps=5.1.26100.9711 py=3.11.9 SAME-AS-S0B prefix_ascii=Y real_prefix=C:\Users\Administrator\AppData\Local\Programs\Python\Python311
EXTRACT	0	6 段 pin 全中：F1/F2/COLLECT/RT/PARSERS/EQATTR
EXTRACT-WRITE	0	ps1 x3(bom) py x3 (synth 2015B pin ok)
PARSE	0	3/3 解析通过（各自独立 err）
HASHES	0	6 files, skip_empty=7
```

`notice_sha_note = MATCHES-KNOWN`；`EXTRACT` 六段 pin **全中**；`PARSE` 3/3 通过。

---

### 4.4 r22 文案甲案落位后的重跑（2026-11-18）

上表 4.1–4.3 记载的是 **2026-11-17 r16 那次**（旧驱动 `722EAF8B…`、旧通知 `96773A27…`），**原样保留不改写**。
本节记载文案修订后的**新一次**运行，两者不得混读：

| 项 | 2026-11-17 r16（旧，保留） | 2026-11-18 r22（甲案） | **2026-11-18 r24（ZCode 终审后·当前）** |
|---|---|---|---|
| 通知 | `121438` B / `1836` 行 / `96773A27…` | `121638` B / `1837` 行 / `771D20A4…` | **`121642` B / `1837` 行 / `5C60A4DD…`** |
| 驱动 | `38088` B `722EAF8B…F99B` | `38189` B `5222BBF6…6D17` | **`38211` B `4BF82B85…7835`** |
| 变动 | — | 甲案：L1726 后补一句（+200 B）；三常量顺延；`ParseFile` errors=0 / 7157 tokens | **ZCode 终审：仅补两处 `**` 加粗（+4 B），文字未改** |
| 运行根 | `…run-20261117T141023Z` | `…run-20261118T123850Z` | **`…run-20261118T134528Z`** |
| 回执 | rc=0 / PASS | rc=0 / PASS | **rc=0 / PASS** |
| `notice_sha_note` | MATCHES-KNOWN | MATCHES-KNOWN | **MATCHES-KNOWN** |
| `plan_assertions` | 0 | 0 | **0** |
| `groups` | 0 | 0 | **0** |
| 六段 pin | 6/6 | 6/6（前缀 114352 B + 后缀 7034 B 逐字节一致） | **6/6** |

**变更原因**：业务 owner 2026-11-18 裁定 `§7.62.3` 采纳**甲案**——在通知 L1726（EOL 句）后补一句，点明「自 2026-11-18 起回退到 1.4.5 属已过期上游支持，仅作短期过渡」。

**未做**：未重跑 ENC／U26／U18／旧 15／负控／probe；已收 9／4／7 项实验一字未动；`T041` **不因文案修订自动解除**。

## 五、状态口径（不得误读）

| 项 | 结论 |
|---|---|
| ENC 5 + U26 前 4 | **r12 已测 9/9 PASS**（驱动 `26C905F6…`，根 `…T034521Z`，**整轮 FAIL 永久保留**） |
| U26 后 4（EQ-TRIO/ATTR） | **r13 已测 4/4 PASS**（驱动 `6558B12E…`，根 `…T071754Z`） |
| 合计 13 | **跨轮合并覆盖 13/13**；**非同版一次全绿**（两次驱动 sha 不同） |
| U18 | 7/7 已证 |
| 索引当前分布 | **已核 6 / 部分 2 / 未测 24**（历史阶段 `1/3/28`、`4/2/26` 各自标注） |

---

## 六、已知偏差（随件申报，未修）

| # | 内容 | 影响 |
|---|---|---|
| 1 | `groups` 行注释文本未列 `NOTICE=0` | 仅注释（实际值 0 正确） |
| 2 | r13 那份 `verdict.txt` 的 `groups`/`business_groups` 两行仍是旧文案（r14 改的是驱动字面量，未重跑） | 历史原件不改 |
| 3 | `$KNOWN_NOTICE_HIST` 仅作留痕，不参与任何判定 | 无 |

---

## 七、呈审请求

1. 请对 **§2.4.12 表逐行口径**给出确认（尤其 H6「已测一半 / 未测一半」的切分是否认可）；
2. 请对 **§六「不作承诺」** 措辞给出确认（是否足以免责）；
3. 确认后本会话可出具**下发版**（`T041` 解除仍须用户明确批准，**临期不自动放宽、不延期**）。

---

## 八、边界

零重跑（4／9／13／U18 七／旧 15／probe）✓｜**未做包 B/C** ✓｜未升级 ✓｜未复制环境 ✓｜**未读 62 GB** ✓｜未建 ACL／junction ✓｜未清 flag ✓｜未供血 ✓｜**未下发** ✓｜**未 Git** ✓

`T041` 仍 `BLOCKED`｜11-17 原时间门**不自动放宽、不延期**

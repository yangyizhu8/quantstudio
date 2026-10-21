---
doc: platform-docs-index
title: 平台文档库 · index（MOC）
version: v1.0
updated_at: 2026-10-11
---

# 平台文档库（QuantStudio-trading 全流程管线 · 外部平台参考）· index（MOC）

> **性质**：本库是 **QuantStudio-trading 项目全流程管线（回测 ＋ 模拟盘 ＋ 实盘）** 的**外部平台文档参考层**——收录 TDX（通达信）、QMT（迅投）、PTrade（恒生）等**目标平台**的官方操作与 API 文档快照。
> **与另两库的分工**：`knowledge/` ＝ 双端对齐库（项目自有契约／口径／剧本）｜`review-kb/` ＝ 实盘复盘库（项目自有复盘与规律）｜**本库 ＝ 外部平台事实参考**（第三方内容，**非项目自产规律**）。
> **★ 以实测为准（项目铁律）**：平台官方文档与真实客户端行为**可能不一致**——项目已有 QMT 先例（`iscomplete` 形参、`bar_date` 属性、`count` 取数锚点等与文档不符）⇒ **本库只作参考线索；契约与判据一律以「运行实测」为准**（见主库 `07-经验库`「平台转换铁律-文档签名不可信须建运行实测形态库」）。

## 一、平台分域

| 平台 | 入口 | 来源站点 | 抓取时点 | 页数 |
| --- | --- | --- | --- | --- |
| **TDX（通达信 TdxQuant）** | `tdx/index.md` | `help.tdx.com.cn/quant/docs/markdown/` | 2026-10-12 | **83** |
| **QMT（迅投 XtQuant）** | `qmt/index.md` | `dict.thinktrader.net` | 2026-10-12 | **78**（去 http/https 同页重复后；原始抓取 135） |
| **PTrade（恒生）** | `ptrade/index.md` | `ptradeapi.com`（恒生官方口径） | 2026-10-12 | **11**（站点 8 条死链，逐条见 `ptrade/_meta/sources.md`） |

## 二、入库纪律（要点）

1. **每页须带源 URL ＋ 抓取时点**——逐页登记于各平台 `_meta/sources.md`，清单见 `_meta/crawl-manifest.json`；
2. **正文保持原样**（不改写、不"优化"、不删减）——如需个人批注，另立 `_notes/` 目录，或在页内以 `> **批注**：…` 明确标注（**与原文可区分**）；
3. **本库不写项目结论**——项目结论属 `knowledge/`（对齐契约）或 `review-kb/`（复盘与规律）；
4. **不写凭据**：库内任何文件不得出现 token／密钥／账号明文。

## 三、如何打开（给协作者）

1. Obsidian 打开本目录：`…\QuantStudio-trading\platform-docs`（**vault 根 ＝ 本目录**，不要选仓库根）；
2. 首启 Obsidian 会在本地自建 `platform-docs/.obsidian/`——**不入库**（见 `.gitignore`），各人一份、互不覆盖；
3. 全库为标准 Markdown ＋ YAML frontmatter：VS Code／记事本打开 `index.md` 即可完整阅读并沿链接跳转。

## 四、沿革

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.0 | 2026-10-11 | 建库：定位／三平台分域／入库纪律／如何打开（**批次 1 ＝ TDX**；QMT、PTrade 待批次 2／3） |
| v1.1 | 2026-10-12 | **三平台齐**：TDX 83 ＋ QMT 78 ＋ PTrade 11（批次 2／3 完成）；QMT 去 http/https 同页重复（135→78）；PTrade 8 条站点死链登记；PTrade 2 页含第三方共享凭据已脱敏 |

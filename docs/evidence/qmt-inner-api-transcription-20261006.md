# 验收证据：大 QMT 内置 Python 参考文档集（迅投知识库 innerApi 全量转写 · 以分享链接页面为准）

- **日期**：2026-10-06
- **基准页面（权威）**：`https://dict.thinktrader.net/innerApi/start_now.html?id=70GYeq` 及其全部子页面
- **改动类型**：新增 / 补正文档资产（`docs/qmt/inner-api/`）＋ 验收记录；**未触碰任何框架代码**（引擎 / 注入 API / 数据适配 / 转换管线 / 校验器 / skill 全部零改动）

## 一、结论摘要

| 对比项 | 结果 |
| --- | --- |
| 页面覆盖 | 站点 `/innerApi/` 路由清单 **15/15** 与本地一一对应 |
| 冲突（本地有、页面不同） | **0 处**（逐行集合比对，`-lines: 0`） |
| 缺失（页面有、本地无） | 已补齐：**代码块语言标注 308 处** + **选项卡组标签 190 组** |
| 最终保真度 | 标题 353/353、表格 175/175、代码块 308/308、选项卡 190/190 全绿 |
| 可见文本行覆盖 | **6043 / 6043 = 100%**（页面每一行可见文本都能在本地文档中找到） |

## 二、两轮执行记录

### 第 1 轮：SSR 快照转写（初版）

- 依据站点 SSR HTML + `div.theme-default-content` → `markdownify`，产出 15 页 + 索引。
- 首轮验收：标题 298/298、表格 175/175、路由 15/15。

### 第 2 轮：以分享链接页面为准的对比与补正（本轮）

1. **参数等价性验证**：对 3 个页面分别取「带 `?id=70GYeq`」与「不带参数」的 SSR 内容，`.theme-default-content` 内容 SHA-256 一致 → 该分享参数**不影响页面内容**。
2. **渲染基准切换**：改用 **Playwright/Chromium 客户端渲染 DOM**（即读者真正看到的页面）作为权威基准，逐页与本地转写做集合级 diff。
3. **补正结果**：15 页全部 `-lines: 0`（本地无任何内容为页面所无），新增内容仅两类，均来自页面客户端渲染才出现的元素：
   - 代码块的 **语言标注**（来自站点 `div.language-*`）→ 补进围栏，如 ```` ```python ````；
   - 选项卡（`div.vp-tabs`）的**组标签**（来自 `div.vp-tabs-nav > button`）→ 以 `**选项卡：A ｜ B**` + `**[A]**` 形式保真落地。
4. **写盘方式**：两阶段（先全量抓取成功再落盘），抓取中断即 `ABORT` 不写盘，避免半成品。

## 三、验收标准与结果（最终态）

| # | 验收项 | 标准 | 结果 |
| --- | --- | --- | --- |
| 1 | 覆盖完整性 | 站点路由清单 vs 本地页面 | **15 / 15** |
| 2 | 标题结构 | 页面 h2/h3/h4 数 vs 本地标题数 | **353 / 353**（15 页全绿） |
| 3 | 表格结构 | 页面 `<table>` 数 vs 本地表格数 | **175 / 175**（15 页全绿） |
| 4 | 代码块 | 页面**非空** `<pre>` 数 vs 本地围栏块数 | **308 / 308**（15 页全绿） |
| 5 | 选项卡 | 页面 `vp-tabs` 组数 + 全部标签文本命中 | **190 / 190**，标签零缺失 |
| 6 | 完整性 | 代码围栏配对 / 空文件 | 全部平衡 / 0 空页 |
| 7 | 在线交叉核对 | skill `find-docs`（Context7）抽样 | `passorder`、`get_trade_detail_data` 逐项一致 |
| 8 | 可见文本行覆盖 | 页面 innerText 每一行（≥6 字）都能在本地 md 中命中 | **6043 / 6043 = 100%**（15 页全绿） |

**第 8 项的判定方法**（`tools/verify_coverage.py`）：把页面渲染后的 `innerText` 按行拆开，与本地 md 用**同一套归一化**（去掉 Markdown 语法与空白、保留标点；选项卡标签、复制按钮、列表序号等两侧都归一）后逐行做包含判定——任一行为「页面有、本地无」即判定为**真缺口**。

> 度量迭代说明（诚实记录）：前几轮该指标曾报 32%~79% 的假阴性，逐条定位后确认**全部是指标自身的归一化缺陷**，非内容缺失——① 未去 Markdown 内联标记（反引号）；② 未去 Markdown 链接语法与列表符号；③ 未去本转换自建的选项卡分隔符 `｜`；④ 未忽略 CSS 渲染的列表序号。四类修正后归零，页面侧无一行文本缺失。

验收脚本输出：`docs/evidence/qmt-inner-api-rendered-verify.json`、`docs/evidence/qmt-inner-api-text-coverage-20261006.json`

```json
{ "pages": 15, "headings_ok": 15, "tables_ok": 15, "code_ok": 15, "tabs_ok": 15,
  "src_headings_total": 353, "src_tables_total": 175, "src_pres_total": 308,
  "src_pres_empty_total": 2, "md_fences_total": 308,
  "src_tabgroups_total": 190, "md_tabgroups_total": 190,
  "all_fences_balanced": true, "empty_files": [] }
```

## 四、差异逐项归因

| # | 现象 | 归因 | 处置 |
| --- | --- | --- | --- |
| 1 | 首轮 3 处「标题疑似缺失」（`主图解析`、两处 FAQ 标题） | 页面标题内含 `<strong>` / `<code>` 内联格式，比对归一化不足导致的**假阴性** | 比对器归一化内联标记后判为命中（内容本就在） |
| 2 | `code_examples` 页 `<pre>` 36 个 vs 本地围栏 34 块 | 页面自身有 **2 个空代码块**（`language-text`，`<code>` 内为空） | 判为**页面侧空占位**，非转写遗漏；比对口径改为「非空 `<pre>`」，并如实登记 |
| 3 | 第 2 轮新增的 `python` / 选项卡标签文本 | 站点**仅客户端渲染**的代码块语言标签与 Tab 导航按钮 | 按页面为准**补入**（语言进围栏；标签成 `选项卡：` 组） |
| 4 | 提示 / 警告样式块无颜色语义 | Markdown 无等价容器 | 降级为普通文本行，已在 README 声明 |

## 五、产物清单（精确路径 + 字节数）

| 文件 | 字节 | 说明 |
| --- | --- | --- |
| `docs/qmt/inner-api/README.md` | 7531 | 索引、转换约定、复现与局限 |
| `docs/qmt/inner-api/00-速查索引.md` | 17520 | 数据结构 25 / 枚举常量 25 / API 函数 124 |
| `docs/qmt/inner-api/01-快速开始.md` | 16741 | start_now |
| `docs/qmt/inner-api/02-使用须知.md` | 3697 | user_attention |
| `docs/qmt/inner-api/03-变量约定.md` | 12966 | variable_convention |
| `docs/qmt/inner-api/04-数据结构.md` | 44806 | data_structure |
| `docs/qmt/inner-api/05-枚举常量.md` | 41054 | enum_constants |
| `docs/qmt/inner-api/06-系统函数.md` | 16923 | system_function |
| `docs/qmt/inner-api/07-行情函数.md` | 130028 | data_function |
| `docs/qmt/inner-api/08-交易函数.md` | 73314 | trading_function |
| `docs/qmt/inner-api/09-引用函数.md` | 10495 | quote_function |
| `docs/qmt/inner-api/10-绘图函数.md` | 3995 | drawing_function |
| `docs/qmt/inner-api/11-成交回报实时主推函数.md` | 21285 | callback_function |
| `docs/qmt/inner-api/12-完整示例.md` | 77119 | code_examples |
| `docs/qmt/inner-api/13-相关说明.md` | 9148 | related_instructions |
| `docs/qmt/inner-api/14-常见问题.md` | 25931 | question_answer |
| `docs/qmt/inner-api/15-界面操作.md` | 19262 | interface_operation |
| `docs/qmt/inner-api/tools/fetch_inner_api.py` | 8883 | 渲染抓取 + 转换（`--apply` 落盘；默认只比对） |
| `docs/qmt/inner-api/tools/build_index.py` | 3783 | 速查索引自动抽取 |
| `docs/qmt/inner-api/tools/verify_inner_api.py` | 6783 | 与页面渲染结果逐页核对（结构四指标） |
| `docs/qmt/inner-api/tools/verify_coverage.py` | 5832 | 可见文本行 100% 覆盖核对（本次完整性验收脚本） |

合计 17 个 md、531,815 字节；3 个工具脚本。

## 六、复现命令

```bash
cd docs/qmt/inner-api
python tools/fetch_inner_api.py            # 与页面比对（只报告）
python tools/fetch_inner_api.py --apply    # 以页面为准写入
python tools/build_index.py                # 重建速查索引
python tools/verify_inner_api.py           # 复算结构四指标
python tools/verify_coverage.py            # 复算可见文本行覆盖率（应 100%）
```

## 七、口径与边界

- **权威基准**＝分享链接页面的客户端渲染结果；转换链路**无 LLM 参与**（HTML→Markdown 仅经 BeautifulSoup + markdownify + 确定性正则）。
- 覆盖仅「内置 Python（innerApi）」节；**不含** `nativeApi`（XtQuant/xtdata/xttrader）与「数据字典」节。
- 快照性：内容为 2026-10-06 抓取快照；官方更新后重跑 `fetch_inner_api.py --apply` 即可刷新（脚本自带比对报告）。
- 图片以站点绝对 URL 外链保留，未下载。

## 八、回退条件与方式

- 本改动为**纯新增/纯保真文档**，无框架代码依赖；回退 = 删除 `docs/qmt/inner-api/` 与 `docs/evidence/qmt-inner-api-*`，或 `git revert <commit>`（不改写历史）。
- 判定标准：若发现某页与页面系统性不符（非版本快照导致），整目录重抓或整体 revert，不做局部补丁。
- 本轮写入前已建零副作用回退点：`git stash store` → `01b38ed064ecbf21dacc11bab9d2cac607746b38`。

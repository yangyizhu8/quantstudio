# 大 QMT（迅投）内置 Python 参考文档集

> **覆盖范围**：迅投知识库「内置 Python」整节 —— `https://dict.thinktrader.net/innerApi/` 全部 **15 个页面**。
> **抓取口径**：以分享链接 `…/start_now.html?id=70GYeq` 为准，基于**客户端渲染后的 DOM**（Playwright/Chromium）做确定性 HTML→Markdown 转换，未做改写、摘要或人工润色。
> **抓取日期**：2026-10-06　|　**验收证据**：`docs/evidence/qmt-inner-api-transcription-20261006.md`

## 一、这是什么

大 QMT（迅投 QMT 极速策略交易系统）**内置 Python**（Python 3.6.8，策略运行在客户端进程内）的全部
**数据结构**、**枚举常量**与 **API 函数**离线参考。适用于本地回测策略 → 大 QMT 转换管线开发，
以及任何需要核对内置 Python 函数签名/参数/返回对象字段的场景。

⚠️ **边界**：本目录**不含**外接 SDK（XtQuant / `xtdata` / `xttrader`，属 `nativeApi` 节）与「数据字典」节。
外接 SDK 请查同站点 `/nativeApi/`；也可用 Context7 库 ID `/websites/dict_thinktrader_net` 在线检索。

## 二、文件清单

| 文件 | 内容 | 源页面 |
| --- | --- | --- |
| [00-速查索引.md](00-速查索引.md) | 数据结构 25 项 + 枚举常量 25 项 + API 函数 124 项速查表（自动抽取） | — |
| [01-快速开始.md](01-快速开始.md) | 概述、回测/实盘模型、运行机制对比、逐 K 线示例 | [start_now](https://dict.thinktrader.net/innerApi/start_now.html?id=70GYeq) |
| [02-使用须知.md](02-使用须知.md) | 安装路径、Python 库、ContextInfo 回滚机制、主图/线程模型 | [user_attention](https://dict.thinktrader.net/innerApi/user_attention.html?id=70GYeq) |
| [03-变量约定.md](03-变量约定.md) | 函数命名规则、账号类型、代码表示、mode 模式、ContextInfo 属性 | [variable_convention](https://dict.thinktrader.net/innerApi/variable_convention.html?id=70GYeq) |
| [04-数据结构.md](04-数据结构.md) | **数据结构**：Tick/Bar/L2 行情，Account/Order/Deal/Position 等交易对象字段表 | [data_structure](https://dict.thinktrader.net/innerApi/data_structure.html?id=70GYeq) |
| [05-枚举常量.md](05-枚举常量.md) | **枚举常量**：opType/orderType/prType/volume/quicktrade 取值表 + `enum_*` 状态释义 | [enum_constants](https://dict.thinktrader.net/innerApi/enum_constants.html?id=70GYeq) |
| [06-系统函数.md](06-系统函数.md) | 生命周期（init/handlebar/stop）、定时器、板块与股票池函数 | [system_function](https://dict.thinktrader.net/innerApi/system_function.html?id=70GYeq) |
| [07-行情函数.md](07-行情函数.md) | **行情函数**：`get_market_data_ex`/`get_full_tick`/订阅推送/财务/合约/期权/交易日等 | [data_function](https://dict.thinktrader.net/innerApi/data_function.html?id=70GYeq) |
| [08-交易函数.md](08-交易函数.md) | **交易函数**：`passorder` 及算法下单、撤单/任务、篮子、查询、回测专用下单函数 | [trading_function](https://dict.thinktrader.net/innerApi/trading_function.html?id=70GYeq) |
| [09-引用函数.md](09-引用函数.md) | `ext_data*`、因子取值与 VBA 模型引用 | [quote_function](https://dict.thinktrader.net/innerApi/quote_function.html?id=70GYeq) |
| [10-绘图函数.md](10-绘图函数.md) | `paint`/`draw_text`/`draw_number`/`draw_vertline`/`draw_icon` | [drawing_function](https://dict.thinktrader.net/innerApi/drawing_function.html?id=70GYeq) |
| [11-成交回报实时主推函数.md](11-成交回报实时主推函数.md) | `account/task/order/deal/position/orderError_callback` 等主推回调 | [callback_function](https://dict.thinktrader.net/innerApi/callback_function.html?id=70GYeq) |
| [12-完整示例.md](12-完整示例.md) | 官方完整示例代码（含 L2、下单、查询、回测示例） | [code_examples](https://dict.thinktrader.net/innerApi/code_examples.html?id=70GYeq) |
| [13-相关说明.md](13-相关说明.md) | 手册说明、内置第三方库清单（NumPy/Pandas/TA-Lib 等） | [related_instructions](https://dict.thinktrader.net/innerApi/related_instructions.html?id=70GYeq) |
| [14-常见问题.md](14-常见问题.md) | 官方 FAQ（含 ContextInfo 逐 K 线保存机制、openInt 释义等） | [question_answer](https://dict.thinktrader.net/innerApi/question_answer.html?id=70GYeq) |
| [15-界面操作.md](15-界面操作.md) | 客户端界面/回测配置相关操作说明 | [interface_operation](https://dict.thinktrader.net/innerApi/interface_operation.html?id=70GYeq) |

## 三、转换约定（保真优先）

1. **正文**：站点正文 → Markdown，结构与顺序保持原样，不做术语统一、不做勘误、不重排章节。
2. **代码块**：保留原代码与缩进，并按站点标注补上语言（如 `python`）——该标注来自站点代码块的 `language-*` 类。
3. **选项卡（Tab）**：站点把同一函数下的多个示例/返回值放在选项卡里。转换时保留**选项卡组标签**，形如：

   ```text
   **选项卡：内置python ｜ data1返回值 ｜ …**
   **[内置python]**
   （该选项卡内容）
   ```

4. **提示/警告块**：原样式块降级为普通文本行（Markdown 无颜色语义）。
5. **图片**：以站点绝对 URL 外链保留，未下载（离线阅读时不可见）。

## 四、怎么用

1. **找函数签名/参数**：先查 `00-速查索引.md` 定位函数，再进对应页面看「调用方法 / 参数表 / 返回值 / 示例」。
2. **找返回对象字段**：查 `04-数据结构.md`（如 `get_market_data_ex` 返回的 Tick 字段、`Order`/`Deal` 的 `m_*` 属性）。
3. **找参数取值**：查 `05-枚举常量.md`（`opType`、`orderType`、`prType`、`quicktrade` 的合法值）。
4. **写转换管线/生成器时**：以本目录为离线权威底本；涉及版本/口径变化时用 Context7 在线复核。

## 五、与「先查文档再动手」铁律的衔接

本目录是该铁律的**离线底本**（分享链接快照）；在线复核通道为 skill `find-docs` / Context7 库 ID
`/websites/dict_thinktrader_net`（另有 `/imlida/qmt-docs`）。

```bash
# 在线复核（单主题查询，每问题 ≤3 次）
npx ctx7@latest docs /websites/dict_thinktrader_net "get_trade_detail_data 参数"
```

已做抽样交叉核对（2026-10-06）：`passorder`、`get_trade_detail_data` 的签名/参数取值与 Context7 返回片段逐项一致。

## 六、复现与再抓取

```bash
cd docs/qmt/inner-api
python tools/fetch_inner_api.py            # 抓取比对（只报告，不写盘）
python tools/fetch_inner_api.py --apply    # 以站点为准写入/更新 15 个页面
python tools/build_index.py                # 重建 00-速查索引.md
python tools/verify_inner_api.py           # 与站点渲染结果逐页核对，产出 docs/evidence/*.json
```

依赖：`playwright`（Chromium）+ `requests` + `beautifulsoup4` + `lxml` + `markdownify`。

## 七、已知局限

- 这是 **2026-10-06 快照**；官方更新后需重跑 `tools/fetch_inner_api.py --apply` 刷新。
- 站点在 `code_examples` 页自带 **2 个空代码块**（`language-text`，内容为空），故该页「代码块数」比页面 `<pre>` 数少 2，非转写遗漏。
- 图片外链、提示块降级见第三节。
- 实测 `?id=` 分享参数**不影响页面内容**（带/不带参数的同页内容 SHA 一致），故本快照与直接访问 `/innerApi/*.html` 等价。

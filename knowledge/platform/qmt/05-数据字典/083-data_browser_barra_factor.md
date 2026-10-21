---
title: "# 数据浏览器使用教程"
source_url: "https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html"
crawled_at: 2026-10-12T03:03:15+08:00
platform: QMT
category: 05-数据字典
depth: 3
tags: [平台/QMT, 状态/已抓取]
---

# [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#数据浏览器使用教程) 数据浏览器使用教程

数据浏览器包含清洗过的财务数据、BARRA 因子等高质量数据。

本教程以 BARRA 因子为例，介绍从下载到使用的流程。

## [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#纵览) 纵览

![刷新数据浏览器](https://dict.thinktrader.net/assets/01_刷新数据浏览器-3d3eef1b.png) 当开通数据浏览器权限后，可在行情-数据页面，刷新数据浏览器列表，找到对应你需要的高级数据。

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#代码下载) 代码下载

#### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#示例) 示例

```
from xtquant import xtdata

# 第一步：下载数据浏览器全部表信息（metatable）
xtdata.download_metatable_data()
# 第二步：获取表名
metainfo = xtdata.get_metatable_list()
print(metainfo)
```

#### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#参数说明) 参数说明

无参数

#### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#返回结果) 返回结果

表对应的中文名，与界面对应

```
{'cne1d_100_asset_data': '残差风险表', 'cne1d_100_asset_dlyspecret': '资产残差收益表', 'cne1d_100_asset_exposure_wide': '资产对每个因子的暴露度（宽表）', 'cne1d_100_covariance': '因子协方差矩阵', 'cne1d_100_dlyfacret_wide': '因子日度收益', 'cne1_daily_asset_price': '每日资产收益、收盘价和流通市值', 'cne1_rates': '汇率和无风险收益率'}
```

## [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#下载) 下载

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#界面下载) 界面下载

![下载相关数据](https://dict.thinktrader.net/assets/02_下载数据浏览器数据-2f0984db.png) 鼠标移动到对应的数据，可以触发**下载**按钮，点击进入下载页面，整体下载数据

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#代码下载-1) 代码下载

你也可以使用如下 Python 代码，将指定的高级数据下载到本地。

#### #

```
# 第二步：下载 
k = 'cne1d_100_asset_exposure_wide' # cne1d_100_asset_exposure_wide
# 参数说明对照优化如下
xtdata.download_tabular_data(
    stock_list=['XXXXXX.XX'],     # 股票代码列表
    period=k,                   # 表名（如BARRA因子表）
    start_time='20151001',       # 起始日期（YYYYMMDD）
    end_time='20300101',         # 截止日期（YYYYMMDD）
    incrementally=None,          # 是否增量下载，默认None
    download_type='validatebypage'   # 下载类型，通常为'validatebypage'
)
```

#### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#参数说明-1) 参数说明

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| stock_list | list | 证券代码列表，固定为['XXXXXX.XX']，无需修改 |
| period | str | 数据表名称（如BARRA因子表） |
| start_time | str | 开始日期，格式YYYYMMDD |
| end_time | str | 结束日期，格式YYYYMMDD |
| incrementally | bool/None | 是否增量下载 |
| download_type | str | 下载类型，包含'bypage',按条数下载。'byregion'按时间范围下载。'validationbypage'数据校验按条数下载（注意参数拼写） |
| source | str | 指定下载地址 |

#### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#返回结构) 返回结构

下载无返回

## [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#使用) 使用

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#界面使用) 界面使用

![界面使用](https://dict.thinktrader.net/assets/03_数据浏览器_界面使用-1852204d.png)

鼠标移动到对应数据，可以触发**应用**按钮，点击**应用**，数据以因子指标形式展示到对应品种主图下方。

在指标上点击右键-编辑指标，可以进入因子公式编辑页面， 查看因子公式写法。

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#代码调用) 代码调用

你也可以使用如下 Python 代码，调用指定的数据：

#### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#示例-1) 示例

```
from xtquant import xtdata
k = 'cne1d_100_asset_exposure_wide' # 
df = xtdata.get_tabular_data([k], ['XXXXXX.XX'], period='', start_time='20251201', end_time='', count=-1)

print(df.head())
print(df.columns)
```

#### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#参数说明-2) 参数说明

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| field_list | list | 数据表名列表，从`get_metatable_list` 获取，如 `cne1d_100_asset_exposure_wide` |
| stock_list | list | 数据库列表，固定为['XXXXXX.XX'] |
| period | str | 时间区间类型，默认为空字符串'' |
| start_time | str | 起始日期，格式为'YYYYMMDD' |
| end_time | str | 结束日期，格式为'YYYYMMDD'，可留空 |
| count | int | 返回记录数量，-1 表示返回全部数据 |
| dividend_type | str | 除权类型，默认'none'，除非有特殊需求无需修改 |
| fill_data | bool | 是否补齐空缺数据，默认为True，通常无需修改 |

#### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#返回结构-1) 返回结构

DataFrame 宽表结构，这里建议执行下一步宽表转换为长表，对应数据字典查看。

```
   cne1d_100_asset_exposure_wide.trade_date  ...  cne1d_100_asset_exposure_wide.instrument_id
0                                  20251201  ...                                    000001.SZ
1                                  20251201  ...                                    000002.SZ
2                                  20251201  ...                                    000004.SZ
3                                  20251201  ...                                    000006.SZ
4                                  20251201  ...                                    000007.
```

## [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#表名和对应关系处理) 表名和对应关系处理

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#宽表转换为长表) 宽表转换为长表

代码示例如下：

```
# 1. 预处理：去掉不需要转换的非因子列（如果有 id, seq, timetag 等无关列，建议先剔除）
# 根据你提供的列表，这些列似乎不是因子，不需要 melt 进去：
ignore_cols = [
    'cne1d_100_asset_exposure_wide.id', 
    'cne1d_100_asset_exposure_wide.seq', 
    'cne1d_100_asset_exposure_wide.timetag'
]
# 如果 df 里有这些列，先 drop 掉，防止它们被当成因子
df_clean = df.drop(columns=[c for c in ignore_cols if c in df.columns])

# 2. 重命名“锚点列”（即保持不变的主键列）
# 将宽表里的 id 和 date 映射回长表要求的名字
df_clean = df_clean.rename(columns={
    'cne1d_100_asset_exposure_wide.instrument_id': 'sid',
    'cne1d_100_asset_exposure_wide.trade_date': 'DataDate'
})

# 3. 使用 melt 进行“逆透视”
# id_vars: 不需要动的列（主键）
# var_name: 原来的列名变成新的一列，这一列叫什么？ -> 叫 'Factor'
# value_name: 原来的单元格数值变成新的一列，这一列叫什么？ -> 叫 'Exposure'
df_long = df_clean.melt(
    id_vars=['sid', 'DataDate'], 
    var_name='Factor', 
    value_name='Exposure'
)

# 4. 清洗 Factor 列的内容
# 此时 Factor 列里还是 "cne1d_100_asset_exposure_wide.size" 这种长名字
# 我们需要把前缀去掉，只保留 "size", "beta" 等
prefix = 'cne1d_100_asset_exposure_wide.'
df_long['Factor'] = df_long['Factor'].str.replace(prefix, '', regex=False)

# 5. (可选) 大写转换
# 图片里的因子名是 'MARKET', 'SIZE' (全大写)，而宽表列名通常是小写
df_long['Factor'] = df_long['Factor'].str.upper()

# 6. 调整列顺序以完全匹配图片
df_final = df_long[['sid', 'Factor', 'Exposure', 'DataDate']]

# 查看结果
print(df_final.head())
```

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#返回结构-2) 返回结构

DataFrame 长表结构

```
--- 2. 最终长表 (df_final) ---
          sid   Factor  Exposure  DataDate
0   000001.SZ     BANK      1.00  20231229
1   000001.SZ     BETA      1.20  20231229
2   000001.SZ  FOODBEV      0.00  20231229
3   000001.SZ   MARKET      1.00  20231229
4   000001.SZ     SIZE     -0.56  20231229
5   000002.SZ     BANK      0.00  20231229
6   000002.SZ     BETA      0.90  20231229
...
```

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#对应字典查询) 对应字典查询

(4) CNE1D_100_Asset_Exposure

| 字段 | 格式 | 释义 | 样例 |
| --- | --- | --- | --- |
| sid | varchar | 股票代码 | 000001.SZ |
| Factor | varchar | 因子 | MARKET |
| Exposure | int | 股票对因子暴露度 | 1 |
| DataDate | int | 日期 | 20231229 |

## [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#完整数据字典) 完整数据字典

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#cnextrd-模型解读) CNEXTRD 模型解读

| 模型 | 数据名称 | 描述 | 数据名称 (英文) |
| --- | --- | --- | --- |
| CNEXTRD | 因子协方差矩阵 | 各风险因子之间协方差关系的矩阵，对角线元素为单个因子的方差，非对角线元素为因子间的协方差。用于衡量因子波动的相关性，是计算前瞻性风险和优化投资组合的核心数据。 | Covariance |
|  | 无风险收益率 | 无风险收益率为 SHIBOR（3 个月）的年化收益率 | Rates |
|  | 因子日度收益 | 各风险因子在交易日内的收益率，计算方式为股票收益率对各个因子暴露度回归得到。该表为全量历史表。 | DlyFacRet |
|  | 每日股票收益、收盘价和流通市值 | 个股总收益率，未复权的收盘价，个股总收益用于计算横截面的回归过程。 | Daily_Asset_Price |
|  | 个股残差风险 | 因子模型中无法被系统性因子解释的部分风险，又称非系统性风险或特有风险，用回归残差的标准差衡量。 | Asset_Data |
|  | 个股对每个因子的暴露度 | 全部 A 股（含北交所）的市场、行业、风格因子暴露度。风格因子暴露度为偏离全市场平均水平的标准差，行业因子暴露度为虚拟变量，所有股票在市场因子上暴露度为 1。 | Asset_Exposure |

提供的表格列表如下：

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#数据文件列表汇总) 数据文件列表汇总

| 文件名 | 释义 |
| --- | --- |
| CNE1D_100_Asset_DlySpecRet | 资产残差收益 |
| CNE1_Daily_Asset_Price | 每日资产收益、收盘价和流通市值 |
| CNE1_Rates | 汇率和无风险收益率 |
| CNE1D_100_Asset_Exposure | 资产对每个因子的暴露度 |
| CNE1D_100_DlyFacRet | 因子日度收益 |
| CNE1D_100_Covariance | 因子协方差矩阵 |
| CNE1D_100_Asset_Data | 残差风险 |
| CNE1D_100_ETF_Exposure | ETF 组合对每个因子的暴露度 |
| CNE1D_100_Factors | 因子中英文名称映射表 |

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#_1-cne1d-100-asset-dlyspecret) (1) CNE1D_100_Asset_DlySpecRet

| 字段 | 格式 | 释义 | 样例 |
| --- | --- | --- | --- |
| sid | varchar | 股票代码 | 000001.SZ |
| SpecificReturn | numeric | 残差收益 | -0.431576664 |
| DataDate | int | 日期 | 20231229 |

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#_2-cne1d-daily-asset-price) (2) CNE1D_Daily_Asset_Price

| 字段 | 格式 | 释义 | 样例 |
| --- | --- | --- | --- |
| sid | varchar | 股票代码 | 603088.SH |
| Price | numeric | 收盘价 | 8.62 |
| Capt | numeric | 流通市值 | 373516.9576 |
| PriceSource | varchar | 数据源 | CSC |
| Currency | varchar | 币种 | CNY |
| DlyReturn% | numeric | 日收益率 (单位：%) | 0.466246605 |
| DataDate | int | 日期 | 20231229 |

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#_3-cne1-rates) (3) CNE1_Rates

| 字段 | 格式 | 释义 | 样例 |
| --- | --- | --- | --- |
| !Currency | varchar | 币种 | CNY |
| USDxrate | numeric | 美元汇率 | 6.5 |
| RFRate% | numeric | 无风险收益率 基于 6 个月 shibor 收益率计算 (单位：%) | 2.53 |
| DataDate | int | 日期 | 20231229 |

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#_4-cne1d-100-asset-exposure) (4) CNE1D_100_Asset_Exposure

| 字段 | 格式 | 释义 | 样例 |
| --- | --- | --- | --- |
| sid | varchar | 股票代码 | 000001.SZ |
| Factor | varchar | 因子 | MARKET |
| Exposure | int | 股票对因子暴露度 | 1 |
| DataDate | int | 日期 | 20231229 |

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#_5-cne1d-100-dlyfacret) (5) CNE1D_100_DlyFacRet

| 字段 | 格式 | 释义 | 样例 |
| --- | --- | --- | --- |
| !Factor | varchar | 因子 | MARKET |
| DlyReturn | numeric | 日收益率 | 0.016697795 |
| DataDate | int | 日期 | 20060105 |

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#_6-cne1d-100-covariance) (6) CNE1D_100_Covariance

| 字段 | 格式 | 释义 | 样例 |
| --- | --- | --- | --- |
| !Factor1 | varchar | 因子 1 | AIRLINE |
| Factor2 | varchar | 因子 2 | AGRICULTURE |
| VarCovar | numeric | 协方差 | 26.53 |
| DataDate | int | 日期 | 20220104 |

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#_7-cne1d-100-asset-data) (7) CNE1D_100_Asset_Data

| 字段 | 格式 | 释义 | 样例 |
| --- | --- | --- | --- |
| sid | varchar | 股票代码 | 000001.SZ |
| SpecRisk% | numeric | 残差风险 (单位：%) | 11.09479345 |
| DataDate | int | 日期 | 20231229 |

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#_8-cne1d-100-etf-exposure) (8) CNE1D_100_ETF_Exposure

| 字段 | 格式 | 释义 | 样例 |
| --- | --- | --- | --- |
| sid | varchar | ETF 代码 | 159150.SZ |
| DataDate | int | 日期 | 20231229 |
| Factor | varchar | 因子 | AGRICULTURE |
| Exposure | numeric | ETF 对因子暴露度 | 0.059283241 |

> **注**：以上文件的 return 数值中，若单位标注为%，则以此为单位；否则以绝对数值为单位。

### [#](https://dict.thinktrader.net/dictionary/data_browser_barra_factor.html#cne1d-100-factors-因子中英文名称映射表) CNE1D_100_Factors（因子中英文名称映射表）

| Factor | name | FactorGroup | Chinese_name | Factor_Group_Chinese |
| --- | --- | --- | --- | --- |
| AGR | Agriculture | 2-Industries | 农林牧渔 | 行业 |
| BASCHEM | Chemical | 2-Industries | 基础化工 | 行业 |
| STEEL | Steel | 2-Industries | 钢铁 | 行业 |
| METAL | Nonferrous Metals | 2-Industries | 有色金属 | 行业 |
| ELECTRN | Electronic Engineering | 2-Industries | 电子 | 行业 |
| AUTO | Automobile | 2-Industries | 汽车 | 行业 |
| HOMEAPP | Home Appliances | 2-Industries | 家用电器 | 行业 |
| FOODBEV | Food And Beverage | 2-Industries | 食品饮料 | 行业 |
| TEXTLAPP | Textiles & Apparel | 2-Industries | 纺织服饰 | 行业 |
| LIGHTIND | Light Manufacturing | 2-Industries | 轻工制造 | 行业 |
| PHARMBIO | Pharmaceutical Distribution | 2-Industries | 制药 | 行业 |
| BEAUTYCARE | Health Care | 2-Industries | 医疗器械 | 行业 |
| UTILITY | Public Utility | 2-Industries | 公用事业 | 行业 |
| TRANSPRT | Transportation | 2-Industries | 地面交通 | 行业 |
| REALEST | Real Estate | 2-Industries | 房地产 | 行业 |
| COMMERET | Commercial Trade | 2-Industries | 商贸零售 | 行业 |
| SOCIASER | Social Services | 2-Industries | 社会服务 | 行业 |
| BANK | Bank | 2-Industries | 银行 | 行业 |
| FINAN | Non-banking financials | 2-Industries | 非银 | 行业 |
| CONG | Conglomerates | 2-Industries | 综合 | 行业 |
| BUILDMAT | Building materials | 2-Industries | 建筑材料 | 行业 |
| CONSTDEC | Construction & Decoration | 2-Industries | 建筑装饰 | 行业 |
| ELECEQP | Electrical Equipment | 2-Industries | 电力设备 | 行业 |
| CAPEQ | Capital Equipment | 2-Industries | 机械设备 | 行业 |
| DEFENCE | Defence | 2-Industries | 国防军工 | 行业 |
| COMPUTER | Computer | 2-Industries | 计算机 | 行业 |
| MEDIA | Media | 2-Industries | 传媒 | 行业 |
| COMM | Communications | 2-Industries | 通信 | 行业 |
| COAL | Coal | 2-Industries | 煤炭 | 行业 |
| PETRO | Petroleum and Petrochemical | 2-Industries | 石油石化 | 行业 |
| ENVIRPRO | Environmental Protection | 2-Industries | 环保 | 行业 |
| NE | State-owned Enterprise | 1-Risk Indices | 央国企 | 风格 |
| ANLYSTSN | Analyst Sentiment | 1-Risk Indices | 分析师情绪 | 风格 |
| BETA | Beta | 1-Risk Indices | 贝塔 | 风格 |
| CROWD | Crowd | 1-Risk Indices | 个股拥挤度 | 风格 |
| DIVYILD | Dividend Yield | 1-Risk Indices | 股息率 | 风格 |
| EARNQLTY | Earning Quality | 1-Risk Indices | 盈利质量 | 风格 |
| EARNVAR | Earnings Varibility | 1-Risk Indices | 盈利波动率 | 风格 |
| GROWTH | Growth | 1-Risk Indices | 成长 | 风格 |
| INDMOM | Industry Momentum | 1-Risk Indices | 行业动量 | 风格 |
| INVSQLTY | Investment Quality | 1-Risk Indices | 投资质量 | 风格 |
| LEVERAGE | Leverage | 1-Risk Indices | 杠杆 | 风格 |
| LIQUIDITY | Liquidity | 1-Risk Indices | 流动性 | 风格 |
| LTREVRSL | Long-Term Reversal | 1-Risk Indices | 长期反转 | 风格 |
| MIDCAP | Midcap | 1-Risk Indices | 中盘股 | 风格 |
| MLFAC | Machine Learning | 1-Risk Indices | 机器学习 | 风格 |
| MOMENTUM | Momentum | 1-Risk Indices | 动量 | 风格 |
| Other | Private Enterprise | 1-Risk Indices | 民营企业 | 风格 |
| PROFIT | Profitability | 1-Risk Indices | 盈利能力 | 风格 |
| RESVOL | Residual Volatility | 1-Risk Indices | 残差波动率 | 风格 |
| SEASONALITY | Seasonality | 1-Risk Indices | 股价季节性 | 风格 |
| SIZE | Size | 1-Risk Indices | 大小盘 | 风格 |
| STREVRSL | Short-Term Reversal | 1-Risk Indices | 短期反转 | 风格 |
| VALUE | Value | 1-Risk Indices | 价值 | 风格 |
| MARKET | Market | 5-Market | 市场因子 | 市场 |

上次更新:

邀请注册送VIP优惠券

分享下方的内容给好友、QQ群、微信群,好友注册您即可获得VIP优惠券

玩转qmt,上迅投qmt知识库

登录后获取

[迅投因子](https://dict.thinktrader.net/dictionary/xuntou_factor.html)

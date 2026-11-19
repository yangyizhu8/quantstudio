# U18 / U26 完整静态驱动 · 设计与正反预期（v2 · **本轮不执行**）

编制：2026-11-17（节点⑲，DRIFT-N19-r8）｜v1 见 `_design-and-expectations.v1-20261117.md`（保留存档）

> **本轮边界**：不执行 driver；不复制真实环境；不 ACL／junction／62 GB／生产操作；不重跑已收 15 条。

## 〇、r8 修订了什么（先认错）

| r7 我的写法 | 错在哪 | r8 的改法 |
|---|---|---|
| A5「`encoding='ascii'` 写**中文路径** ⇒ 必抛 `UnicodeEncodeError`」 | **概念混淆**：`encoding` 管的是**内容**编码，路径由 OS 宽字符 API 处理，**目录名含中文不会让 ASCII 写入失败**；且本机真实 `sys.prefix` 是纯 ASCII（见 §1），真实 metadata 写进中文目录**根本不含非 ASCII 内容** | 拆成两组：**A 组 = 真实 metadata × 中文＋空格路径**（只证路径面）；**B 组 = 独立合成载荷 × 明确含中文内容**（只证内容面），并**加一条反向对照** B5：纯 ASCII 内容 + `ascii` 编码 + 中文＋空格路径 ⇒ **应当成功** |
| 用真实 pin 去测 ASCII 失败 | 真实内容纯 ASCII，测不出 | 合成载荷独立成件，**不冒充** env identity；真实 pin 全程不掺伪 |
| 未声明「目录中文 ≠ prefix 内容中文」 | 少了一句关键限定 | driver 落 `real_prefix_ascii` 字段**如实记录**，并写入 verdict |

## 一、为什么两组控制必须分开（r8 的核心）

| 面 | 变量 | 被测对象 | 会不会因中文目录而失败 |
|---|---|---|---|
| **路径面**（A 组） | 路径含 `中文` 与空格 | OS 路径 API + `open()` 路径解析 + 回读 | **不会**——路径编码与 `encoding=` 无关 |
| **内容面**（B 组） | **内容**含非 ASCII | 文本编码器（`ascii`/`utf-8`/`ensure_ascii`） | 与目录**无关**——纯 ASCII 内容写中文路径也不失败（B5 证） |

> 本机真实 `sys.prefix` = `C:\Users\Administrator\AppData\Local\Programs\Python\Python311`，**纯 ASCII**。
> 所以「把真实 metadata 写进中文目录」**只可能**暴露路径面问题；内容面必须另造合成载荷。这两件事不能合成一条断言。
> driver 会实测并把结果落成字段 `real_prefix_ascii = Y/N`——若哪天环境换成中文 prefix，字段变 N，A 组的含义随之改变，**这个事实写在证据里而不是靠记忆**。

## 二、参数

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `-Py` | string | 空＝PATH 上的 python | 指定解释器绝对路径；全部子进程用同一 exe |
| `-KeepFailedScene` | switch | 关 | 失败时保留合成件（默认本就保留，此开关仅作显式意图标记） |

固定常量（驱动内）：`$SIX_FIELDS`（六字段串）、六个内容 pin、`$KNOWN_NOTICE`、`$S0B_PY_SHA`。

## 三、抽取 pin（4 新 + 2 沿用 + 1 合成）

| 段 | 落点 | bytes | sha256 |
|---|---|---|---|
| `PY_COLLECT` | L366..L374 | 545 | `223231A658589E11C1449D465449C7FF62DA07120F35697AA622D710A56DD945` |
| `PY_RT` | L396..L401 | 271 | `C0E57F7C8A47C3FF38D0FB7640F16CE3DD5B365BB4AD8C30EE7D5B828D080A8F` |
| `CHK_PARSERS`（8a） | L546..L552 | 723 | `9545FF90F59F8D7C3E8449051FD5BC14C9B4A2561EA9FC8448E8DC4BB62C82EB` |
| `EQ_ATTR`（8b+8c） | L558..L577 | 1498 | `18AF80E87BB2B67130F7532EA1EE8ECEBF3EA6DDB529CD8A77894F2F09CE6CBE` |
| `Assert-AbsoluteDrivePath`（沿用 r5） | L326..L336 | — | `1DD4DC464640BCDBDB2AAB3F6E85992FB6FDFBCE31D3B276E8DC2F60B0DB234F` |
| `Test-ChainNoReparse`（沿用 r5） | L338..L361 | — | `8B725BA145C6D0EB9EB0796922F604FA9AF86C71272E34C9267FFA84D61C486B` |
| `_synth.template.py`（本地合成器，非通知抽取） | — | 2015 | `D7A906EAD538DEBEDB0FDAB8E54B5F13C841AF61D4F53D4AA7C9392E8E90739F` |

> 七段 pin 任一漂移 ⇒ `EXTRACT-PIN-*` FAIL，**不做任何补测**。

## 四、精确合成输入

设 `RUN = docs\evidence\duckdb-v6-u18-u26-run-<stamp>`（排他创建），`CN = RUN\中文 目录\data`（**3 CJK + 1 空格**）。

### A 组用：真实 metadata（**不掺伪**）

| 件 | 来源 |
|---|---|
| `CN\pypin-<stamp>.json` | `PY_COLLECT` 原样执行；内容 = 本机真实 `sys.version`/`sys.prefix`/`duckdb.__file__`/`duckdb.__version__`/`source_id` |

### B 组用：独立合成载荷（**明确含中文，不冒充 env identity**）

固定载荷（`_synth.template.py` 内 `CN` 常量），六个字段全为合成值且刻意含 CJK：

```text
prefix      = D:\合成\中文前缀
exe         = D:\合成\中文\python.exe
duckdb_file = D:\合成\中文\duckdb\__init__.py
source_id   = 合成SID
```

| 件 | 造法 |
|---|---|
| `CN\synth-cn-utf8.json` | `json.dumps(CN, ensure_ascii=False, sort_keys=True)` → `open('wb')` 写 utf-8 字节 |
| `CN\synth-cn-ensureascii.json` | `ensure_ascii=True` 写同一对象 |
| `CN\synth-cn-asciiwrite.json` | `ensure_ascii=False` 内容 + `open(...,encoding='ascii')` ⇒ **应抛 `UnicodeEncodeError`** |
| `CN\synth-plain-ascii.json` | 内容 `{"a":"b"}` 纯 ASCII + `encoding='ascii'` + 中文空格路径 ⇒ **应成功**（r7 错处的反向对照） |

### C 组用：篡改件（由真实 pin 复制后改，**篡改的是文件字节，不是伪造观测值**）

| 件 | 改法 | 期望副作用 |
|---|---|---|
| `…mut-byte.json` | `python` 值 `3.11.9`→`3.11.8`（1 字节） | FILE≠META，JSON 仍合法 |
| `…mut-pfx.json` | `prefix` 值末尾加 `X` | PFX 变 |
| `…mut-fields.json` | 删 `duckdb_file` 键 | FIELDS 不齐 |

## 五、命令（Hidden）

```powershell
powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass `
  -File <repo>\docs\evidence\duckdb-v6-u18-u26-static-driver-20261117\_driver.candidate.ps1
```

驱动内 python 子进程一律 `Start-Process -WindowStyle Hidden`，`-ArgumentList` **逐参手工加引号**（路径含空格，PS 5.1 不会自动加引号）。

## 六、排他输出与失败留证

| 机制 | 做法 |
|---|---|
| 排他创建 | `New-Item -ItemType Directory` **不带 `-Force`**；已存在则 `-2/-3…` 递增，最多 10 次 |
| 祖先检查 | 逐级向上，任一祖先带 `ReparsePoint` 属性 ⇒ 立即 throw |
| 失败留证 | 顶层 `catch` → `Save-Evidence FAIL <分类>`；分类含 `BOOTSTRAP/EXTRACT/STAGE/CASE/HOST/VERDICT/OTHER` |
| 证据写不出 | `exit 2`（与 FAIL=1 / PASS=0 区分） |
| 未达断言 | `plan` 预登记 20 条；未跑标 `NOT-REACHED` 并计入 `not_reached` |
| 首失败 | 保现场、不改源码、不换 stamp 求绿 |

## 七、预期结果（**3 组 / 20 条判决断言**）

### A 组 · U18 真实 metadata × 中文＋空格路径（7 条）

| # | ID | 判据 | 精确期望 |
|---|---|---|---|
| 1 | `U18-PATH-HAS-CJK-SPACE` | 输出路径实测含 CJK 且含空格 | `CJK=Y,SPACE=Y` |
| 2 | `U18-COLLECT-RC` | `PY_COLLECT` 退出码 | `0` |
| 3 | `U18-FILE-EQ-META` | `FILE-HEX` vs `META-HEX` | 两串相等 |
| 4 | `U18-PFX-EQ` | `PFX_HEX1` vs `PFX_HEX0` | 两串相等 |
| 5 | `U18-FIELDS-EXACT` | `FIELDS` | `duckdb,duckdb_file,exe,prefix,python,source_id` |
| 6 | `U18-PREFIX-ROUNDTRIP` | PS `ReadAllText(UTF8)`+`ConvertFrom-Json` 的 `prefix` vs 独立取得的 `sys.prefix` | **逐字符相等** |
| 7 | `U18-PS-RECOMPUTE-EQ` | PS 对文件字节算 sha256 vs `META-HEX` | 相等（**PS 侧独立复算**，防两侧同源同错） |

### B 组 · 独立合成编码控制（5 条）

| # | ID | 判据 | 精确期望 |
|---|---|---|---|
| 8 | `ENC-ASCII-CONTENT-REJECT` | `ascii` 编码写**含中文内容** | `RC-NZ=Y,UE=Y`（非零退出且 stderr 含 `UnicodeEncodeError`） |
| 9 | `ENC-UTF8-OK-BYTES` | `PY_RT` 报的 `FILE-HEX` vs 合成器内存字节 sha256 | 相等 |
| 10 | `ENC-ENSUREASCII-FILE-DIFF` | `ensure_ascii` 件 `FILE-HEX` vs utf-8 件 `FILE-HEX` | `DIFF=Y`（转义改变了字节） |
| 11 | `ENC-VALUE-PRESERVED` | 两件的 `PFX-HEX`（对**解码后** `prefix` 值取哈希） | 相等（值不变） |
| 12 | `ENC-ASCII-CONTENT-ON-CJK-PATH-OK` | 纯 ASCII 内容 + `ascii` + 中文空格路径 | `RC-ZERO=Y,OK=Y`（**成功**，反证 r7 混淆） |

> 第 10 与第 11 合看才是「FILE 与 PFX 测的不是同一件事」：`ensure_ascii` 让**字节**变、让**值**不变。

### C 组 · U26 留存比较链路（8 条）

| # | ID | 模式 | 判据 | 精确期望 |
|---|---|---|---|---|
| 13 | `U26-POS-TRIPLE` | 正例 | 8a 对原 pin | `threw=False` |
| 14 | `U26-NEG-FILE` | **整链** | META 保原值 + `mut-byte` | 分类 = `FILE` |
| 15 | `U26-NEG-PFX-ISO` | **隔离（试支路）** | META 对齐篡改后 FILE + `mut-pfx` | 分类 = `PFX` |
| 16 | `U26-NEG-FIELDS-ISO` | **隔离（试支路）** | META 对齐 + `mut-fields` | 分类 = `FIELDS` |
| 17 | `U26-EQ-TRIO-NEG` | 8b | `python` 改 `3.11.8` | 分类 = `TRIO` |
| 18 | `U26-EQ-TRIO-POS` | 8b | 三值相同 | `threw=False` |
| 19 | `U26-ATTR-POS` | 8c | `$keepRoot` = 真实 `sys.prefix` | `threw=False` |
| 20 | `U26-ATTR-NEG` | 8c | `$keepRoot` = `RUN\假留存根` | 分类 = `ATTR-EXE` |

## 八、分母口径（三者分列，绝不合并）

| 口径 | 值 | 说明 |
|---|---|---|
| 分组数 | **3** | A 路径面 / B 内容面 / C 比较链 |
| 判决断言数 | **20** | `plan` 预登记 = 20，`reached` 必须同为 20 才可能 PASS |
| python 进程 spawn 数 | **16** | `Invoke-Py` 14 次 + HOST-ID `-c` 探测 2 次；`py_calls` 字段统计此值 |
| 抽取段数 | **7** | 4 新 + 2 沿用 + 1 合成器 |

> 三者是不同量纲：**断言数不等于进程数**，进程数不等于分组数。verdict 里分字段写，不求和。

## 九、U26 隔离模式的定性（r8 明确要求，写死）

| 说法 | 是否成立 |
|---|---|
| 隔离模式证明 `PFX`/`FIELDS` 支路**自身有鉴别力** | **成立** |
| 隔离模式证明**生产环境可以绕过 FILE 检查** | **不成立**——隔离模式是**测试期人为对齐 `$CHK_META_HEX`**，生产代码里该变量来自 `PY_COLLECT` 的返回值，**顺序固定为先 FILE 后 PFX，无绕过路径** |

> 驱动在 verdict 内固定落一行：`isolation_note = 隔离模式仅试支路；生产顺序为先 FILE 后 PFX，不存在绕过路径`。
> 这一行**不随运行结果变化**，是定性声明而非读数。

## 十、写域

| 允许写 |
|---|
| `RUN\`（排他）、`RUN\中文 目录\data\*.json`（真实 pin、篡改件、合成件） |
| `RUN\_pathfuncs.ps1` / `_pyid_collect.py` / `_pyid_check.py` / `_chkparse.ps1` / `_eqattr.ps1` / `_synth.py` |
| `RUN\stages.tsv` / `cases.tsv` / `verdict.txt` / `hashes.tsv` / `py-*.{stdout,stderr,rc}.txt` |

| 禁止写 | 理由 |
|---|---|
| `QuantStudio\data\**` | 生产 data 目录；A 组用合成中文目录等价覆盖 |
| `data\quantstudio.db`、62 GB 库、任何 `.wal` | 生产数据 |
| 任何 `pykeep-*`、`site-packages` | 属环境复制 |
| 任何 ACL／junction | 边界明令禁止 |

## 十一、本轮未做

未执行 driver ✓｜未复制真实环境 ✓｜未 ACL／junction ✓｜未触 62 GB／生产库 ✓｜未清 flag ✓｜未恢复供血 ✓｜未下发 ✓｜未提交推送 ✓｜未重跑 15 条 ✓

**索引保持 4 已核 / 2 部分 / 26 未测**；`T041` 仍 `BLOCKED`；11-17 原闸保持，**临期不自动放宽或延期**。

## 十二、待审点

1. A/B 分组是否把 r8 指出的混淆彻底切开（尤其 B5 反向对照是否够）；
2. 20 条是否就是下一轮**全部**执行范围（不顺手扩到其它 24 项）；
3. `real_prefix_ascii` 字段作为**如实记录**而非断言，是否认可（它是事实字段，不参与 PASS/FAIL）；
4. 合成载荷用 `D:\合成\中文前缀` 这类**明显不可能存在**的路径，是否足够清楚地区别于 env identity。

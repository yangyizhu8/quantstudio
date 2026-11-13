# PD-2026-0020 ⓒ C 探针差异核验证据

| 项 | 值 |
|---|---|
| 对照版本 REF | `7b7a69c^`（C 探针加入前模板） |
| 冻结输入 | `output/ptrade_export/tech_etf_mvo_rotation/tech_etf_mvo_rotation_ptrade.py` |
| 策略/配置 | `tech_etf_mvo_rotation` / `daily_eod_1450`, persistence=true, isolation=false |
| REF 产物 sha256 | `49cb89f75ffba92b4e5e9b6d8cfabf1c69ed26983e8baf07f839a19f002250cb` |
| CUR 产物 sha256 | `0eaee67a21bff881368a226ddc7fa3a801d2b529185d9b840530cb8df1f50367` |
| REF 模板 sha256 | `5c87c491a90c8fa132419dd49cf5292ae4ae9d57075b075fb4b72c4b266ea60c` |
| 差异块数 | 1（纯 insert） |
| 新增行数 | 9（全为 C 探针段） |

## BUILD 指纹边界

`_build_tag()` 的 src 内容哈希读取**当前磁盘源码**，不反映 monkey-patch 的旧模板；故两次渲染 BUILD 行必然相同，**BUILD 差异不在本核验判据内**，跨源码状态的 BUILD 差异须在「冻结/跨版本」环节另立判据。

## 结论

持久化开启时，相对参照版本产物**唯一差异为 C 探针段（9 行纯 insert）**，无其他漂移。

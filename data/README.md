# 数据库放置目录

将分发的 `quantstudio.db` 压缩包解压到 **本目录**，使最终路径为：

```
QuantStudio/data/quantstudio.db
```

回测引擎、采集管线、能力探测均从此路径读取（由 `quantstudio/_paths.py`
解析 `config/data_config.json` 的 `path` 字段，相对项目根锚定）。

## 验证数据库就位

```bash
# 在项目根目录运行
python -c "from quantstudio._paths import db_path; print(db_path()); print('存在:', db_path().exists())"
```

应输出 `.../QuantStudio/data/quantstudio.db` 且 `存在: True`。

## 为什么数据库不随 git 一起下载

数据库约 12 GB，远超 GitHub 单文件 100 MB 上限，因此通过压缩包另行分发，
不纳入版本控制（`.gitignore` 排除 `data/*.db`）。

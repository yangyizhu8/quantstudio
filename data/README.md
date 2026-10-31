# 数据库放置目录

本目录用于存放 QuantStudio 的主数据库 `quantstudio.db`（以及随附的两代 QFQ 因子库）。
数据**不再通过手工解压压缩包**交付，请改用 **「数据交付」页签**（GUI 左侧导航）或
`qs-deliver` 命令行：导入会自动完成校验、备份、覆盖与复验，并留下可回滚的备份目录。

## 交付路径（推荐）

1. 打开 GUI → 左侧导航 **数据交付** → 「导入」区选择交付包目录 → 点「开始导入」；
2. 或命令行（同一内核，产物与 GUI 逐位一致）：

```bash
qs-deliver verify --import-dir <交付包目录>     # 只读校验，不写盘
qs-deliver import --import-dir <交付包目录>     # 校验 → 备份 → 覆盖 → 复验
qs-deliver recover --action rollback            # 需要时回滚到导入前
```

导入完成后，最终路径应为：

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

应输出 `.../QuantStudio/data/quantstudio.db` 与 `存在: True`。

## 为什么数据库不随 git 一起下载

主库为 GB 级单文件，远超 git 单文件上限，因此不纳入版本控制（`.gitignore` 排除 `data/*.db`），
改由交付包分发；交付包本身也落在版本控制之外。

## 备份与回滚

每次导入会在本目录旁生成 `backup_import_<时间戳>/`，其中是导入前旧库的**原件**
（同卷 rename，不额外占空间），并附 `RESTORE_README.txt`。
确认新库运行正常前请勿删除；确认无误后可自行删除以释放空间。

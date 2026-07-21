# 更新日志

本项目所有重要变更都记录在此文件。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号采用日期版本（CalVer，`YYYY.MM.DD`），与项目根目录的 `VERSION` 文件保持一致。

## [Unreleased]

### 工程化
- 新增 `CHANGELOG.md`，记录版本变更（借鉴 cockpit-tools）
- 新增 `scripts/sync_version.py`：以 `VERSION` 文件为唯一版本源，同步到 `pyproject.toml`
- 新增 GitHub Actions CI：push/PR 自动跑语法检查、版本一致性校验、单元测试

## [2026.07.21]

### 新增
- 筛选导出支持按通知书名称和实际发文日期匹配，同一份通知书内的名称与日期条件采用“且”关系。

### 修复
- 恢复以发文采集情况计算 Dashboard 主完整度，避免费用字段缺失导致已有发文被误显示为未完成。

### 变更
- 费用资料完整度独立列为辅助指标，不再影响发文采集率和待补发文列表。

## [2026.06.16]

### 新增
- **网络更新检查与一键更新**：Dashboard 启动时及每小时自动检查 GitHub 上的新版本，
  发现新版本时顶部横幅提示，点「立即更新」一键触发（git 优先、HTTP 兜底）
- **更新源国内镜像兜底**：GitHub 原站不可达时，自动回退 jsDelivr / ghproxy / gitmirror，
  解决国内访问 GitHub 不稳定的问题；支持 `RAW_FILE_MIRRORS` 环境变量自定义镜像
- **代理机构字段采集**：发文专利采集时，MITM 自动拦截 `/api/view/gn/sqxx` 提取代理机构、代理人
- **代理机构批量导入**：`import_agency_csv.py` + Dashboard 文件上传，
  支持 CSV/Excel（申请号 + 代理机构），自动识别中英文列名
- **跨机数据同步**：`sync_from_jsonl.py` 从 JSONL 备份导入本地库；Dashboard「从 JSONL 重建 DB」按钮

### 修复
- **数据损坏（P0）**：`PatentsDB.update_fields()` 原会把未传入的列清空为 NULL，
  现改为只更新指定字段；影响代理机构采集、批量导入等所有部分更新场景
- **Dashboard 子进程死锁**：`sync.py pull` 与 `collect_fwxx.py` 在非交互模式下不再调用 `input()`
- **数据库迁移**：`ALTER TABLE` 异常匹配修正为 `duplicate column`，修复重启时迁移报错
- CSV 表头读取时序、`dailijgList` 类型检查、导入统计计数器分离等若干健壮性修复

### 变更
- Dashboard Tab 9 新增「检查更新」「更新系统代码」「无 git 更新」按钮
- Dashboard Tab 7「合并检测日志」改为「从 JSONL 重建 DB」

[Unreleased]: https://github.com/chenxiaomon/cnipa-patent-collector/compare/main...HEAD

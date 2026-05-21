# CNIPA 专利采集系统 - Claude 工作指南

## 项目概述

CNIPA 专利采集系统：自动化采集国知局专利案件状态，含 MITM 代理拦截、浏览器自动化、数据分析和本地 Web Dashboard。

主要数据文件：`data/results/detection_log.jsonl`（9,400+ 条，唯一真相源）

---

## Dashboard 全功能重设计方案（待实现）

### 目标

将当前单页滚动式 Dashboard 重构为 **左侧导航 + 9 个 Tab** 的结构，暴露系统全部 28 个脚本的功能。

### 当前问题
- 7 个维护脚本（validate、analyze、retry_failed、merge、sync）完全未暴露到 UI
- 无历史趋势图，只有当前快照
- 终端日志无颜色区分
- 所有功能堆在一页，操作上下文混乱

### 9 个 Tab 结构

| Tab | 内容 | 状态 |
|-----|------|------|
| 概览 | 5 指标卡片 + 7 天折线图（SVG）+ 快捷操作 + 运行任务 | 改造 |
| 采集控制 | 主流程采集 + 进度条 + 按清单更新 | 改造 |
| 策��管理 | 分组条形图 + 每组 [立即采集] 按钮 + 辅助操作 | 改造 |
| 发文采集 | 完成率进度环（CSS）+ 操作按钮 + 待补采列表 TOP 20 | 改造 |
| 公开查询 | 步骤化布局：代理→浏览器→翻页→导出 | 整理 |
| 数据分析 | 状态分布 + 申请人排行 + 验证 + 分析（新增） | 全新 |
| 数据管理 | 重试管理 + 日志维护 + 多机同步（新增） | 全新 |
| 任务日志 | 多任务列表 + 颜色高亮终端 | 升级 |
| 系统配置 | 坐标配置 + 系统信息（只读） | 整理 |

### 新增后端 API

- `GET /api/analytics` — 按日聚合采集量时序数据（供折线图）
- `GET /api/fwxx-pending` — 待补采发文申请号列表（TOP 50）

### 新增前端能力

1. **Tab 路由**：JS 状态机 + URL hash 锚点（`#overview` 等）
2. **SVG 折线图**：7 天趋势，纯 SVG，无第三方库
3. **CSS 进度环**：`stroke-dasharray` 实现圆环进度
4. **日志着色**：JS 正则匹配 ✓/success→绿，✗/error/fail→红，⚠/warn→黄

### 保持不变

- HTTP 服务器核心逻辑（BaseHTTPRequestHandler）
- JobManager 子进程管理机制
- `build_summary()` 数据聚合逻辑
- 所有现有 API 路由和后端命令映射
- 颜色主题（`--accent: #147a63` 翠绿等）
- 单文件架构（`web_dashboard.py`）

---

## 文件结构说明

```
cnipa-patent-collector/
├── web_dashboard.py          # 本地 Web 控制台（127.0.0.1:8765）
├── main_automation.py        # 核心采集流程
├── collect_fwxx.py           # 发文信息采集
├── update_by_strategy.py     # 策略管理（更新周期计算）
├── start_mitm_proxy.py       # 主 MITM 代理
├── start_mitm_public_search.py # 公开查询代理
├── settings.py               # 集中化路径和配置常量
├── detection_logger.py       # 数据记录和 Excel 导出
├── data/
│   ├── results/detection_log.jsonl  # 主日志（唯一真相源）
│   ├── focus_strategy.json           # 跟踪申请周期配置
│   ├── update_strategy.json          # 全局更新优先级策略
│   └── update_list_*.txt             # 各周期更新清单
```

## 文件管理规则

- `data/config*.json`（坐标）、`data/patent_*_cache.json`：本机专用，不入库
- `data/config_backup_*.json`、`data/results/*.xlsx`：临时产物，不入库
- `data/results/detection_log.jsonl`：主数据，追踪入库
- `/tmp/cnipa_*.png`：截图临时文件，30 秒后自动清理

## 开发约定

- 所有文件路径通过 `settings.py` 中的常量导入，禁止硬编码相对路径
- 原子写入：先写 `.tmp` 再 `os.replace()` 到目标文件
- Dashboard 保持零依赖（仅 Python 标准库）

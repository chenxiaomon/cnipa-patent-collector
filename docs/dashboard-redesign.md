# Dashboard 全功能重设计方案

> ✅ 已于 2026-07 完成并验收（9 个 Tab 全部上线），本文档仅作存档。
> 完成的部分请及时更新「状态」列，避免文档与代码脱节。

## 目标

将当前单页滚动式 Dashboard 重构为 **左侧导航 + 9 个 Tab** 的结构，暴露系统全部 28 个脚本的功能。

## 当前问题
- 7 个维护脚本（validate、analyze、retry_failed、merge、sync）完全未暴露到 UI
- 无历史趋势图，只有当前快照
- 终端日志无颜色区分
- 所有功能堆在一页，操作上下文混乱

## 9 个 Tab 结构

| Tab | 内容 | 状态 |
|-----|------|------|
| 概览 | 5 指标卡片 + 7 天折线图（SVG）+ 快捷操作 + 运行任务 | ✅ 已完成 |
| 采集控制 | 主流程采集 + 进度条 + 按清单更新 | ✅ 已完成 |
| 策略管理 | 分组条形图 + 每组 [立即采集] 按钮 + 辅助操作 | ✅ 已完成 |
| 发文采集 | 完成率进度环（CSS）+ 操作按钮 + 待补采列表 TOP 20 | ✅ 已完成 |
| 公开查询 | 步骤化布局：代理→浏览器→翻页→导出 | ✅ 已完成 |
| 数据分析 | 状态分布 + 申请人排行 + 验证 + 分析（新增） | ✅ 已完成 |
| 数据管理 | 重试管理 + 日志维护 + 多机同步（新增） | ✅ 已完成 |
| 任务日志 | 多任务列表 + 颜色高亮终端 | ✅ 已完成 |
| 系统配置 | 坐标配置 + 系统信息（只读） | ✅ 已完成 |

## 新增后端 API

- `GET /api/analytics` — ⚠️ 合并入 `/api/summary`（`daily_counts`、`status_counts`、`applicant_counts` 字段）
- `GET /api/fwxx-pending` — ⚠️ 合并入 `/api/summary`（`fwxx_pending_list` 字段，TOP 50）

## 新增前端能力

1. **Tab 路由**：JS 状态机 + URL hash 锚点（`#overview` 等）
2. **SVG 折线图**：7 天趋势，纯 SVG，无第三方库
3. **CSS 进度环**：`stroke-dasharray` 实现圆环进度
4. **日志着色**：JS 正则匹配 ✓/success→绿，✗/error/fail→红，⚠/warn→黄

## 保持不变

- HTTP 服务器核心逻辑（BaseHTTPRequestHandler）
- JobManager 子进程管理机制
- `build_summary()` 数据聚合逻辑
- 所有现有 API 路由和后端命令映射
- 颜色主题（`--accent: #147a63` 翠绿等）
- 单文件架构（`web_dashboard.py`）

## 与设计规则的已知摩擦点

> 实现时按 CLAUDE.md「Before acting」第 2 条处理：先指出，再动手。

- 新增 `/api/analytics`、`/api/fwxx-pending` 与 JS 状态机路由属于「新增 API/层」，
  与设计规则第 5 条相关。本任务已确认这些层是为隐藏调用方对数据聚合/路由的认知，
  视为通过；如实现中发现某一层只是透传，停下重新评估。

# AI 上下文（AI Context）

> 📌 **此文件专为 AI/Harness 协作设计**  
> 功能：快速理解项目现状、约束和下一步方向  
> 更新频率：每次重大改动后追加  
> 目标长度：1-2 屏（控制在 500 行以内）

---

## 当前目标（2026-05-10）

**优先级 1 - 工程化基础** (文档定稿中)
- [x] 文档重构（保守版）：project-brief, architecture, domain-rules, runbook, decision-log, ai-context, worklog
  - ✅ 已确认：以 anjianywzt 为采集发文判定条件
  - ℹ️ 无历史数据：成功率、故障实际发生频率待补统计
  - ℹ️ 未确定：决策日期与采集时间/案件状态的关系
- [ ] 代码改进：将主流程改为统一使用 anjianywzt（与补采脚本一致）
- [ ] 状态存储升级：从 JSON 改为 JSONL + 原子写入

**优先级 2 - 代码模块化**
- [ ] 抽象公共模块：browser_service, input_service, cache_service, cnipa_rules
- [ ] 减少 main_automation.py 和 collect_fwxx.py 的代码重复

**优先级 3 - 可靠性**
- [ ] 补充 unit tests（纯函数）：申请号规范化、API 解析、日志合并
- [ ] 增强错误恢复机制

---

## 已知约束

### 核心架构约束

- **采集方式**: MITM 代理 + PyAutoGUI（不能改为纯 DOM）
- **采集对象**: CNIPA 官网 (https://cpquery.cponline.cnipa.gov.cn/)
- **触发条件**: anjianywzt == '驳回等复审请求' 时才采集发文（以 anjianywzt 为准）
- **性能目标**: 500 条目 < 30 分钟（单线程）
- **成功率目标**: ≥ 95%（允许重试补采，当前无历史统计数据）

### 代码约束

- **文件路径不统一** ⚠️
  - main_automation.py 使用 `__file__` 计算（line 40）
  - 其他脚本硬编码 `data/` 路径（如 collect_fwxx.py line 68）
  - 建议：统一 paths.py 或 settings.py

- **业务规则统一** ✅
  - 已确认：falvzt 和 anjianywzt 一般相同，以 **anjianywzt** 为准
  - main_automation.py 宜改为 `anjianywzt` 判定（当前为 falvzt，line 455）
  - collect_fwxx.py 已使用 `anjianywzt` 筛选（line 232）
  - 建议：统一主流程也改为 anjianywzt，保持逻辑一致

- **状态存储 RMW 问题** ⚠️
  - detection_log.json 每次 add_record 都整文件重写（detection_logger.py line 148）
  - 中断会导致 JSON 损坏
  - 建议：改为 JSONL + O_APPEND（原子写入）

### 依赖约束

- undetected-chromedriver (可能版本更新频繁)
- mitmproxy (9.x+)
- PyAutoGUI (鼠标坐标绑定，需手工配置)
- Selenium (4.x)

---

## 最近改动

| 日期 | 改动内容 | 文件 | 影响范围 |
|------|---------|------|---------|
| 2026-05-10 | 文档重构（本轮） | docs/ 新增 | 无代码改动 |
| 2026-XX-XX | [待填] | | |

---

## 当前风险 & 处理计划

| 风险 | 症状 | 优先级 | 处理 |
|------|------|--------|------|
| **falvzt vs anjianywzt** | 主流程用 falvzt，补采用 anjianywzt（一般相同） | 🟡 P1 | 建议统一改为 anjianywzt |
| **JSON 文件 RMW 非原子** | 中断会损坏日志 | 🔴 P0 | 改 JSONL + 文件锁（2 周） |
| **文档草稿审核中** | 成功率、falvzt 定义、决策日期待补充 | 🟡 P1 | 用户补充业务事实后定稿 |
| **代码重复（browser/input 逻辑）** | 维护成本高；bug 修复需多处改 | 🟡 P1 | 模块化（3 周） |
| **路径策略不统一** | 从不同目录启动脚本会失败 | 🟡 P1 | 统一 settings.py（1 周） |
| **缺少单元测试** | 规则变化时易出现回归 | 🟡 P2 | 补充测试（2 周） |

---

## 关键文件速览

### 主流程

| 文件 | 行数 | 关键行 | 改动频率 |
|------|------|--------|---------|
| main_automation.py | 550+ | 40(路径), 62(浏览器), 455(falvzt判定), 398(MITM超时) | 🔴 高 |
| detection_logger.py | 350+ | 148(RMW问题), 250(导出) | 🟡 中 |
| patent_mitm_scraper.py | 380+ | 139(缓存写入), 221(缓存写入) | 🟡 中 |

### 补采脚本

| 文件 | 作用 | 关键问题 |
|------|------|---------|
| collect_fwxx.py | 补采发文信息 | 使用 anjianywzt（与主流程 falvzt 不同） ⚠️ |
| retry_failed_applications.py | 重试失败申请号 | 正常 ✓ |

### 辅助脚本

| 文件 | 作用 | 状态 |
|------|------|------|
| merge_detection_logs.py | 合并多个日志 | 可用 ✓ |
| export_public_search.py | 导出公开查询 | 可选 |
| patent_data_cache.py | 内存缓存（已弃用） | 已弃用 ✓ |

---

## 下一步建议

### 本周（2026-05-10 ~ 2026-05-17）

1. **确认业务规则** (1 天)
   - 验证 CNIPA 文档：falvzt vs anjianywzt 是否等价
   - 对应关系：是 1:1 还是 1:n？

2. **改进状态存储** (3-4 天)
   - 新增 DetectionLoggerV2（JSONL 格式）
   - 保留导出 JSON 的接口
   - 迁移脚本：将旧 JSON 转换为 JSONL

3. **统一配置管理** (1-2 天)
   - 新建 `settings.py` 或 `config.py`
   - 替换所有硬编码路径和超时时间

### 下周（2026-05-17 ~ 2026-05-24）

4. **模块化重构** (5-7 天)
   - 提取 `browser_service.py`：浏览器创建、销毁、健康检查
   - 提取 `input_service.py`：坐标录制、鼠标操作、输入验证
   - 提取 `cache_service.py`：缓存读写、原子操作
   - 提取 `cnipa_rules.py`：申请号规范化、字段判定逻辑

5. **测试覆盖** (3-5 天)
   - 单元测试：申请号规范化、API 解析、日志合并
   - 集成测试：单个申请号完整流程

### 关键决策点

- [ ] 确认 falvzt & anjianywzt（本周）→ 影响 collect_fwxx.py 改动
- [ ] SQLite 迁移 vs JSONL（中期评估）→ 影响数据结构设计
- [ ] 坐标自动识别工具（可选）→ 影响用户体验

---

## 快速参考

### 启动命令

```bash
# 终端 1：启动代理
python start_mitm_proxy.py

# 终端 2：启动主程序
USE_MITM_PROXY=true python main_automation.py

# 测试模式（前 5 条）
USE_MITM_PROXY=true python main_automation.py --test 5

# 重试失败
python retry_failed_applications.py

# 补采发文
python collect_fwxx.py
```

### 文件位置

```
data/
├── search_list.txt           # 申请号输入
├── config.json               # 鼠标坐标
├── patent_cache.json         # MITM 缓存（临时）
├── results/
│   ├── detection_log.json    # ⭐ 主日志
│   └── patents_data.xlsx     # ⭐ 最终报表
```

### 关键变量

| 变量 | 位置 | 当前值 | 调整方式 |
|------|------|--------|---------|
| MITM_TIMEOUT | main_automation.py:398 | 8s | 环境变量 MITM_TIMEOUT |
| DATA_DIR | main_automation.py:40 | ./data | settings.py |
| falvzt 判定 | main_automation.py:455 | '驳回等复审请求' | domain-rules.md |

---

## 协作指南

### 读代码时应该先读

1. **docs/project-brief.md** - 项目目标
2. **docs/architecture.md** - 整体流程和文件职责
3. **docs/domain-rules.md** - 业务规则（特别注意 falvzt vs anjianywzt）
4. **main_automation.py** - 主流程实现

### 改代码时应该

- [ ] 检查 decision-log.md 中相关的技术决策
- [ ] 在 worklog.md 中追加一行记录（日期、目标、做了什么、结果、下一步）
- [ ] 测试 5+ 个申请号，确保不破坏主流程
- [ ] 更新受影响的文档（特别是 architecture.md 和 domain-rules.md）

### 遇到问题时

- 🔍 先查 **docs/runbook.md** → 常见问题排查
- 📋 再查 **decision-log.md** → 理解设计意图
- 🧪 最后查源码 + 单步调试

---

*生成时间*: 2026-05-10  
*有效期*: 直到下一次重大改动  
*下次更新*: 2026-05-17  
*维护人*: @minxiaochen

# AI 上下文（AI Context）

> 📌 **此文件专为 AI/Harness 协作设计**  
> 功能：快速理解项目现状、约束和下一步方向  
> 更新频率：每次重大改动后追加  
> 目标长度：1-2 屏（控制在 500 行以内）

---

## 当前目标（2026-05-11）

**优先级 1 - 工程化基础** (进行中)
- [x] 文档重构（保守版）：project-brief, architecture, domain-rules, runbook, decision-log, ai-context, worklog
  - ✅ 已确认：以 anjianywzt 为采集发文判定条件
  - ✅ 已补充：真实采集数据 9422 条，成功率 99.96%
  - ℹ️ 待完成：决策日期与采集时间/案件状态的关系
- [x] 配置集中化：新建 settings.py，迁移 5 个主要脚本
  - ✅ 已创建 settings.py，统一管理路径、MITM 参数、环境变量
  - ✅ 已迁移：validate_results.py, detection_logger.py, main_automation.py, collect_fwxx.py, patent_mitm_scraper.py
  - ✅ 验证：从任意目录运行脚本均能正确定位数据文件
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
- **性能目标**: 500 条目 < 30 分钟（单线程）✅ 已验证
- **成功率**: 99.96%（9418/9422，按 status_code=200 计）✅ 已验证

### 代码约束

- **文件路径不统一** ⚠️
  - main_automation.py 使用 `__file__` 计算（line 40）
  - 其他脚本硬编码 `data/` 路径（如 collect_fwxx.py line 68）
  - 建议：统一 paths.py 或 settings.py

- **业务规则统一** ✅ 已验证
  - 已确认：falvzt 100% 为 `--`（不可用），anjianywzt 为准
  - 主流程 (main_automation.py) 只采基础字段，不负责发文采集（无需改动）
  - 补采脚本 (collect_fwxx.py line 232) 正确使用 anjianywzt 判定
  - 数据验证：9422 条采集，falvzt 全为 `--`，anjianywzt 有真实分布

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
| **falvzt vs anjianywzt** | 实测：falvzt 全为 `--`（不可用），anjianywzt 为准 | ✅ 完成 | 已验证，代码逻辑一致 |
| **JSON 文件 RMW 非原子** | 中断会损坏日志 | 🔴 P0 | 改 JSONL + 文件锁（2 周） |
| **采集成功率已验证** | 99.96%（9418/9422），文档已更新 | ✅ 完成 | 后续关注缺失的 21 条发文 |
| **代码重复（browser/input 逻辑）** | 维护成本高；bug 修复需多处改 | 🟡 P1 | 模块化（3 周） |
| **路径策略不统一** | 从不同目录启动脚本会失败 | 🟡 P1 | 统一 settings.py（1 周） |
| **缺少单元测试** | 规则变化时易出现回归 | 🟡 P2 | 补充测试（2 周） |

---

## 关键文件速览

### 主流程

| 文件 | 行数 | 关键行 | 改动频率 |
|------|------|--------|---------|
| main_automation.py | 550+ | 40(路径), 62(浏览器), 398(MITM超时) | 🟡 中 |
| detection_logger.py | 350+ | 148(RMW问题), 250(导出) | 🟡 中 |
| patent_mitm_scraper.py | 380+ | 139(缓存写入), 221(缓存写入) | 🟡 中 |

### 补采脚本

| 文件 | 作用 | 关键问题 |
|------|------|---------|
| collect_fwxx.py | 补采发文信息 | 正确使用 anjianywzt 判定 ✓ |
| retry_failed_applications.py | 重试失败申请号 | 正常 ✓ |

### 辅助脚本

| 文件 | 作用 | 状态 |
|------|------|------|
| merge_detection_logs.py | 合并多个日志 | 可用 ✓ |
| export_public_search.py | 导出公开查询 | 可选 |
| patent_data_cache.py | 内存缓存（已弃用） | 已弃用 ✓ |

---

## 已完成任务卡：settings.py 配置集中化

**状态**: ✅ 已完成  
**优先级**: P1  
**完成时间**: 2026-05-11  
**目标**: 统一项目路径、代理端口、超时时间和结果文件位置，减少硬编码，避免从不同目录运行脚本时路径失效。

### 完成内容

- ✅ 新增 `settings.py`（65 行）
  - 集中定义 `BASE_DIR`, `DATA_DIR`, `RESULTS_DIR`, `RAW_RESPONSES_DIR`, `RAW_SEARCHES_DIR`
  - 集中定义 `DETECTION_LOG_FILE`, `PATENTS_EXCEL_FILE`, 所有缓存和配置文件路径
  - 集中定义 `MITM_HOST`, `MITM_PORT`, `MITM_TIMEOUT`, `MITM_POLL_INTERVAL`
  - 支持环境变量覆盖默认值
  - 提供 `get_config_summary()` 和 `verify_paths()` 工具函数
- ✅ 迁移 5 个主要脚本
  - `validate_results.py`: 使用 DETECTION_LOG_FILE, PATENTS_EXCEL_FILE
  - `detection_logger.py`: 使用 DETECTION_LOG_FILE, RESULTS_DIR
  - `main_automation.py`: 使用 CNIPA_URL, SEARCH_LIST_FILE, CONFIG_FILE, MITM_TIMEOUT, PATENT_CACHE_FILE, USE_MITM_PROXY
  - `collect_fwxx.py`: 使用所有配置值，添加 pyautogui 配置
  - `patent_mitm_scraper.py`: 使用 PATENT_CACHE_FILE, PATENT_FWXX_CACHE_FILE, MARKER_FILE, FORCE_UPDATE_FLAG
- ✅ 验收标准全部满足
  - `python3 -m py_compile *.py` 通过
  - `python3 validate_results.py` 通过（99.96% 成功率验证）
  - 从项目根目录和外部目录均能正确运行
  - 补充脚本（export_public_search.py, mitm_addon_public_search.py 等）暂未迁移（范围外）

---

## 下一步建议

### 已完成（2026-05-10 ~ 2026-05-11）

1. ✅ **确认业务规则** 
   - 实测数据验证：falvzt 全为 `--`（不可用），anjianywzt 为准
   - 数据样本：9422 条采集记录

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
| anjianywzt 判定 | collect_fwxx.py:232 | '驳回等复审请求' | domain-rules.md |

---

## 协作指南

### 读代码时应该先读

1. **docs/project-brief.md** - 项目目标
2. **docs/architecture.md** - 整体流程和文件职责
3. **docs/domain-rules.md** - 业务规则（anjianywzt 为判定条件，falvzt 已弃用）
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

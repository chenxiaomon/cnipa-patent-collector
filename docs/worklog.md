# 工作日志（Worklog）

> 📝 **工程协作日志**  
> 每次与 AI/Harness 协作后，追加 5 行左右记录改动内容  
> 帮助后续协作快速理解"发生了什么、为什么改、结果如何"  
> 无需详细（git commit message 够详细），但要点名关键决策

---

## 2026-05-10：文档重构与工程化设计

**目标**: 建立完整的工程化文档体系，减少 AI 协作成本；识别优先级最高的代码问题

**改动**:
- ✅ 新增 7 份文档：project-brief, architecture, domain-rules, runbook, decision-log, ai-context, worklog
- ✅ 梳理数据流、技术决策、约束条件、已知问题
- ✅ 识别 3 个 P0 风险：falvzt/anjianywzt 口径不统一、JSON RMW 非原子、代码重复

**结果**: 
- 创建了标准化的知识库结构
- 明确了优先级最高的 3 个优化点（见 ai-context.md）
- 后续 AI 无需每次都读全仓库，可快速定位改动范围

**下一步**:
- [ ] 下周：确认 falvzt vs anjianywzt 的关系（可能需要登陆 CNIPA 确认）
- [ ] 下周：实现 JSONL 日志存储，替换当前的 JSON RMW 问题
- [ ] 下周：统一 settings.py，修复路径策略不一致

**文档位置**: /docs/

---

## 2026-05-11：配置集中化（settings.py）

**目标**: 统一项目配置管理，减少硬编码路径，支持从任意目录运行脚本

**改动**:
- ✅ 新增 settings.py（65 行）：集中管理所有路径、MITM 参数、环境变量
- ✅ 迁移 5 个主要脚本：validate_results.py, detection_logger.py, main_automation.py, collect_fwxx.py, patent_mitm_scraper.py
- ✅ 移除硬编码 data/ 路径，改用 settings 导入

**结果**:
- `python3 -m py_compile *.py` 通过（所有脚本语法检查合格）
- `python3 validate_results.py` 通过（从项目根目录和 /tmp 目录均成功运行）
- 路径现在相对于 settings.py 位置计算，不依赖当前工作目录
- 支持通过环境变量覆盖 MITM 地址、端口、超时等参数

**下一步**:
- [ ] JSONL 日志存储升级（MITM_TIMEOUT 完全配置化后实施）
- [ ] 代码模块化：提取 browser_service, input_service, cache_service
- [ ] 考虑对补充脚本（export_public_search.py 等）进行迁移

---

## 2026-05-12：环境和工作区收口

**目标**: 统一 Python 环境（3.11）、修复测试体系、清理工作区脏数据、对齐 JSON/Excel

**改动**:
- ✅ 新增 `pyproject.toml` + `uv.lock`：项目正式用 uv 管理依赖，Python 3.11
- ✅ `pyproject.toml` 增加 `[dependency-groups] dev = [pytest>=8.0.0]`，解决测试无法运行问题
- ✅ `requirements.txt` 补充 `pandas` / `openpyxl`（之前漏掉）
- ✅ 删除 3 条因 MITM 未启动产生的测试脏记录（201880002233/234/235），恢复 9422 条
- ✅ 重新导出 `patents_data.xlsx`（9422 条专利主信息 + 10830 条发文）

**结果**:
- `uv run pytest tests/` → 35/35 passed，Python 3.11.15
- `validate_results.py` 通过，JSON/Excel 一致，成功率 99.96%（9418/9422）
- 工作区干净，commit `5bfc542`

**下一步**:
- [ ] JSONL 日志存储（仍是大 JSON 原子替换，高并发/大规模时仍有风险）
- [ ] 补充脚本路径硬编码问题（`update_by_strategy.py`、`sync.py`、`import_from_cache.py`）

---

## 2026-05-12：README 状态修正 + 补采列表准备

**目标**: 修正 README.md 两处过时状态；生成数据缺口的补采列表

**改动**:
- ✅ README.md：JSON RMW 状态改为"🟡 部分修复"（DetectionLogger 已原子替换，JSONL 待做）
- ✅ README.md：路径策略状态改为"✅ 已完成"（settings.py 2026-05-11 完成）
- ✅ 生成 `data/retry_failed.txt`（4 条基础采集失败，待补采）
- ✅ 生成 `data/retry_fwxx.txt`（21 条驳回等复审缺发文，待补采）

**结果**:
- README 与实际代码状态一致
- 补采列表已就绪，补采命令：
  - 基础数据：`USE_MITM_PROXY=true uv run python main_automation.py --update-list data/retry_failed.txt`
  - 发文数据：`USE_MITM_PROXY=true uv run python collect_fwxx.py --input data/retry_fwxx.txt`

**下一步**:
- [ ] 实际运行上述两条补采命令（需要 MITM 代理 + 浏览器登录）
- [ ] 补采完成后重跑 validate_results.py 验证

---

## 2026-05-12：修复 macOS 输入框清空 bug

**目标**: 修复连续采集时 `ctrl+a` 在 macOS 不全选导致申请号拼接的问题

**改动**:
- ✅ `browser_utils.py`：新增 `clear_input_field()`，click → command+a/ctrl+a → backspace
- ✅ `main_automation.py`：替换 3 行清空逻辑 → 调用 `clear_input_field()`
- ✅ `collect_fwxx.py`：同上

**结果**:
- `python -m py_compile` 三个文件全部通过
- 无残留 `ctrl+a` 硬编码
- 补采前置条件满足，可安全执行连续多条采集

**下一步**:
- [ ] 运行补采：`USE_MITM_PROXY=true uv run python main_automation.py --update-list data/retry_failed.txt`
- [ ] 运行发文补采：`USE_MITM_PROXY=true uv run python collect_fwxx.py --input data/retry_fwxx.txt`
- [ ] 补采完成后 validate_results.py 验证

---

## 2026-05-12：JSONL 存储升级

**目标**: 解决 detection_logger.py 的 RMW 非原子写入问题，改为 JSONL 追加写入，中断安全

**改动**:
- ✅ `detection_logger.py` 核心改写：JSONL 追加写入，`add_record()` O_APPEND + fsync
- ✅ `upsert_record()`：读全量 → 更新 → 原子重写（强制更新专用，频率低）
- ✅ 新增 `export_to_json()`：按需生成 JSON 快照（兼容旧格式）
- ✅ `collect_fwxx.py` / `validate_results.py`：改读 JSONL
- ✅ `tests/test_validation.py`：改用 `DetectionLogger` API 加载数据
- ✅ 新增 `migrate_to_jsonl.py`：一次性迁移脚本
- ✅ 执行迁移：`detection_log.jsonl` 9422 条，JSON 保留为备份

**结果**:
- `uv run pytest` → 35/35 passed
- `uv run python validate_results.py` → 成功率 99.98%（9420/9422），发文覆盖 98.50%
- 较上次验证：失败记录从 4 条降至 2 条（2022111108974 / 2022114225683 已成功）
- JSON/Excel 一致性 ✅，commit `ab68d5e`

**下一步**:
- [ ] 补采 2 条基础失败（2021105729516、2025116556932）
- [ ] 补采 21 条缺发文（data/retry_fwxx.txt）
- [ ] 删除旧备份：`rm data/results/detection_log.json`（确认无误后）

---

## 2026-05-13：数据补采收口

**目标**: 补采基础失败记录和缺发文记录，修复 collect_fwxx.py JSONL 兼容问题

**改动**:
- ✅ 补采基础失败：2021105729516 成功，2025116556932 持续超时（2025年申请，CNIPA 暂未公开）
- ✅ 修复 `collect_fwxx.py` 的 `update_detection_log()` 和 `_load_standalone_collected()`：由 `json.load()` 改为 `DetectionLogger` JSONL API
- ✅ 修复输入框清空：改为 click → command+a/ctrl+a → backspace
- ✅ 补采 21 条缺发文，成功写入 16 条（5 条仍缺失，接受现状）

**结果**:
- 成功率：99.99%（9421/9422，1 条 2025 年申请暂不可采）
- 发文覆盖率：98.50% → **99.64%**（1398/1403）
- JSON/Excel 一致 ✅，commit `dbe15c7`

**下一步**:
- [ ] 模块化重构（browser_service, input_service, cache_service）
- [ ] 一键启动脚本 run.sh
- [ ] 补充脚本路径迁移（update_by_strategy.py, sync.py, import_from_cache.py）

---

## 2026-05-13：补充手动/半手动采集能力文档

**目标**: 将 Phase 0 手动采集和 publicSearch 手动/半自动采集补入长期维护入口，避免后续 AI 只看到自动按申请号采集链路

**改动**:
- ✅ `README.md`：新增三种采集模式说明、手动采集命令、公开查询命令
- ✅ `docs/architecture.md`：新增模式 A/B/C 三条采集链路和相关脚本职责
- ✅ `docs/ai-context.md`：新增手动/半手动采集入口速览和快速命令

**结果**:
- 系统功能清单覆盖自动主采集、Phase 0 手动按申请人采集、公开查询手动/半自动采集
- 后续 AI 可从 README 或 ai-context 直接发现 `start_browser_for_phase0.py`、`import_from_cache.py`、`launch_browser_with_proxy.py`、`auto_paginate.py`、`export_public_search.py`

**下一步**:
- [ ] 后续模块化时评估是否把手动采集脚本纳入 `settings.py` 路径迁移范围
- [ ] README 中成功率、JSONL 状态等旧口径可单独做一次文档状态收口

---

## 2026-05-13：README 状态收口

**目标**: 修正 README 中过时的成功率、JSONL 状态、数据文件位置和重试入口，避免入口文档误导后续 AI 或人工操作

**改动**:
- ✅ `README.md`：成功率更新为 99.99%（9421/9422），发文覆盖更新为 99.64%（1398/1403）
- ✅ `README.md`：JSONL 状态改为已完成，`detection_log.jsonl` 标记为主日志
- ✅ `README.md`：重试入口改为 `main_automation.py --update-list data/retry_failed.txt`
- ✅ `README.md`：优化计划表同步为当前状态，补充数据缺口已收口
- ✅ `docs/ai-context.md`：同步成功率、当前优先级和 README 收口状态
- ✅ `docs/architecture.md`：同步 JSONL 日志口径和 `--update-list` 重试入口

**结果**:
- `python3 validate_results.py` 通过
- JSONL/Excel 行数一致，发文列表数一致
- README 与 2026-05-13 数据补采收口状态一致

**下一步**:
- [ ] 模块化重构（browser_service, input_service, cache_service）
- [ ] 一键启动脚本 run.sh
- [ ] 补充脚本路径迁移（update_by_strategy.py, sync.py, import_from_cache.py）

---

## 2026-05-13：接入 coordinate_service.py

**目标**: 将已存在但未跟踪的 coordinate_service.py 接入主流程，消除 main_automation.py 中的重复坐标加载逻辑

**改动**:
- ✅ `coordinate_service.py`：修复语法错误（中文引号嵌套）
- ✅ `main_automation.py`：新增 `from coordinate_service import CoordinateService`
- ✅ `main_automation.py`：删除本地 `load_or_record_positions()` 函数（约 52 行）
- ✅ `main_automation.py`：将调用点改为 `CoordinateService.load_or_record_search_coordinates()`

**结果**:
- `uv run python main_automation.py --help` 正常运行
- `uv run pytest` → 35/35 passed
- `py_compile` 三个文件全部通过

**��一步**:
- [x] collect_fwxx.py 中的重复坐标逻辑替换为 CoordinateService（同日完成）
- [ ] git add + commit

---

## 2026-05-13：browser_service.py 接入

**目标**: 统一 main_automation.py 和 collect_fwxx.py 中重复的浏览器启动/登录逻辑

**改动**:
- ✅ 新增 `browser_service.py`：`BrowserService.launch_and_login(url, page_load_wait)`
- ✅ `main_automation.py`：删除内联启动+登录代码（约 20 行），改为一行调用
- ✅ `collect_fwxx.py`：同上（约 20 行），以 `page_load_wait=3` 保留差异参数
- ✅ 两个文件均移除不再直接使用的 `load_credentials` / `auto_fill_login` 导入

**结果**:
- `uv run pytest` → 35/35 passed
- `py_compile` 三个文件全部通过

**下一步**:
- [ ] input_service.py：PyAutoGUI 鼠标/输入操作抽象

---

## [待填] 

**目标**:  
**改动**:  
**结果**:  
**下一步**:  

---

## 日志格式说明

每条记录包括 5 个要素（共 5-7 行）：

1. **日期 + 简述标题** (第 1 行)
   - 格式: `YYYY-MM-DD: 一句话说改了什么`

2. **目标** (第 2 行)
   - 本次改动的目的是什么
   - 示例: "修复状态存储的原子性问题"

3. **改动** (第 3 行，列表形式)
   - 哪些文件/功能改动了
   - 用 ✅/⚠️/🔴 标记改动的重要程度
   - 示例: ✅ 新增 JSONL 格式支持; ⚠️ 调整 API 超时逻辑

4. **结果** (第 4 行)
   - 改动的成果和验证情况
   - 示例: "采集成功率维持 > 95%, 新增 5 条单元测试"

5. **下一步** (第 5 行，TODO 列表)
   - 遗留的待办项
   - 示例: `[ ] 迁移旧 JSON 到 JSONL; [ ] 补充集成测试`

---

## 快速统计

| 阶段 | 目标 | 状态 |
|------|------|------|
| **阶段 1**（已完成）| 业务链路跑通 | ✅ 2025-12-31 |
| **阶段 2**（进行中）| 工程化优化 | 🔵 2026-05-10~ |
| **阶段 3**（待启动）| 可靠性增强 | ⚪ 2026-06-XX~ |

---

*最后更新*: 2026-05-10  
*下次更新*: 下次改动后追加（或每周综述）

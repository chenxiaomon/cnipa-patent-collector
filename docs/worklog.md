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
- ✅ `browser_utils.py`：新增 `clear_input_field()`，macOS 用 `command+a`，其他平台用 `ctrl+a`
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

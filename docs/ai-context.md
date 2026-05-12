# AI 上下文（AI Context）

> 📌 **此文件专为 AI/Harness 协作设计**  
> 功能：快速理解项目现状、约束和下一步方向  
> 更新频率：每次重大改动后追加  
> 目标长度：1-2 屏（控制在 500 行以内）

---

## 当前目标（2026-05-12 更新）

**优先级 1 - 工程化基础** ✅ 基本完成
- [x] 文档重构：project-brief, architecture, domain-rules, runbook, decision-log, ai-context, worklog
- [x] 配置集中化：settings.py 完成，5 个主要脚本已迁移
- [x] 单元测试：35 个测试，`uv run pytest` 全部通过（Python 3.11）
- [x] 环境统一：pyproject.toml + uv.lock，统一 Python 3.11 环境
- [x] macOS 输入框 bug 修复：browser_utils.clear_input_field() 跨平台
- [x] **状态存储升级**：JSONL 追加写入完成，中断安全（commit ab68d5e）

**优先级 2 - 数据完整性**（待补采）
- [ ] 补采 2 条基础采集失败（2021105729516、2025116556932）
- [ ] 补采 21 条驳回等复审缺发文（data/retry_fwxx.txt 已准备）

**优先级 3 - 代码模块化**
- [ ] 抽象公共模块：browser_service, input_service, cache_service, cnipa_rules
- [ ] 减少 main_automation.py 和 collect_fwxx.py 的代码重复
- [ ] 补充脚本路径迁移：update_by_strategy.py, sync.py, import_from_cache.py

---

## 已知约束

### 核心架构约束

- **采集方式**: MITM 代理 + PyAutoGUI（不能改为纯 DOM）
- **采集对象**: CNIPA 官网 (https://cpquery.cponline.cnipa.gov.cn/)
- **触发条件**: anjianywzt == '驳回等复审请求' 时才采集发文（以 anjianywzt 为准）
- **性能目标**: 500 条目 < 30 分钟（单线程）✅ 已验证
- **成功率**: 99.98%（9420/9422，按 status_code=200 计）✅ 已验证（2026-05-12）

### 代码约束

- **文件路径** ✅ 主要脚本已统一
  - settings.py 集中管理所有路径，5 个主要脚本已迁移
  - ⚠️ 残留：update_by_strategy.py, sync.py, import_from_cache.py 仍有硬编码（非核心，待迁移）

- **业务规则统一** ✅ 已验证
  - 已确认：falvzt 100% 为 `--`（不可用），anjianywzt 为准
  - 主流程 (main_automation.py) 只采基础字段，不负责发文采集（无需改动）
  - 补采脚本 (collect_fwxx.py line 232) 正确使用 anjianywzt 判定
  - 数据验证：9422 条采集，falvzt 全为 `--`，anjianywzt 有真实分布

- **状态存储** ✅ 已升级（2026-05-12）
  - detection_log.jsonl，JSONL 追加写入，O_APPEND + fsync，中断安全
  - detection_log.json 保留为备份，可手动删除

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
| 2026-05-11 | settings.py 配置集中化 | settings.py 新增，5 个脚本迁移 | 路径、MITM 配置统一 |
| 2026-05-11 | 单元测试补充 | tests/ 新增 | 35 个测试，pytest 通过 |
| 2026-05-12 | 环境统一（pyproject.toml + uv.lock） | pyproject.toml, uv.lock 新增 | Python 3.11，pytest dev 依赖 |
| 2026-05-12 | 输入框清空 bug 修复（三击+backspace） | browser_utils.py, main_automation.py, collect_fwxx.py | 三击全选 + backspace 删除，跨平台可靠 |
| 2026-05-12 | README 状态修正 | README.md | 路径策略、JSON RMW 状态更新 |
| 2026-05-12 | 补采列表生成 | data/retry_failed.txt, data/retry_fwxx.txt | 4 条失败 + 21 条缺发文 |
| 2026-05-12 | JSONL 存储升级 | detection_logger.py, collect_fwxx.py, validate_results.py, tests/, migrate_to_jsonl.py | 中断安全，O_APPEND + fsync |

---

## 当前风险 & 处理计划

| 风险 | 症状 | 优先级 | 处理 |
|------|------|--------|------|
| **falvzt vs anjianywzt** | 实测：falvzt 全为 `--`（不可用），anjianywzt 为准 | ✅ 完成 | 已验证，代码逻辑一致 |
| **JSON 文件 RMW 非原子** | 中断会损坏日志 | ✅ 完成 | JSONL 追加写入，add_record() O_APPEND + fsync（2026-05-12） |
| **采集成功率已验证** | 99.96%（9418/9422），文档已更新 | ✅ 完成 | 后续关注缺失的 21 条发文 |
| **输入框清空跨平台不兼容** | macOS 下连续输入申请号时可能残留上次内容 | ✅ 完成 | browser_utils.clear_input_field()，三击全选 + backspace（2026-05-12） |
| **代码重复（browser/input 逻辑）** | 维护成本高；bug 修复需多处改 | 🟡 P1 | 模块化重构（待做） |
| **路径策略不统一** | 从不同目录启动脚本会失败 | ✅ 完成 | settings.py 集中管理（2026-05-11），残留 3 个非核心脚本 |
| **缺少单元测试** | 规则变化时易出现回归 | ✅ 完成 | 35 个测试，uv run pytest 通过（2026-05-12） |
| **数据缺口** | 2 条采集失败 + 21 条缺发文 | 🔴 P0 | 补采列表已准备，待执行补采 |

---

## 关键文件速览

### 主流程

| 文件 | 行数 | 关键行 | 改动频率 |
|------|------|--------|---------|
| main_automation.py | 550+ | 62(浏览器创建), 398(MITM超时) | 🟡 中 |
| detection_logger.py | 280+ | add_record(JSONL追加), export_to_excel, export_to_json | 🟡 中 |
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

## 已完成任务卡：输入框清空跨平台修复

**状态**: ✅ 已完成（2026-05-12，commit 5eb2b37）  
**优先级**: P1  
**发现时间**: 2026-05-12  
**来源**: 实际运行 `USE_MITM_PROXY=true uv run python main_automation.py --update-list data/retry_failed.txt`

### 问题描述

连续查询多个专利申请号时，第二次输入前可能没有清空上一次输入内容，导致新申请号与旧内容拼接，进而查询错误申请号。

### 已知原因

- `main_automation.py` 当前使用 `pyautogui.hotkey('ctrl', 'a')` + `pyautogui.press('delete')` 清空输入框。
- 在 macOS 上，`ctrl+a` 通常不是“全选”，更可能是把光标移动到行首；macOS 全选应使用 `command+a`。
- `collect_fwxx.py` 中也存在同类清空逻辑，修复时需要同步处理。

### 影响范围

- 主采集流程：`main_automation.py`
- 重试/更新列表流程：`main_automation.py --update-list ...`
- 发文补采流程：`collect_fwxx.py`

### 最终实现方案

- `browser_utils.clear_input_field(x, y)`：三击坐标处（全选输入框内容）+ `backspace`（删除）
- `command+a` / `ctrl+a` 快捷键在 Vue.js 输入框中不可靠（Vue 事件拦截），三击是跨平台最稳定方案
- `main_automation.py` 和 `collect_fwxx.py` 均调用 `clear_input_field(input_x, input_y)`，无平台分支逻辑

### 验收标准

- 连续采集两个不同申请号时，第二个申请号不会拼接第一个申请号。
- `--update-list data/retry_failed.txt` 流程可正常连续输入多个申请号。
- `collect_fwxx.py` 中同类输入清空逻辑同步处理。
- 修复后在 `docs/worklog.md` 追加执行记录，写明修改文件、验证命令和实际结果。

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

### 已完成（2026-05-10 ~ 2026-05-12）

1. ✅ **确认业务规则**：falvzt 全为 `--`，anjianywzt 为准，9422 条验证
2. ✅ **统一配置管理**：settings.py 完成，5 个主要脚本迁移
3. ✅ **单元测试**：35 个测试，uv run pytest 全部通过
4. ✅ **环境统一**：pyproject.toml + uv.lock，Python 3.11
5. ✅ **macOS 输入框 bug**：clear_input_field() 跨平台修复

### 当前优先（2026-05-12 起）

1. **补采数据缺口**（最紧迫，列表已就绪）
   - 2 条基础采集失败：`USE_MITM_PROXY=true uv run python main_automation.py --update-list data/retry_failed.txt`
   - 21 条缺发文：`USE_MITM_PROXY=true uv run python collect_fwxx.py --input data/retry_fwxx.txt`

2. ~~**JSONL 存储升级**~~ ✅ 已完成（2026-05-12，commit ab68d5e）

### 后续（2026-05-17 起）

3. **模块化重构**（P1，5-7 天）
   - 提取 `browser_service.py`：浏览器创建、销毁、健康检查
   - 提取 `input_service.py`：坐标录制、鼠标操作
   - 提取 `cache_service.py`：缓存读写、原子操作

4. **运维效率**（P1）
   - 一键启动脚本 run.sh
   - 采集结束自动检测缺口、自动 git push

### 关键决策点

- [x] ~~确认 falvzt & anjianywzt~~ → 已验证，anjianywzt 为准
- [ ] SQLite 迁移 vs JSONL（超过 10K 条时评估）→ 影响数据结构设计
- [ ] 坐标自动识别工具（可选）→ 换机器时不用手动录坐标

---

## 快速参考

### 启动命令

```bash
# 终端 1：启动代理
uv run python start_mitm_proxy.py

# 终端 2：启动主程序
USE_MITM_PROXY=true uv run python main_automation.py

# 测试模式（前 5 条）
USE_MITM_PROXY=true uv run python main_automation.py --test 5

# 补采基础失败
USE_MITM_PROXY=true uv run python main_automation.py --update-list data/retry_failed.txt

# 补采发文
USE_MITM_PROXY=true uv run python collect_fwxx.py --input data/retry_fwxx.txt

# 验证数据
uv run python validate_results.py
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
*最后更新*: 2026-05-12（JSONL 升级完成）  
*下次更新*: 补采完成后  
*维护人*: @minxiaochen

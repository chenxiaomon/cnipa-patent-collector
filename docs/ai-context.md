# AI 上下文（AI Context）

> 历史说明：本文主体记录 2026-05 的工程化过程，数量快照不再代表当前数据。
> 当前运行时唯一真相源是 `data/patents.db`；实时统计以 README 自动区块和 Dashboard 为准。

> 📌 **此文件专为 AI/Harness 协作设计**  
> 功能：快速理解项目现状、约束和下一步方向  
> 更新频率：每次重大改动后追加  
> 目标长度：1-2 屏（控制在 500 行以内）

---

## 当前运行状态（2026-07-11）

- `data/patents.db` 是运行时唯一真相源，所有业务读写通过 `PatentsDB`。
- 部署机是唯一数据 `master`；开发机与 Mac 是 `replica`。
- `data/results/detection_log.jsonl` 只作为 Git 备份与跨机传输载体，由数据库导出刷新。
- replica 使用 `sync_pull_from_master.py` 从 Dashboard HTTP 增量接口拉取数据；旧 `sync.py pull/push` 已禁用。
- 代码发布使用 `release_manifest.json` 逐文件校验，失败自动恢复；`rollback.py` 回滚最近备份。
- 无人值守采集由 `collection_watchdog.py` 监管，replica 通过 `poll_master_alerts.py` 转发 ServerChan 报警。

## 历史目标（2026-05-13）

**优先级 1 - 工程化基础** ✅ 基本完成
- [x] 文档重构：project-brief, architecture, domain-rules, runbook, decision-log, ai-context, worklog
- [x] 配置集中化：settings.py 完成，5 个主要脚本已迁移
- [x] 单元测试：35 个测试，`uv run pytest` 全部通过（Python 3.11）
- [x] 环境统一：pyproject.toml + uv.lock，统一 Python 3.11 环境
- [x] macOS 输入框 bug 修复：browser_utils.clear_input_field() 跨平台
- [x] **状态存储升级**：JSONL 追加写入完成，中断安全（commit ab68d5e）

**优先级 2 - 数据完整性** ✅ 已收口
- [x] 基础采集：9421/9422（99.99%），2025116556932 暂不可采（2025年申请未公开）
- [x] 发文覆盖：**100.00%（1405/1405）**（2026-05-14 新一轮采集后首次达到 100%）

**优先级 3 - 代码模块化** ✅ 全部完成
- [x] coordinate_service.py：坐标加载/记录逻辑统一（commit 3375574，删除 ~169 行）
- [x] browser_service.py：浏览器启动/登录统一（commit a2105f8，删除 ~40 行）
- [x] input_service.py：PyAutoGUI 操作统一（commit 394fa42，删除 ~35 行）
- [x] cache_utils 扩展：新增 clear_cache_key()（commit e686469）
- [x] 补充脚本路径迁移：update_by_strategy.py, sync.py, import_from_cache.py（commit e686469）

---

## 已知约束

### 核心架构约束

- **采集方式**: MITM 代理 + PyAutoGUI（不能改为纯 DOM）
- **采集模式**: 自动按申请号采集 + Phase 0 手动按申请人采集 + 公开查询手动/半自动采集
- **采集对象**: CNIPA 官网 (https://cpquery.cponline.cnipa.gov.cn/)
- **触发条件**: anjianywzt == '驳回等复审请求' 时才采集发文（以 anjianywzt 为准）
- **性能目标**: 500 条目 < 30 分钟（单线程，历史批次已验证）
- **成功率**: 不在本文维护静态快照；以 README 自动统计区块和 Dashboard 为准

### 代码约束

- **文件路径** ✅ 已统一
  - 运行时文件路径由 `settings.py` 集中管理，脚本不依赖当前工作目录

- **业务规则统一** ✅ 已验证
  - 已确认：falvzt 100% 为 `--`（不可用），anjianywzt 为准
  - 主流程 (main_automation.py) 只采基础字段，不负责发文采集（无需改动）
  - 补采脚本 (collect_fwxx.py line 232) 正确使用 anjianywzt 判定
  - 数据验证：9422 条采集，falvzt 全为 `--`，anjianywzt 有真实分布

- **状态存储** ✅ 已升级（2026-07）
  - `patents.db` 是运行时唯一真相源，采集、补采、分析和 Dashboard 均通过 `PatentsDB` 访问。
  - `detection_log.jsonl` 是由数据库生成的只读 Git 备份，运行时禁止直接读取。

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
| 2026-05-12 | 输入框清空 bug 修复 | browser_utils.py, main_automation.py, collect_fwxx.py | command+a/ctrl+a + backspace，跨平台可靠 |
| 2026-05-12 | README 状态修正 | README.md | 路径策略、JSON RMW 状态更新 |
| 2026-05-12 | 补采列表生成 | data/retry_failed.txt, data/retry_fwxx.txt | 4 条失败 + 21 条缺发文 |
| 2026-05-12 | JSONL 存储升级 | detection_logger.py, collect_fwxx.py, validate_results.py, tests/, migrate_to_jsonl.py | 中断安全，O_APPEND + fsync |
| 2026-05-13 | 数据补采收口 | detection_log.jsonl, patents_data.xlsx | 成功率 99.99%，发文覆盖 99.64% |
| 2026-05-13 | 手动/半手动采集文档补全 | README.md, docs/architecture.md, docs/ai-context.md | Phase 0 + publicSearch 入口补齐 |
| 2026-05-13 | README 状态收口 | README.md, docs/worklog.md | 成功率、JSONL、数据位置、重试入口同步 |
| 2026-05-13 | coordinate_service.py 接入主流程 | coordinate_service.py 新增, main_automation.py, collect_fwxx.py | 删除 ~169 行重复坐标逻辑，模块化第一步 |
| 2026-05-13 | browser_service.py 接入 | browser_service.py 新增, main_automation.py, collect_fwxx.py | 删除 ~40 行重复登录逻辑 |
| 2026-05-13 | input_service.py 接入 | input_service.py 新增, main_automation.py, collect_fwxx.py | 删除 ~35 行重复 PyAutoGUI 操作 |
| 2026-05-14 | cache_utils 扩展 + 补充脚本路径迁移 | cache_utils.py, collect_fwxx.py, import_from_cache.py, sync.py, update_by_strategy.py | clear_cache_key()；JSONL 路径统一到 settings |
| 2026-05-14 | 新一轮采集（update_by_strategy 动态更新） | detection_log.jsonl, patents_data.xlsx | 发文覆盖 99.64% → **100.00%**（1405/1405） |

---

## 当前风险 & 处理计划

| 风险 | 症状 | 优先级 | 处理 |
|------|------|--------|------|
| **falvzt vs anjianywzt** | 实测：falvzt 全为 `--`（不可用），anjianywzt 为准 | ✅ 完成 | 已验证，代码逻辑一致 |
| **JSON 文件 RMW 非原子** | 中断会损坏日志 | ✅ 完成 | JSONL 追加写入，add_record() O_APPEND + fsync（2026-05-12） |
| **采集成功率已验证** | 99.99%（9421/9422），文档已更新 | ✅ 完成 | 1 条 2025 年申请暂不可采，5 条缺发文接受现状 |
| **输入框清空跨平台不兼容** | macOS 下连续输入申请号时可能残留上次内容 | ✅ 完成 | click → command+a/ctrl+a → backspace（2026-05-12） |
| **代码重复（browser/input 逻辑）** | 维护成本高；bug 修复需多处改 | ✅ 完成 | coordinate/browser/input_service 三个模块已完成，合计删除 ~244 行（2026-05-13） |
| **路径策略不统一** | 从不同目录启动脚本会失败 | ✅ 完成 | settings.py 集中管理全部脚本（2026-05-14，含 sync/import_from_cache/update_by_strategy） |
| **缺少单元测试** | 规则变化时易出现回归 | ✅ 完成 | 35 个测试，uv run pytest 通过（2026-05-12） |
| **数据缺口** | 1 条不可采（2025年申请）| ✅ 收口 | 成功率 99.99%，发文覆盖 100.00%（2026-05-14） |
| **本机坐标配置入库** | `data/config*.json` 是本机屏幕坐标，提交会误导其他环境 | ✅ 完成 | 已停止跟踪真实坐标，改用 example 模板（2026-05-14，commit 1117e7b） |

---

## 关键文件速览

### 主流程

| 文件 | 行数 | 关键行 | 改动频率 |
|------|------|--------|---------|
| main_automation.py | 360+ | BrowserService/InputService/CoordinateService 调用，MITM 超时 | 🟡 中 |
| db_manager.py | 700+ | PatentsDB、SQLite upsert、聚合、增量导入导出 | 🟡 中 |
| detection_logger.py | 280+ | 通过 PatentsDB 写入、导出 Excel/JSON/JSONL | 🟡 中 |
| patent_mitm_scraper.py | 380+ | 139(缓存写入), 221(缓存写入) | 🟡 中 |
| coordinate_service.py | 137 | load_or_record_search_coordinates, load_or_record_fwxx_coordinates | 🟢 低 |
| browser_service.py | 55 | BrowserService.launch_and_login(url, page_load_wait) | 🟢 低 |
| input_service.py | 60 | InputService.move_and_click(), type_in_search() | 🟢 低 |
| cache_utils.py | 85 | clear_cache_key(), poll_cache_for_key(), normalize_app_no() | 🟢 低 |

### 补采脚本

| 文件 | 作用 | 关键问题 |
|------|------|---------|
| collect_fwxx.py | 补采发文信息 | 正确使用 anjianywzt 判定 ✓ |
| main_automation.py --update-list | 重试/强制更新指定申请号列表 | 正常 ✓ |

### 手动/半手动采集入口

| 文件 | 作用 | 何时使用 |
|------|------|----------|
| start_browser_for_phase0.py | 打开带代理浏览器，用户手动按申请人搜索和翻页 | 已知申请人/关键词，想批量发现申请号 |
| import_from_cache.py | 将 Phase 0 手动浏览产生的 `patent_cache.json` 导入 SQLite | 手动浏览结束后 |
| start_mitm_public_search.py | 启动 publicSearch 专用 MITM 插件 | 公开查询采集前 |
| launch_browser_with_proxy.py | 打开带代理的 publicSearch 浏览器 | 手动输入公开查询条件 |
| auto_paginate.py | 对 publicSearch 页面半自动翻页 | 页数较多时减少手动点击 |
| export_public_search.py | 将 `data/raw_responses/` 导出为 Excel/JSON | 公开查询采集结束后 |

### 辅助脚本

| 文件 | 作用 | 状态 |
|------|------|------|
| merge_detection_logs.py | 合并多个日志 | 可用 ✓ |
| export_public_search.py | 导出公开查询 | 可选 |
| patent_data_cache.py | 内存缓存（已弃用） | 已弃用 ✓ |

---

## 已完成任务卡：本机坐标配置不入库

**状态**: ✅ 已完成（2026-05-14，commit 1117e7b）  
**优先级**: P1  
**发现时间**: 2026-05-14  
**背景**: `data/config.json` 和 `data/config_fwxx.json` 存储的是当前机器的 PyAutoGUI 鼠标坐标。它们会随屏幕分辨率、浏览器位置、缩放比例变化，不适合作为仓库标准配置。

### 完成结果

- ✅ `data/config.json` 和 `data/config_fwxx.json` 已停止 git 跟踪，但本地文件仍保留。
- ✅ `.gitignore` 已忽略真实坐标配置。
- ✅ 新增 `data/config.example.json` 和 `data/config_fwxx.example.json` 作为字段结构模板。
- ✅ example 模板使用 `0` 和占位时间，不包含真实坐标。
- ✅ 未修改采集逻辑，未改 `coordinate_service.py` 行为。

### 问题描述

- 当前 `data/config.json` 和 `data/config_fwxx.json` 已被 git 跟踪。
- 用户运行或重新记录坐标后，这两个文件会频繁产生本机 diff。
- 如果提交真实坐标，其他机器可能直接加载错误坐标，导致点击错位置或采集失败。
- `.gitignore` 当前没有忽略这两个真实配置文件。

### 已执行方案

- 保留用户本地真实坐标文件，不删除本地文件。
- 使用 `git rm --cached data/config.json data/config_fwxx.json` 停止跟踪真实坐标。
- 在 `.gitignore` 中加入：
  - `data/config.json`
  - `data/config_fwxx.json`
- 新增模板文件：
  - `data/config.example.json`
  - `data/config_fwxx.example.json`
- 模板文件只说明字段结构，使用 `0` 值，不包含个人真实坐标。

### 验收标准

- `git status` 中不再显示 `data/config.json` 和 `data/config_fwxx.json` 的修改。
- 仓库中保留 `data/config.example.json` 和 `data/config_fwxx.example.json`。
- `.gitignore` 明确忽略真实坐标配置。
- 本地真实 `data/config.json` 和 `data/config_fwxx.json` 仍存在，不影响当前机器运行。
- 不修改采集逻辑，不改 `coordinate_service.py` 行为。

### 监工审查点

- 确认执行者没有删除用户本地坐标文件。
- 确认执行者没有把真实坐标复制进 example 模板。
- 确认执行者没有顺手改动主程序逻辑。
- 确认提交中只包含 `.gitignore`、example 模板，以及从 git 跟踪中移除真实坐标这类配置收口。

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

- 点击输入框获取焦点 → `command+a`（macOS）/ `ctrl+a`（Linux/Windows）全选 → `backspace` 删除 → 输入新号
- `browser_utils.clear_input_field()`：无需传坐标，在已获焦点的输入框上直接执行
- `main_automation.py` 和 `collect_fwxx.py` 均先 `click()` 再调用 `clear_input_field()`

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
  - `python3 validate_results.py` 通过（最新 99.99% 成功率验证）
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

### 当前优先（2026-05-13 起）

1. ~~**补采数据缺口**~~ ✅ 已收口（2026-05-13）
   - 基础采集：9421/9422，1 条 2025 年申请暂不可采
   - 发文覆盖：1398/1403，5 条残留接受现状

2. ~~**JSONL 存储升级**~~ ✅ 已完成（2026-05-12，commit ab68d5e）

3. ~~**README 状态收口**~~ ✅ 已完成（2026-05-13）
   - 成功率、JSONL 状态、数据文件位置、重试入口已同步

### 已完成（2026-05-14 全部收口）

4. ~~**模块化重构**~~ ✅ 已完成
   - coordinate_service / browser_service / input_service 三个模块全部接入
   - cache_utils 扩展 + 三个辅助脚本路径迁移
   - 合计删除 ~250 行重复代码

5. **运维效率**（可选，暂无紧迫需求）
   - 一键启动脚本 run.sh
   - 采集结束自动检测缺口、自动 git push

### 关键决策点

- [x] ~~确认 falvzt & anjianywzt~~ → 已验证，anjianywzt 为准
- [x] ~~SQLite 迁移 vs JSONL~~ → 已完成；SQLite 为运行时 SSOT，JSONL 为 Git 备份
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

### Phase 0 手动按申请人采集

```bash
# 终端 1：启动主 MITM 代理
uv run python start_mitm_proxy.py

# 终端 2：打开带代理浏览器，手动登录、按申请人搜索、翻页
uv run python start_browser_for_phase0.py

# 浏览完成后：导入缓存到 SQLite 运行库
uv run python import_from_cache.py
```

### 公开查询手动/半自动采集

```bash
# 终端 1：启动 publicSearch 专用 MITM
uv run python start_mitm_public_search.py

# 终端 2：打开带代理 publicSearch 页面，手动输入查询条件
uv run python launch_browser_with_proxy.py

# 可选：自动/半自动翻页
uv run python auto_paginate.py --delay 1.5 --max-pages 50

# 采集完成后导出公开查询结果
uv run python export_public_search.py
```

### 文件位置

```
data/
├── search_list.txt           # 申请号输入
├── config.json               # 鼠标坐标
├── patent_cache.json         # MITM 缓存（临时）
├── patents.db                # ⭐ 运行时唯一真相源
├── machine_role.txt          # master/replica 本机角色（不进 Git）
├── raw_responses/            # 公开查询原始响应
├── raw_searches/             # 公开查询 JSONL 记录
├── results/
│   ├── detection_log.jsonl   # Git 备份，由 PatentsDB 导出刷新
│   ├── detection_log.json    # 兼容导出
│   ├── patents_data.xlsx     # ⭐ 最终报表
│   └── public_search_results.xlsx/json
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
*最后更新*: 2026-05-14（发文覆盖 100%，新一轮采集完成）  
*下次更新*: 有新功能或新一轮采集时  
*维护人*: @minxiaochen

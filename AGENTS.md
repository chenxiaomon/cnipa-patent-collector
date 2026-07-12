# CNIPA 专利采集系统 - Codex 工作指南

## 项目概述

CNIPA 专利采集系统：自动化采集国知局专利案件状态，含 MITM 代理拦截、浏览器自动化、数据分析和本地 Web Dashboard。

主要数据文件：`data/patents.db`（SQLite，运行时唯一真相源）

## 当前任务

进行中的较大任务有独立任务文档，开始相关工作前先阅读对应文档，不要从本文件推断需求：

- （当前无进行中的大任务。已完成任务的存档见 `docs/`，如 `docs/dashboard-redesign.md`）

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
│   ├── patents.db                    # 运行时唯一真相源（所有读写通过 PatentsDB）
│   ├── results/detection_log.jsonl  # 只读 git 备份，每次大批次后由 export_to_jsonl() 刷新
│   ├── focus_strategy.json           # 跟踪申请周期配置
│   ├── update_strategy.json          # 全局更新优先级策略
│   └── update_list_*.txt             # 各周期更新清单
```

## 文件管理规则

- `data/config*.json`（坐标）、`data/patent_*_cache.json`：本机专用，不入库
- `data/config_backup_*.json`、`data/results/*.xlsx`：临时产物，不入库
- `data/patents.db`：主数据，追踪入库（运行时 SSOT，禁止在运行时直接读 JSONL，sync.py 例外）
- `data/results/detection_log.jsonl`：只读 git 备份，由 `PatentsDB.export_to_jsonl()` 刷新
- `/tmp/cnipa_*.png`：截图临时文件，30 秒后自动清理

## 开发约定

- 所有文件路径通过 `settings.py` 中的常量导入，禁止硬编码相对路径
- 原子写入：先写 `.tmp` 再 `os.replace()` 到目标文件
- Dashboard 保持零依赖（仅 Python 标准库）

---

## Before acting
- If the request is ambiguous, state assumptions or ask — don't silently
  pick one reading and build it.
- If a task document (e.g. `docs/dashboard-redesign.md`) conflicts with the
  Design Rules below, do NOT silently pick a side. Flag the conflict and
  explain the tradeoff before writing code.

## When editing existing code
- Change only what the request requires. Don't refactor or restyle working
  code you weren't asked to touch. Match the existing style.

## Design Rules (strict)

Before changing code, check the rules below. If a change would violate one,
stop and explain the smaller redesign first.

These rules constrain NEW names and NEW abstractions. They are not a mandate
to rename existing public symbols. Out of scope: standard-library names
(e.g. `BaseHTTPRequestHandler`) and established project names already in use
(e.g. `JobManager`). Don't rename those just to satisfy a rule.

Do not fix a banned smell by changing its shape: bool → enum/options,
checks → wrappers, flag/switch → Strategy, pass-through layer → facade/adapter.

1. **Names must disambiguate.** Banned defaults for new code: `data`, `info`,
   `result`, `handler`, `manager`, `process`, `utils`, `helper`, `do_*`,
   `*_impl`. Rename to describe the specific thing/action.

2. **Validate once at edges; trust invariants inside.** Do not scatter
   defensive checks across trusted internal boundaries. No repeated
   `if x is None: return` / `if (!ptr) return -1;`. If the same check
   appears 3+ times, redesign the boundary.

3. **Comments document contracts, invariants, rationale, constraints, and
   rejected alternatives.** Do not narrate code or compensate for bad
   names/boundaries.

4. **No mode/flag parameter for a special case.** No bool, enum, string mode,
   or options bag to switch behavior. If variation is real, use separate
   operations owned by the right abstraction. (CLI-level flags exposed to the
   user are out of scope; this rule targets internal abstractions.)

5. **Right owner, complete operation.** Put complexity where the decision,
   invariant, or external dependency lives. Expose complete operations, not
   caller-managed steps. Add no API/layer unless it hides caller knowledge,
   enforces an invariant, or adapts an external dependency. Do not stuff
   unrelated behavior together just to keep the API small.

## Stop signals (redesign, don't push through)
- One change spreads across many files → wrong owner or duplicated
  knowledge, not more patches.
- Naming gets hard, or a comment is explaining around an awkward interface
  → suspect the abstraction boundary before adding more words.
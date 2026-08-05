# 运行手册（Runbook）

## 快速开始

### 0. 环境检查

```bash
# 确保在项目目录
cd ~/Desktop/vibe/cnipa-patent-collector

# 检查 Python 版本（3.8+）
python --version

# 检查必需文件
ls data/search_list.txt  # 申请号列表
ls data/config.json      # 鼠标坐标配置
```

### 1. 安装依赖

```bash
# 新建虚拟环境（推荐）
python -m venv venv
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt  # 如果有 requirements.txt
# 或逐个安装:
pip install undetected-chromedriver selenium pyautogui mitmproxy pandas openpyxl
```

### 2. 准备申请号列表

创建 `data/search_list.txt`，每行一个申请号：

```
CN201880002233
CN201880002234
CN202380004567
```

### 3. 配置鼠标坐标

方法 A：使用坐标记录工具
```bash
# 交互式记录坐标（需要 PyAutoGUI）
python -c "import pyautogui; pyautogui.locateOnScreen"
```

方法 B：手动编辑 `data/config.json`

```json
{
  "input_x": 366,
  "input_y": 242,
  "button_x": 722,
  "button_y": 368,
  "last_updated": "2026-03-26T15:43:04.376435"
}
```

注：实际坐标值需根据你的屏幕分辨率和浏览器位置调整。上面的是示例坐标。

### 3.5 配置管理（可选，高级）

所有项目配置已集中到 `settings.py`，包括：
- **路径**: 数据目录、日志文件、缓存文件位置
- **MITM 参数**: 主机、端口、超时时间
- **环境开关**: USE_MITM_PROXY 等

**查看当前配置**:
```bash
python settings.py
```

**通过环境变量调整**（可选）:
```bash
# 自定义 MITM 端口
MITM_PORT=9090 python main_automation.py

# 自定义超时时间（秒）
MITM_TIMEOUT=10 USE_MITM_PROXY=true python main_automation.py

# 自定义数据目录（不推荐，默认即可）
# 注：DATA_DIR 由 settings.py 自动计算，无需手动设置
```

**常见配置调整**:

| 需求 | 做法 |
|------|------|
| 改变数据存储位置 | 编辑 `settings.py` 的 `DATA_DIR` |
| 改变 MITM 端口 | `MITM_PORT=8888 python start_mitm_proxy.py` |
| 增加 MITM 超时 | `MITM_TIMEOUT=12 USE_MITM_PROXY=true python main_automation.py` |
| 从不同目录运行 | 无需改动，`settings.py` 自动定位 |

### 4. 启动 MITM 代理（终端 1）

```bash
# 进入项目目录
cd ~/Desktop/vibe/cnipa-patent-collector

# 启动 mitmproxy（监听 127.0.0.1:8082）
python start_mitm_proxy.py
```

**预期输出**:
```
[+] 启动 mitmproxy...
[*] 监听地址: 127.0.0.1:8082
[*] 脚本文件: patent_mitm_scraper.py
```

**故障排查**:
- 端口被占用: `lsof -i :8082 | kill -9 <PID>`
- mitmproxy 未安装: `pip install mitmproxy`

### 5. 启动主程序（终端 2）

```bash
# 启用 MITM 代理并运行
USE_MITM_PROXY=true python main_automation.py

# 如果有 requirements.txt
# USE_MITM_PROXY=true python main_automation.py --test 5  # 测试前 5 条
```

**预期输出**:
```
✓ 已加载 100 个申请号
🚀 正在初始化 undetected_chromedriver...
[✓] 浏览器创建成功!
[●] 开始采集: CN201880002233
  [*] 等待 MITM 响应（超时 8s）...
  ✓ 获得专利数据: 一种...
  ✓ 状态: 1, 耗时: 3400ms
```

**浏览器交互**:
- 浏览器自动打开，PyAutoGUI 自动输入搜索框
- **勿手动关闭浏览器**（会中断采集）
- **勿移动鼠标**（可能干扰 PyAutoGUI）
- **勿切换窗口**（某些情况下会导致输入失败）

---

## 多机协作 & 数据同步

部署机是唯一数据 `master`，开发机和 Mac 是 `replica`。每台机器先配置不进 Git 的角色文件：

```bash
# 部署机
echo master > data/machine_role.txt

# 开发机 / Mac
echo replica > data/machine_role.txt
```

### 数据架构

```
部署机 patents.db (master)
        │ Dashboard /api/export/delta?since=...
        ▼
开发机 patents.db (replica) → detection_log.jsonl + README → Git commit → 人工 git push
```

`patents.db` 是各机器的运行时唯一真相源。`detection_log.jsonl` 只作为 Git 备份，运行时禁止直接读取。

### 新机器初始化

```bash
git clone https://github.com/chenxiaomon/cnipa-patent-collector.git
cd cnipa-patent-collector
uv sync --frozen
echo replica > data/machine_role.txt
uv run python sync.py init   # 仅 replica 从 JSONL 备份初始化 SQLite
```

### replica 拉取 master 增量

```bash
cp data/master_sync.example.json data/master_sync.json
# 编辑 master_url 后执行
uv run python sync_pull_from_master.py
```

脚本以 `data/master_sync_state.json` 保存的 master 数据库修改时间为游标，同时拉取新增记录和已有记录更新；成功后合并 SQLite、刷新 JSONL 和 README，并创建一个限定范围的数据提交。旧游标首次升级或 master 地址变化时会做一次全量重对账。最后人工检查并执行：

```bash
git push
```

### 危险操作护栏

`master` 默认拒绝从 JSONL 重建/覆盖数据库。只有明确承担风险时才允许：

```bash
uv run python sync.py rebuild --force --i-know-this-is-master
```

master 的增量导入允许执行，但终端必须先显示输入条数、新申请号、更新已有记录和时间范围，并等待回车确认。Dashboard 导入会先返回同一摘要，再要求二次确认。

### 安全代码更新与回滚

部署机代码更新使用 HTTP 发布清单，永不覆盖 `data/`：

```bash
uv run python fetch_update.py
uv run python rollback.py
```

更新前代码写入 `backups/code_YYYYMMDD_HHMMSS/`，下载文件逐一校验 SHA-256；校验或安装失败会自动恢复，最多保留 5 份备份。

### 无人值守采集与报警

```bash
# 部署机：看门狗启动主采集
uv run python collection_watchdog.py

# replica：轮询部署机报警并转发到 ServerChan
SERVERCHAN_SENDKEY=... uv run python poll_master_alerts.py
```

看门狗检查 10 分钟心跳超时、磁盘空间和连续失败次数；连续重启失败 3 次后停止并写入 `data/alert_status.json`。

### 部署现场验收清单

以下项目必须在部署机、真实网络和手机端完成，单元测试不能替代：

- [ ] 部署机写入 `master`，Dashboard 顶部显示红色 `MASTER 数据主机`。
- [ ] 在 master 执行 `uv run python sync.py rebuild`，确认无危险参数时被拒绝且数据库未变化。
- [ ] 执行 `uv run python normalize_pending_status.py --apply`，确认 NULL 状态全部迁移为 `-1`。
- [ ] 连续两个工作日从 replica 执行 `sync_pull_from_master.py`，确认第二次只拉当天增量，并分别 push 两个数据提交。
- [ ] 发布带 `release_manifest.json` 的代码，执行一次真实 `fetch_update.py`，再用 `rollback.py` 恢复最近备份。
- [ ] 在维护窗口终止受看门狗管理的浏览器进程，确认采集任务被重新启动。
- [ ] 配置 `SERVERCHAN_SENDKEY`，触发测试报警并确认手机实际收到通知。
- [ ] 确认部署机 `data/api_token.txt` 存在且仅当前用户可读，所有写操作无 token 时返回 401。

---

## 常见操作

### 1. 测试模式（采集前 N 条）

```bash
USE_MITM_PROXY=true python main_automation.py --test 5
```

### 2. 从中断点续传

采集过程中按 `Ctrl+C` 中断：

```
^C 捕获中断信号...
[*] 已采集 47 条，保存日志...
```

重新启动时自动跳过已完成采集的申请号（基于 `patents.db`）

```bash
USE_MITM_PROXY=true python main_automation.py
```

### 3. 批量重试失败的申请号

采集完成后，检查失败记录：

```bash
# 查看失败原因分布
uv run python analyze_failures.py

# 生成失败重试清单
uv run python retry_failed.py --write-list

# 按清单重采
USE_MITM_PROXY=true uv run python main_automation.py --update-list data/retry_failed.txt
```

### 4. 补采发文信息

若主流程跳过了发文信息（或采集失败）：

```bash
# 执行补采（从 patents.db 中筛选待补采目标）
USE_MITM_PROXY=true uv run python collect_fwxx.py

# 或仅补采前 5 条（测试模式）
USE_MITM_PROXY=true uv run python collect_fwxx.py --test 5

# 或指定申请号文件
USE_MITM_PROXY=true uv run python collect_fwxx.py --input data/fwxx_list.txt

# 或指定单个申请号
USE_MITM_PROXY=true uv run python collect_fwxx.py --app CN201880002233
```

自动模式只计划 `anjianywzt == '驳回等复审请求'` 且 `fwxx_list IS NULL` 的记录。`fwxx_list=[]` 表示接口明确返回空列表，仍视为发文采集完成；费用字段是否缺失不会扩大这份计划。

### 5. 补采费用信息

费用信息由独立脚本处理，不会附带执行发文采集。费用采集范围由**导入的费用数据集**决定（与案件状态无关）：

```bash
# 第一步：导入费用数据集（CSV/Excel 含申请号列，导入即整表替换；--dry 预览）
uv run python import_fee_targets.py 名单.xlsx

# 执行采集（目标 = 数据集内费用未采齐的已建档申请号）
USE_MITM_PROXY=true uv run python collect_fees.py

# 强制重采整个数据集（费用会变化时手动刷新）
USE_MITM_PROXY=true uv run python collect_fees.py --force

# 或仅补采前 5 条（测试模式）
USE_MITM_PROXY=true uv run python collect_fees.py --test 5

# 或指定申请号文件
USE_MITM_PROXY=true uv run python collect_fees.py --input data/fwxx_list.txt

# 或指定单个申请号
USE_MITM_PROXY=true uv run python collect_fees.py --app CN201880002233
```

自动模式只计划数据集内 `payable_fee_records`、`paid_fee_records`、`fee_receipt_dispatch_records` 任一仍为 `NULL` 的已建档记录；数据集为空时会打印导入指引后退出。三个必需列表都非 `NULL` 即视为费用采集完成，明确返回的 `[]` 算完成；`late_fee_schedule_records` 可能不由接口返回，不作为完成条件。费用字段写入不会刷新基础状态的通用 `timestamp`。数据集内主库无记录的申请号计为「未建档」，需先跑主采集建档；可在 Dashboard 费用面板点【未建档转入主采集清单】一键加入 `data/search_list.txt`。

发文和费用任务共用桌面、浏览器坐标以及当前申请号标记，不可同时运行。两个脚本在整个采集周期内都会持有同一个跨进程文件锁；无论从 Dashboard 还是命令行启动，第二个发文/费用任务都会在打开或控制浏览器前报桌面占用并退出，不需要仅靠人工协调。Dashboard 还会拒绝其管理的其他桌面任务冲突；直接运行未接入该锁的其他 CLI 桌面脚本时，仍应避免同时操作桌面。

### 6. 导出 Excel 报表

采集完成后自动导出，若需要重新导出：

```bash
python -c "from detection_logger import DetectionLogger; logger = DetectionLogger(); logger.export_to_excel()"
```

---

## 故障排查

> ℹ️ **说明**: 以下故障是代码逻辑推导和假设场景，实际遇到频率和解决有效性需验证。

### ❌ 浏览器创建失败 [🧪 代码支持，待实测]

**症状**: `[❌] 浏览器初始化失败` 或 `undetected_chromedriver` 错误

**代码支持**: 存在 `create_driver_with_retry(max_retries=3)` 重试机制

**原因（推测）**:
- ChromeDriver 与 Chrome 版本不匹配
- Chrome 浏览器未安装
- 沙盒限制（某些 Linux/Docker 环境）

**解决**:
```bash
# 强制升级 undetected-chromedriver
pip install --upgrade undetected-chromedriver

# 或指定 Chrome 路径
export CHROME_BIN=/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome
python main_automation.py
```

### ❌ MITM 代理无法连接 [🧪 代码支持，待实测]

**症状**: 采集卡在"等待 MITM 响应"，8 秒后返回 status_code=0

**代码支持**: main_automation.py 有 8 秒超时逻辑

**原因**:
- 代理未启动
- 浏览器代理配置失败
- 网络隔离（VPN、代理冲突）

**检查**:
```bash
# 检查代理是否运行
curl -x http://127.0.0.1:8080 http://example.com

# 查看浏览器代理配置
ps aux | grep "chrome.*proxy"

# 手动启动 mitmproxy 调试
mitmdump -p 8080 -s start_mitm_proxy.py
```

### ❌ 采集超时（8s 未收到数据）[🧪 待实测]

**症状**: 大量 `status_code=0` 记录

**说明**: 代码设定 MITM 等待超时为 8 秒。实际发生频率和解决有效性需验证。

**原因（推测）**:
- CNIPA 网站响应慢
- MITM 拦截配置错误
- 网络不稳定

**解决**:
1. 增加超时时间（main_automation.py: line 398）
2. 检查 MITM 拦截规则（patent_mitm_scraper.py）
3. 重试单个申请号

### ❌ 浏览器崩溃 [🧪 待实测]

**症状**: 进程突然终止，无错误信息

**说明**: 推测的故障，需验证实际发生频率和恢复流程。

**原因（推测）**:
- 内存不足（某些大型网页）
- Chrome 内部错误
- 操作系统资源限制

**恢复**:
```bash
# 查看数据库中最近记录
uv run python -c "from db_manager import PatentsDB; from settings import PATENTS_DB_FILE; print(PatentsDB(PATENTS_DB_FILE).get_recent_records(5))"

# 从该申请号的下一条重新启动
# main_automation.py 会自动跳过已采集的
USE_MITM_PROXY=true python main_automation.py
```

### ❌ SQLite 完整性检查失败

**症状**: Dashboard/采集脚本报告 `database disk image is malformed` 或无法打开 `patents.db`

**说明**: `patents.db` 是运行时唯一真相源。JSONL 是 Git 备份，不能在运行时替代数据库直接读取。

**原因**:
- 文件系统或磁盘异常
- 非正常断电导致数据库页损坏

**恢复**:
```bash
# 先停止采集并检查数据库
sqlite3 data/patents.db 'PRAGMA integrity_check;'

# replica 可从 Git JSONL 备份重建
uv run python sync.py rebuild

# master 默认禁止重建；先保留损坏库，再由人工确认风险后执行
cp data/patents.db data/patents.db.corrupt
uv run python sync.py rebuild --force --i-know-this-is-master
```

---

## 性能优化建议

### 调整超时时间

**文件**: `main_automation.py`

超时已由 `settings.py` 集中读取，可通过环境变量调整：
```bash
MITM_TIMEOUT=12 USE_MITM_PROXY=true python main_automation.py
```

### 并发采集（未来版本）

目前单线程顺序采集。若需加速：
1. 多个浏览器实例 + 线程池
2. 需要分摊申请号列表
3. SQLite 已提供线程安全写入；并发采集仍需评估浏览器、代理和风控限制

### 资源清理

```bash
# 清理缓存（保留日志）
rm data/patent_cache.json data/patent_fwxx_cache.json

# 完整清理（需要重新采集）
rm -rf data/*
```

---

## 验证采集结果

### 检查数据库统计与完整性

```bash
sqlite3 data/patents.db 'PRAGMA integrity_check;'
uv run python -c "from db_manager import PatentsDB; from settings import PATENTS_DB_FILE; print(PatentsDB(PATENTS_DB_FILE).get_summary())"
```

### 导出数据检查

```bash
# 查看 Excel 文件
open data/results/patents_data.xlsx
```

---

## 清单

- [ ] 依赖已安装 (`pip list | grep selenium`)
- [ ] 申请号列表已准备 (`wc -l data/search_list.txt`)
- [ ] 坐标配置已生成 (`cat data/config.json`)
- [ ] 机器角色已配置 (`cat data/machine_role.txt`)
- [ ] MITM 代理已启动（终端 1）
- [ ] 主程序或看门狗已启动（终端 2）
- [ ] 采集中未手动干扰浏览器
- [ ] `sqlite3 data/patents.db 'PRAGMA integrity_check;'` 返回 `ok`
- [ ] replica 已完成增量拉取并检查数据提交

---

*更新时间*: 2026-07-27
*验证平台*: macOS / Windows, Python 3.11
*上次测试*: 2026-07-11

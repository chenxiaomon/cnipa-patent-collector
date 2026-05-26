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

跨机器运行时使用 `sync.py` 同步采集进度。核心原理：`patents.db`（SQLite，本地运行时主存储）通过 `data/results/detection_log.jsonl`（git 追踪）在多机之间共享。

### 数据架构

```
patents.db  ←→  export_to_jsonl / import_from_jsonl  ←→  detection_log.jsonl  ←→  GitHub
（本地主存储，不入 git）                                   （git 追踪，多机同步载体）
```

### 新机器初始化

```bash
git clone https://github.com/chenxiaomon/cnipa-patent-collector.git
cd cnipa-patent-collector
pip install -r requirements.txt
python sync.py init          # git pull + 从 JSONL 重建本地 DB
```

### 每次采集前

```bash
python sync.py pull          # 拉取最新 JSONL → 重建本地 DB → 自动跳过已采记录
```

### 每次采集后

```bash
python sync.py push          # 导出 DB → 合并远端 → 提交 JSONL → git push
```

### 所有 sync 命令

| 命令 | 说明 |
|------|------|
| `python sync.py pull` | 采集前：git pull + 重建 DB |
| `python sync.py push` | 采集后：导出 DB + git push |
| `python sync.py status` | 查看本地记录数和 git 状态 |
| `python sync.py init` | 新机器：git pull + 重建 DB（DB 已存在时提示确认） |
| `python sync.py rebuild` | 仅从现有 JSONL 重建 DB（DB 损坏 / 迁移 / 恢复用） |

### 冲突处理

`pull` / `push` 遇到 git 冲突时自动合并：以申请号为键，两边独有的记录全保留，同一申请号取 timestamp 较新的。无需人工介入。

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

重新启动时自动跳过已采集的申请号（基于 detection_log.json）

```bash
USE_MITM_PROXY=true python main_automation.py
```

### 3. 批量重试失败的申请号

采集完成后，检查失败记录：

```bash
# 查看失败统计
grep -c '"status_code": 0' data/results/detection_log.json

# 运行重试脚本
python retry_failed_applications.py
```

### 4. 补采发文信息

若主流程跳过了发文信息（或采集失败）：

```bash
# 执行补采（从 detection_log.json 中筛选待补采目标）
USE_MITM_PROXY=true python collect_fwxx.py

# 或仅补采前 5 条（测试模式）
USE_MITM_PROXY=true python collect_fwxx.py --test 5

# 或指定申请号文件
USE_MITM_PROXY=true python collect_fwxx.py --input data/fwxx_list.txt

# 或指定单个申请号
USE_MITM_PROXY=true python collect_fwxx.py --app CN201880002233
```

### 5. 导出 Excel 报表

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
- 内存不足（某些���型网页）
- Chrome 内部错误
- 操作系统资源限制

**恢复**:
```bash
# 检查日志，找到最后成功的申请号
tail -n 5 data/results/detection_log.json

# 从该申请号的下一条重新启动
# main_automation.py 会自动跳过已采集的
USE_MITM_PROXY=true python main_automation.py
```

### ❌ 文件损坏（JSON 解析错误）[⚠️ 已知高风险]

**症状**: `json.JSONDecodeError` 或 `detection_log.json` 无法打开

**说明**: 当前代码每次 add_record() 都整文件读写，中断会导致不完整写入。**这是已知的架构问题**。

**原因**:
- 进程中断时 JSON 写入不完整（RMW 非原子操作）
- 并发写入冲突（若多进程访问同一文件）

**恢复**:
```bash
# 备份
cp data/results/detection_log.json data/results/detection_log.json.bak

# 验证 JSON 格式
python -m json.tool data/results/detection_log.json

# 如果无法修复，从备份恢复
cp data/results/detection_log.json.bak data/results/detection_log.json
```

---

## 性能优化建议

### 调整超时时间 [📋 待实现]

**文件**: `main_automation.py`

当前超时硬编码为 8 秒（line 402）。若需调整，建议改造：

```python
# 建议改造：使用环境变量或配置文件
MITM_TIMEOUT = int(os.getenv('MITM_TIMEOUT', 8))  # 默认 8s
```

然后可通过环境变量调整：
```bash
MITM_TIMEOUT=12 USE_MITM_PROXY=true python main_automation.py
```

> 注：此功能当前代码中不存在，列为建议改进项

### 并发采集（未来版本）

目前单线程顺序采集。若需加速：
1. 多个浏览器实例 + 线程池
2. 需要分摊申请号列表
3. 需要线程安全的日志文件（或改用 SQLite）

### 资源清理

```bash
# 清理缓存（保留日志）
rm data/patent_cache.json data/patent_fwxx_cache.json

# 完整清理（需要重新采集）
rm -rf data/*
```

---

## 验证采集结果

### 检查日志完整性

```bash
# 统计成功/失败
python -c "
import json
with open('data/results/detection_log.json') as f:
    log = json.load(f)
    meta = log.get('metadata', {})
    print(f\"总计: {meta.get('total_records', '?')}
    成功: {meta.get('successful', '?')}
    失败: {meta.get('failed', '?')}
    成功率: {meta.get('successful', 0) / meta.get('total_records', 1) * 100:.1f}%\")
"
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
- [ ] MITM 代理已启动（终端 1）
- [ ] 主程序已启动（终端 2）
- [ ] 采集中未手动干扰浏览器
- [ ] 采集完成后日志文件有效

---

*更新时间*: 2026-05-10  
*验证平台*: macOS 12+, Python 3.8+  
*上次测试*: 2026-05-10

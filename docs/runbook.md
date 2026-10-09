# 运行手册（Runbook）

## 快速开始

### 独立桌面控制台（可选）

桌面版通过系统 WebView 显示现有控制台，支持 macOS 和 Windows；网页后台仍只使用标准库，不依赖桌面组件。首次安装：

```bash
uv sync --frozen --extra desktop --python 3.11
```

macOS 在项目目录运行 `.venv/bin/python install_desktop_app.py`，会在桌面创建带图标的 **CNIPA 专利采集.app**，以后双击即可。安装器使用本机 Apple 命令行工具编译原生启动器，避免 Apple Silicon 把脚本应用识别成需要 Rosetta 的旧程序；仍然调用项目 `.venv`，不打包或替换 Python。只更新属于本项目的同名入口，不覆盖其他应用；移动项目后，将旧桌面入口移到废纸篓，再从新位置重新安装。图标源文件可通过 `scripts/generate_desktop_icon.py` 重建。

macOS 也可继续双击 `desktop.command`，Windows 双击 `desktop.bat`。入口固定使用项目 `.venv`，不在启动时自动安装依赖。命令行入口为：

```bash
uv run --frozen --extra desktop python desktop_dashboard.py
# 自定义端口；重开时仍使用同一个端口
uv run --frozen --extra desktop python desktop_dashboard.py --port 8766
```

- 首次打开自动启动独立的本机后台，默认仅监听 `127.0.0.1:8765`；重开时通过本机身份接口核对项目目录后复用后台，不重复启动任务。端口被其他项目、旧版控制台或其他软件占用时明确报错，不自动终止对方。
- 关闭桌面窗口或退出软件时，先停止本控制台管理的采集及其浏览器，再停止代理，等待任务结束日志保存后退出本地后台。收尾可能需要数秒。下次打开后可在「任务日志 → 采集批次」选择原批次继续未完成项；不会自动重新执行任务。需要继续在后台采集时，请最小化窗口。
- 桌面窗口连接同一个本机后台；关闭其中一个桌面窗口会退出该后台，其他窗口也会断开。退出请求绑定本次后台实例，不会关闭后来在同端口启动的新后台。普通浏览器网页关闭仍只关闭页面。
- 若桌面窗口复用了终端手动启动的网页后台，退出桌面软件也会关闭这个后台及其管理的任务；单独从其他终端启动的采集程序不归此后台管理，不会被一并终止。
- 代码更新或数据库重建正在执行时，关闭窗口会提示等待操作完成后再退出；若系统退出路径已关闭界面，程序会等待维护结束再自动关闭后台。普通采集的未完成批次始终保留；系统强制结束进程时仍需按批次恢复。
- 桌面入口自动启动的后台将访问和错误日志写入 `data/dashboard_service.log`，每片最多 4 MiB，保留 3 片备份；这是持续追加并轮转的运行日志。任务输出仍分别保存在 `data/job_logs/`。启动失败时先查看终端提示及服务日志。
- 更新代码后需要重启后台才能加载新版。先结束或停止活动任务及代理，再终止对应的后台进程，重新打开桌面入口。macOS 可用 `lsof -nP -iTCP:8765 -sTCP:LISTEN` 确认 PID 后执行 `kill <PID>`；Windows 在任务管理器中核对对应项目的 Python 命令行后结束该后台。不要终止其他项目的 Python 进程。
- Windows 必须具备 Microsoft Edge WebView2 Runtime；桌面版明确使用 Edge 内核，不回退到旧 IE。macOS 使用系统 WebKit。Linux 继续使用网页或命令行入口。
- 下载会弹出系统保存窗口；可下载报表、批次记录、任务日志和导入模板。关闭窗口后不保留 WebView 登录存储，下次打开会重新从本机后台取得操作令牌。

本机已有旧后台且缺少当前桌面退出协议时，桌面入口会提示不兼容。确认其任务结束并停止旧后台后重开即可；桌面入口不会为了接管界面强制中断旧任务。

### 0. 环境检查

```bash
# 确保在项目目录
cd ~/Desktop/vibe/cnipa-patent-collector

# 检查项目 Python 版本（生产与开发统一为 3.11）
python --version

# 检查必需文件
ls data/search_list.txt  # 申请号列表
ls data/config.json      # 鼠标坐标配置
```

### 1. 安装依赖

```bash
# 使用 uv.lock 安装到项目 .venv
uv sync --frozen --python 3.11

# 后续 python 命令使用该环境
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate.bat  # Windows CMD
```

Windows 可直接运行 `setup.bat` 安装锁定运行依赖；缺少 uv 时按脚本提示安装，再重新打开终端。采集入口 `run.bat` 和公开浏览器入口 `launch_browser.bat` 固定使用项目 `.venv`。

### 2. 准备申请号列表

创建 `data/search_list.txt`，每行一个申请号：

```
CN201880002233
CN201880002234
CN202380004567
```

### 3. 配置鼠标坐标

Windows 和 macOS 在 Dashboard「系统配置 → 鼠标坐标配置」分别启动四个校准任务，也可在项目 Python 环境执行：

```bash
python record_search_coordinates.py
python record_detail_coordinates.py detail-link
python record_detail_coordinates.py fwxx-menu
python record_detail_coordinates.py fee-menu
```

校准会打开与采集相同的固定尺寸 Chrome 窗口。完成登录并导航到目标页面后，按弹窗提示单击输入框、查询按钮、搜索结果标题或详情菜单。点击会正常作用于页面；切换窗口时的点击不会作为目标保存。

坐标配置在 Dashboard 中只读，校准任务与采集、代码维护互斥。配置同时记录屏幕和窗口几何；坐标缺失、落在工具栏或屏幕范围外、屏幕分辨率或窗口设置变化时，采集会在启动浏览器前停止，须重新校准。旧版没有几何信息的配置也须重新录制；看门狗遇到此类错误会保留待采批次并停止，修复后可续跑。

macOS 校准使用原生弹窗。先准备好页面，确认弹窗后切回本次校准打开的 Chrome，再点击目标；切换窗口的点击不会记录，按 Esc 可以取消。请在「系统设置 → 隐私与安全性」允许启动控制台的终端应用使用辅助功能、输入监控和屏幕录制，变更权限后重启终端及控制台。窗口默认位于 `(0, 50)`，避开菜单栏；如需调整尺寸，请统一修改 `.env` 中的 `BROWSER_WINDOW_X/Y/WIDTH/HEIGHT` 后重新校准，不要手工给旧坐标补几何字段。

Apple Silicon 会自动准备与 Chrome 主版本匹配的 ARM64 ChromeDriver，在独立临时目录完成 UC 补丁、签名和可执行验证后发布到 UC 缓存。手工放入 `chromedriver-mac-arm64/` 的匹配驱动也会先复制再处理，原文件保持不变；不需要安装 Rosetta 或关闭系统安全设置。

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

# 启动主 mitmproxy（默认监听 127.0.0.1:8083）
python start_mitm_proxy.py
```

**预期输出**:
```
[+] 启动 mitmproxy...
[*] 监听地址: 127.0.0.1:8083
[*] 脚本文件: patent_mitm_scraper.py
```

**故障排查**:
- 端口被占用：先确认是否已有主代理运行；主代理默认 8083，公开查询代理默认 8082，可通过 `settings.py` 的环境变量覆盖。
- mitmproxy 未安装：在项目目录重新运行 `uv sync --frozen --python 3.11`。

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
- 确认已完成登录并进入查询页后，在终端按 Enter，或在 Dashboard 点击登录确认按钮。程序以人工确认为准，确认后不再探测页面；等待超时或未确认时任务会停止，不继续消耗申请号。看门狗对 `login_required` 报警不自动重启，处理完成后重新启动任务。
- 浏览器自动打开，PyAutoGUI 自动输入搜索框
- **勿手动关闭浏览器**（会中断采集）
- **勿移动鼠标**（可能干扰 PyAutoGUI）
- **勿切换窗口**（某些情况下会导致输入失败）

发文和费用采集在提交搜索前建立本轮尝试标记，被动等待 MITM 收到的搜索响应确认唯一目标，再用本轮官方 `sqxx` 响应核对详情页申请号。登录后不注入脚本、不读取 DOM、不注册页面鼠标监听。搜索未确认、详情页打开超时或身份响应超时，发文会有限重试，费用则记入失败清单后继续下一件；已点击的尝试会先撤销标记、刷新搜索页并清理迟到窗口。官方申请号不符、登录失效、多开窗口或无法安全恢复时仍停止批次。升级这些模块后须重启 Dashboard 和主 MITM 代理，使采集器和代理使用同一版协议。

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

`init` 只接受 `git pull --ff-only` 成功后的备份；网络失败、分支分叉或合并冲突时停止，不导入数据库。需要离线恢复已核实的本地 JSONL 时，单独执行 `sync.py rebuild`。导入前会验证整份 JSONL，损坏行或无效申请号会使整次导入失败；旧版追加日志中的同申请号多条记录仍按文件顺序重放。

### replica 拉取 master 增量

```bash
cp data/master_sync.example.json data/master_sync.json
# 编辑 master_url 后执行
uv run python sync_pull_from_master.py
```

脚本以 `data/master_sync_state.json` 保存的 master 数据库修改时间为游标，同时拉取新增记录和已有记录更新；master 明确提供的空值也会覆盖 replica 旧值。成功后合并 SQLite、刷新 JSONL 和 README，并创建一个限定范围的数据提交。升级本次同步导入逻辑后，旧游标会触发一次全量重对账，补回以前漏掉的清空操作；之后继续增量。master 地址变化也会触发全量重对账。最后人工检查并执行：

```bash
git push
```

### 危险操作护栏

`master` 默认拒绝从 JSONL 重建/覆盖数据库。只有明确承担风险时才允许：

```bash
uv run python sync.py rebuild --force --i-know-this-is-master
```

master 的增量导入允许执行，但终端必须先显示输入条数、新申请号、更新已有记录和时间范围，并等待回车确认。Dashboard 导入会先返回同一摘要，再要求二次确认。

### 完整数据库备份

`DetectionLogger` 每累计 500 次写入生成一份完整 SQLite 备份，保存于日志同目录的 `detection_log_backup_*.db`，保留最新 5 份。备份读取已提交的 WAL 内容，包含专利、费用数据集、请求和采集失败记录；完成后才原子发布，失败不会替换上一份备份。历史 `.jsonl` 备份继续保留。

JSONL 只保存专利记录，不能替代完整数据库备份。恢复 `.db` 备份前须停止 Dashboard、MITM 和全部采集进程，并保留当前数据库及其 WAL/SHM 文件；恢复时不能混用旧数据库的 WAL/SHM 与备份文件。本轮升级不会自动恢复或改写生产数据。

### 安全代码更新与回滚

部署机代码更新使用 HTTP 发布清单，永不覆盖 `data/`：

```bash
uv run python fetch_update.py
uv run python rollback.py
```

更新先下载全部文件并校验 SHA-256 和版本，验证成功后才生成代码备份并安装。备份位于 `backups/code_YYYYMMDD_HHMMSS_ffffff/`，完成后才发布；安装失败会自动恢复，最多保留 5 份。下载或校验失败不会替换当前代码。

更新和回滚从开始检查到安装、恢复及清理结束全程持有跨进程维护锁。主采集看门狗、详情采集、Phase 0 浏览器和公开查询浏览器/翻页任务运行时会拒绝维护；维护运行时这些入口也会在启动浏览器前退出。看门狗在子进程重启等待期间仍保留监督锁，不能利用重试间隙插入更新。锁由操作系统随进程退出释放，不要手工删除正在使用的运行锁文件。

回滚前验证整份备份的文件清单和 SHA-256。残缺、损坏或缺少哈希索引的旧备份会被拒绝，旧备份文件仍保留，可人工核对后恢复。代码回滚不回退 Python 依赖。

Windows 的 `upgrade.bat` 和 Dashboard 更新入口统一使用 HTTP 发布清单。维护顺序为：停止采集，执行更新，运行 `setup.bat` 同步锁定依赖，再重启 Dashboard 和主 MITM 代理。回滚到旧代码后也须重新运行对应版本的 `setup.bat`。

### 从 Mac 发布到 GitHub

公司电脑通过 GitHub `main` 分支的 HTTP 文件获取发布版本。向 GitHub 推送代码不会远程安装到公司电脑；公司电脑的更新检查会发现新版本，由现场操作者触发安装。

1. 在发布分支整理代码、测试和运行手册。跨日期发布提升 `VERSION`，并把 `RELEASE_REVISION` 设为 `0`；同日追加发布只递增 `RELEASE_REVISION`。日期格式始终为 `YYYY.MM.DD`，不可直接在 `VERSION` 后加修订后缀。
2. 运行 `python scripts/sync_version.py`，再运行 `uv lock --python 3.11` 同步项目版本，不加 `--upgrade`。
3. 完成代码检查和隔离测试后，最后运行 `python scripts/generate_code_manifest.py`。版本、代码、锁文件和清单须一起提交，本轮不提交 `data/` 的变化。
4. 推送发布分支，创建目标为 `main` 的 Pull Request，等待 Ubuntu 和 Windows CI 全部通过。当前工作流不会因单独推送普通开发分支而触发。
5. 合并到 `main` 后，支持修订号的公司电脑即可检测该发布。首次迁移按下面的同日修订步骤手动更新。不要把尚未通过 Windows 检查的候选版本直接推到 `main`。

### 同日修订、重启和同步对账

`2026.09.06 r1` 的 `VERSION` 仍是 `2026.09.06`，`RELEASE_REVISION` 为 `1`。没有修订文件的旧安装按 `r0` 处理；旧更新检查只比较日期，因此首次安装 r1 不会自动提示新版本。

1. 在公司 master 停止采集、看门狗和代理，保留数据库备份。已安装 `2026.09.06` 的机器可直接运行 `upgrade.bat`，或点击 Dashboard「更新系统代码（HTTP）」；更早的部署按下方首次升级步骤操作。
2. 更新完成后重启 Dashboard 和需要使用的代理。后续更新或回滚时，Dashboard 会区分「正在运行」和「已安装」修订，并提示待重启；修改磁盘上的代码不会自动替换正在运行的服务。
3. 开发 replica 也升级到 r1 后，在 replica 项目环境执行 `python sync_pull_from_master.py --full`，或在「数据管理 → 多机同步」点击「从 master 全量对账」。它只把 master 的专利快照合并到 replica，不删除或修改 master 数据。
4. 升级后的首次同步本身会自动全量对账一次。仍建议在两端都升级并重启后显式执行 `--full`，覆盖 replica 先升级、master 尚未升级期间的旧同步问题。同步生成的数据提交须检查后再推送 GitHub。

同一副本机的增量同步和全量对账不能同时运行；重复启动会提示已有同步任务占用。不要删除正在使用的 `data/master_sync.lock` 文件。

### 采集中断后继续

升级 r1 后，新启动的主采集、发文和费用采集均在本机 `data/collection_batches/` 保存独立批次。Dashboard「任务日志 → 采集批次」可查看总数、成功、失败、剩余申请号、失败原因和每轮执行记录，支持筛选及下载 JSON。历史不依赖 Dashboard 内存，重启后仍可读取；已有的旧任务不会补造历史。

看门狗会在第一次启动子进程前把当时的待采目标冻结为一个主采集批次。健康检查终止子进程后，每次自动重启都使用该批次 ID，只跳过已经成功的目标；即使子进程退出码为 `0`，看门狗也会核对批次是否确实无剩余项。需要人工登录时停止自动重启并保留该批次，空目标则不启动子进程。

恢复浏览器和登录环境后，选择原批次并点击「继续未完成项」。它只重新处理尚未成功的申请号，在同一批次增加一轮记录；此前的失败原因仍保存在历史中。命令行也可使用日志中的完整批次 ID：

```text
python main_automation.py --resume-batch <批次ID>
python collect_fwxx.py --resume-batch <批次ID>
python collect_fees.py --resume-batch <批次ID>
```

仍在运行的批次不能重复续跑，已全部成功的批次无需续跑。进程意外退出但未写入结束状态时，读取历史会显示为中断；不要删除批次目录中的锁文件。批次记录和锁均不提交 GitHub，不会随代码更新转移到其他电脑，需要保留历史时将该目录纳入本机备份。

三个采集器分别维护最新一批的恢复清单：主采集为 `data/checkpoint_resume.txt`，发文为 `data/checkpoint_fwxx.txt`，费用为 `data/checkpoint_fees.txt`。新批次会替换对应采集器的旧清单；每条成功写入数据库后才从清单移除，失败和未尝试项都会保留。清单是本机运行文件，不提交 GitHub。

普通单条失败仍会继续处理后续申请号；完成本批导出后，只要本次选中的目标仍有失败项，任务就以失败状态结束。`--test N` 只限制本次执行数量，未选中的后续目标仍保留在清单中，不算本次失败。浏览器退出或用户中断同样不会被报告为正常完成。恢复浏览器和登录环境后，在项目 Python 环境执行对应命令：

```text
python main_automation.py --update-list data/checkpoint_resume.txt
python collect_fwxx.py --input data/checkpoint_fwxx.txt --force
python collect_fees.py --input data/checkpoint_fees.txt --force
```

以上 TXT 命令兼容旧清单，会创建新批次。需要延续现有批次历史时使用 `--resume-batch`。日志也会输出续跑命令；清单内所有目标采集成功后，对应文件为空。`--test N` 执行完成但仍有未选中目标时，批次显示为暂停。

### 一键环境诊断

在 Dashboard「系统配置 → 环境诊断」点击「开始诊断」，完成后可下载 JSON 报告。检查使用与 Dashboard 启动采集任务相同的 Python，涵盖解释器和依赖、Chrome 与现有驱动版本、本机代理端口、坐标配置及数据库目录和现有 DB/WAL/SHM 文件的权限。

诊断对象是运行 Dashboard 服务的电脑。要排查公司 Windows 环境，必须在公司 Dashboard 上执行；Mac 上的诊断不能说明公司电脑是否正常。缺少、超时或不能确定的检查会单独显示，并给出处理建议。

诊断不会启动浏览器、移动鼠标、访问国知局或打开专利数据库。存储检查只用独立临时文件测试目录写入，报告中的 `sqlite_write_tested` 固定为 `false`；它不证明真实 SQLite 事务、账号登录、页面坐标准确性或代理拦截协议均可用。非本机代理地址不会被探测，报告不会包含密码、Token 或专利记录。

### 首次升级到 2026.09.06

以下步骤用于已经带有 `fetch_update.py` 的旧部署。公司电脑的实际版本尚未核验；如果没有该文件，应先核对现有部署入口。

旧更新器会在进程启动时加载旧备份代码，即使下载了新版文件，本次升级前的备份仍可能没有哈希索引。因此首次迁移使用独立的项目副本作为回退依据，后续由新版更新器生成可校验的代码备份。

1. 在公司电脑确认 `uv --version` 可用，再停止看门狗、采集任务、Dashboard 和两个 MITM 代理，关闭使用项目虚拟环境的终端任务。缺少 uv 时先按 [uv 官方说明](https://docs.astral.sh/uv/getting-started/installation/) 安装并重新打开终端。
2. 将整个项目目录复制到项目之外的另一个目录，保留 `data/`、`.venv`、`.env` 及本机配置。确认目录复制完成，没有跳过或失败的文件。
3. 在原先能运行项目的 Python 环境和项目目录中，执行 `python fetch_update.py --check`，成功后执行 `python fetch_update.py`。检查 `VERSION` 必须为 `2026.09.06`，避免自定义分支或镜像仍返回旧版本。首次升级不要依赖旧 `upgrade.bat`，它仍可能走 Git 更新。
4. 运行新版 `setup.bat`，确认安装成功且 `.venv\Scripts\python.exe --version` 为 3.11。使用该解释器分别启动 Dashboard 和主 MITM 代理，核对界面版本，再执行少量采集验收；此前保持采集和看门狗停止。
5. 如果代码更新或依赖安装失败，且尚未恢复采集，关闭相关进程，将第 2 步的完整副本恢复到原项目路径，包括原 `.venv`、`data/` 和本机配置，不复用失败安装留下的文件。此次首跳不能依赖新版 `rollback.py` 自动接收无哈希的旧代码备份。

### 无人值守采集与报警

```bash
# 部署机：看门狗启动主采集
uv run python collection_watchdog.py

# replica：轮询部署机报警并转发到 ServerChan
SERVERCHAN_SENDKEY=... uv run python poll_master_alerts.py
```

看门狗检查 10 分钟心跳超时、磁盘空间和连续失败次数；同一批次连续 3 轮采集失败（`WATCHDOG_MAX_RESTARTS`，默认 3，即首次运行加 2 次自动重启）后停止并写入 `data/alert_status.json`。只要某一轮有新的申请号采集成功，计数就重新开始，长批次中相隔很久的偶发故障不会累计到停机。

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

费用响应先到部分栏目时，程序会继续等待至缓存超时；届时保存本轮有效栏目，日志和批次历史明确列出缺失的必需栏目。代理额外记录缺失位置、JSON 类型、`isShow` 布尔值等结构诊断，不记录原始费用内容。`null`、缺少栏目、非对象或非列表不能推定为零应缴费，仍保留待重试。每份含有效栏目的新响应都带快照时间，新部分快照不会借用旧完整快照中的栏目。

费用采集每轮每件只尝试一次。搜索结果未就绪、详情导航超时、无费用响应或栏目不完整等普通失败，会保留具体原因并继续下一件；不再当场重试，也不因连续普通失败停批。已开始的详情尝试仍须安全清理后才能继续；登录失效、浏览器丢失、申请号不符或无法确认唯一窗口会中断批次，保留剩余清单。批次全部尝试完但仍有失败时，任务仍显示失败，日志给出成功与失败数量和下一轮补采入口。

后续可多次点击 Dashboard 费用面板的「仅重试失败项」，或执行 `USE_MITM_PROXY=true uv run python collect_fees.py --retry-failed`。每次只读取当时仍在费用失败清单中的申请号，每件本轮最多增加一次失败计数；成功落库后自动移出，下一轮不会再采。指定名单、失败重试及批次续跑都会先检查主库是否建档：未建档的跳过浏览器操作并保留明确原因，全部未建档则直接退出，需先完成主采集。中途中断而尚未尝试的申请号通过 Dashboard 续跑原批次处理。

若日志反复出现搜索未确认，应结合代理日志判断是否有搜索接口失败，不能仅凭这条提示认定坐标失效。搜索回执独立于基础专利缓存，已建档案件也会产生回执；只有绑定本轮请求且返回唯一目标的响应才能放行。没有本轮回执时会提示检查主代理版本及重启，过期回执不能替代。程序按原坐标真实点击，随后仍通过 `sqxx` 核对实际详情身份，搜索确认不代表页面坐标准确。

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

### 长时间运行的失败恢复与日志

Dashboard 的“采集新增目标”跳过已经尝试过的申请号，包括历史失败。需要补齐失败时，在“任务日志 → 批次记录”选择原批次并继续，或使用失败重试入口；不要仅重复启动新增目标采集。

人工确认登录后建立本轮会话标识。MITM 收到 HTTP 401、业务码 401 或明确的登录失效消息后，采集在下一次等待检查时停止，保留未完成批次；看门狗等待人工处理，不进行无效重启。重新启动该批次、完成登录后可继续。普通 403、429 和服务器错误只记录证据，不自动认定为登录过期；未识别的故障由各采集器的超时策略处理，主采集和发文保留连续失败熔断，费用则记录失败后继续。

发文采集先等待本轮搜索回执确认唯一目标，再打开详情，并保留官方详情申请号核验。搜索响应异常、详情未打开、身份超时及发文响应超时分别记录原因；每件仍最多尝试两次。可用 `FWXX_SEARCH_READY_TIMEOUT` 调整本地搜索回执等待上限，默认 15 秒，不会改变原有鼠标操作和防爬等待。

从 Dashboard 启动的任务将 stdout/stderr 写入 `data/job_logs/`，每行带 UTC 时间，结束写入退出状态；在“任务日志”的已保存日志区域可列出和下载，Dashboard 重启后文件仍可用。默认每片 4 MiB、每任务保留 3 个旧片段，最多保留最近 100 个已结束任务，运行中任务不清理。对应环境变量为 `CNIPA_DASHBOARD_JOB_LOG_MAX_BYTES`、`CNIPA_DASHBOARD_JOB_LOG_BACKUP_COUNT`、`CNIPA_DASHBOARD_JOB_LOG_RETENTION_COUNT`，均须为正整数。

MITM 接口失败另存 `data/results/cnipa_api_events.jsonl`，每片 1 MiB，保留 3 个旧片段。事件仅包含时间、会话标识、接口路径、HTTP 状态、数值业务码和分类，不保存请求参数、响应正文、Cookie 或 Token。`CNIPA_SESSION_STARTED` 日志可用于关联任务与接口事件。

升级后需要重新启动 Dashboard、MITM 代理以及采集任务，新日志与会话识别才会生效。历史仅存在内存中且已丢失的任务日志无法补回。日志超过轮转保留范围后会清理，需长期保留时及时下载。

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

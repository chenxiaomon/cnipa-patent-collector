# 发文信息采集程序使用指南

## 问题原因

运行 `collect_fwxx.py` 时报错：
```
[!] 采集过程出错: [Errno 2] No such file or directory: 'data/config.json'
```

这是因为程序需要从 `data/config.json` 中读取搜索界面的坐标配置。

## 解决方案（已自动修复）

我已经更新了 `collect_fwxx.py`，现在当配置文件不存在时，程序会自动进入**坐标记录模式**，无需先运行 `main_automation.py`。

## 使用步骤

### 第一次运行（需要记录坐标）

1. **启动 MITM 代理**（在终端 1）：
   ```bash
   python start_mitm_proxy.py
   ```

2. **启动采集程序**（在终端 2）：
   ```bash
   USE_MITM_PROXY=true python collect_fwxx.py
   ```

3. **程序会自动打开浏览器并要求登录**：
   - 手动输入用户名密码，登录 CNIPA 系统
   - 按 Enter 或等待 30 秒自动继续

4. **程序会提示记录坐标（仅需一次）**：
   - **第一步**：把鼠标移到「申请号输入框」的中间，等待 8 秒自动读取
   - **第二步**：把鼠标移到「查询按钮」的中间，等待 8 秒自动读取
   - 坐标会自动保存到 `data/config.json`

5. **程序会继续提示记录发文信息页面坐标**：
   - 把鼠标移到「申请号链接」的位置，等待 8 秒自动读取
   - 把鼠标移到左侧「发文信息」菜单的位置，等待 8 秒自动读取
   - 坐标会自动保存到 `data/config_fwxx.json`

6. **自动采集开始**：
   - 程序会自动搜索、进入详情页、采集发文信息
   - 支持断点续传（中断后重新运行会跳过已采集的）

### 后续运行（直接采集）

坐标已记录后，后续运行只需：

```bash
USE_MITM_PROXY=true python collect_fwxx.py
```

程序会：
1. 自动加载保存的坐标配置
2. 打开浏览器，提示登录
3. 登录后立即开始采集发文信息

### 指定列表强制采集（不限制案件状态）

在可视化控制台进入“发文采集”，将申请号粘贴到“指定专利批量采集发文”区域后启动。也可以准备一行一个申请号的文件并运行：

```bash
USE_MITM_PROXY=true python collect_fwxx.py --input data/fwxx_list.txt --force
```

`--force` 会让列表中的申请号全部进入详情页发文流程，不检查是否为“驳回等复审请求”，也不跳过已经存在发文信息的记录。申请号不在主数据库时，采集结果会保存在 `data/fwxx_unmatched.json`。

## 常见问题

### Q: 坐标记录时位置不对怎么办？

A: 删除配置文件，下次运行会重新记录：
```bash
rm data/config.json
rm data/config_fwxx.json
```

### Q: 程序中途被关闭了怎么办？

A: 无需担心，重新运行时程序会自动跳过已采集的申请号，继续从中断的地方采集。

### Q: 怎么知道采集进度？

A: 程序会实时显示进度：
```
[1/681] 申请号: CN202210054219.4
  [CN202210054219.4] 开始采集发文信息...
  [✓] 成功: 获取到 3 条发文信息
  
已采集: 1, 失败: 0, 总进度: 1/681
```

### Q: MITM 代理需要一直运行吗？

A: 是的，采集过程中 MITM 代理必须保持运行，因为程序需要通过代理拦截专利系统的 API 响应来获取发文信息。

## 采集完成后

采集完成后，运行以下命令导出最终 Excel（两个 Sheet）：

```bash
python -c "from detection_logger import DetectionLogger; DetectionLogger().export_to_excel()"
```

## 技术细节

- **坐标配置**：`data/config.json`（搜索页坐标）和 `data/config_fwxx.json`（详情页坐标）
- **采集日志**：`data/results/detection_log.json`（5,888 条记录，含采集状态）
- **缓存文件**：`data/patent_fwxx_cache.json`（MITM 代理写入，程序读取）
- **标记文件**：`data/current_fwxx_target.json`（同步 MITM 和采集程序）


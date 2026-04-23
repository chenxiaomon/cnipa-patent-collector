# 失败记录重试指南

## 🎯 目标
从 8,192 条已采集的记录中找出失败的记录，并重新采集它们的数据。

---

## 📊 当前状态

- **总记录数**：8,192 条
- **成功采集**：8,145 条（99.4%）
- **失败记录**：47 条（0.6%）- status_code=0，专利字段全为 null

---

## 🛠️ 手动重试方案

### 方案 1：使用 `retry_failed.py`（推荐）

这个程序会**自动检测失败的记录**，显示详情，然后可以一键清理和重试。

#### 步骤 1：查看失败的记录（不修改任何文件）
```bash
python retry_failed.py
```

**输出示例：**
```
📊 总记录数: 8192
❌ 失败记录: 47 条

序号  申请号                     状态码   原因
────────────────────────────────────────────────────────
1     CN202511000001.1          0        Network timeout
2     CN202511000002.2          0        Connection refused
... (共 47 条)

💡 如需重试这 47 条失败记录，请运行:
   python retry_failed.py --run
```

#### 步骤 2：执行清理和重试准备
```bash
python retry_failed.py --run
```

**输出示例：**
```
✅ 已备份: data/results/detection_log_backup_20260301_120000.json
✅ 已移除 47 条失败记录
📊 剩余记录: 8145 条

🚀 现在可以重跑:
   python start_mitm_proxy.py          # 终端 1
   USE_MITM_PROXY=true python main_automation.py --test 5
```

#### 步骤 3：启动重试采集

**终端 1（启动 MITM 代理）：**
```bash
python start_mitm_proxy.py
```
等待看到：`Listening on http://127.0.0.1:8080`

**终端 2（启动重试采集）：**
```bash
# 先用 --test 5 小规模测试（采集前 5 条）
USE_MITM_PROXY=true python main_automation.py --test 5

# 确认成功后，执行完整重试（采集全部 47 条）
USE_MITM_PROXY=true python main_automation.py
```

**预期耗时：**
- 5 条测试：2-3 分钟
- 47 条完整：8-12 分钟（考虑三次优化后的速度）

---

### 方案 2：使用 `retry_failed_applications.py`（备选）

如果你已经有一个 `failed_applications.txt` 文件，可以使用这个脚本。

#### 步骤 1：准备失败列表
需要提前在 `data/failed_applications.txt` 中列出失败的申请号：
```
CN202511000001.1
CN202511000002.2
CN202511000003.3
...（每行一个申请号）
```

#### 步骤 2：运行重试脚本
```bash
python retry_failed_applications.py
```

**输出示例：**
```
📋 读取失败的申请号...
✅ 找到 47 条失败的申请号

🔄 从 data/results/detection_log.json 中删除失败的记录...
✅ 删除了 47 条失败的记录
   剩余 8145 条成功的记录

📝 创建重试搜索列表...
✅ 重试列表已创建: data/search_list_retry.txt
   包含 47 个申请号

📌 下一步操作:
1. 检查重试列表: cat data/search_list_retry.txt
2. 替换搜索列表: cp data/search_list_retry.txt data/search_list.txt
3. 启动 MITM: python start_mitm_proxy.py
4. 重新采集: USE_MITM_PROXY=true python main_automation.py
```

#### 步骤 3：替换搜索列表并重试
```bash
cp data/search_list_retry.txt data/search_list.txt

# 然后执行和方案 1 相同的操作
python start_mitm_proxy.py          # 终端 1
USE_MITM_PROXY=true python main_automation.py  # 终端 2
```

---

## 📋 快速参考

| 程序 | 用途 | 命令 |
|------|------|------|
| `retry_failed.py` | 查看失败记录 | `python retry_failed.py` |
| `retry_failed.py --run` | 清理失败记录并准备重试 | `python retry_failed.py --run` |
| `retry_failed_applications.py` | 从文件列表重试（需要 failed_applications.txt） | `python retry_failed_applications.py` |

---

## 🔍 如何判断失败的记录？

一条记录被认为是"失败"的标准：

1. **status_code ≠ 200**（例如 0、403、500 等）
   - 通常表示网络超时、连接被拒绝等

2. **或者**：status_code = 200 但关键字段为 null
   - 例如 `zhuanlimc`（专利名称）为 null
   - 表示虽然连接成功，但 MITM 没有拦截到数据

3. **有 error_message**
   - 表示程序记录了错误

---

## ⏱️ 时间估计

根据之前应用的三项性能优化：

| 操作 | 耗时 |
|------|------|
| 查看失败记录（`retry_failed.py`） | < 1 秒 |
| 清理失败记录（`retry_failed.py --run`） | < 1 秒 |
| 重试 5 条（测试模式）| 2-3 分钟 |
| 重试 47 条（完整）| 8-12 分钟 |

---

## ✅ 验证步骤

重试采集完成后，检查结果：

```bash
# 1. 查看新的记录数
cat data/results/detection_log.json | jq '.records | length'
# 应该显示: 8192 (8145 + 47)

# 2. 查看是否还有失败的记录
python retry_failed.py
# 应该显示: ❌ 失败记录: 0 条

# 3. 导出最终 Excel
python main_automation.py --export-only
```

---

## 🚨 常见问题

### Q1：重试时浏览器一直等待？
**A：** 这是正常的。浏览器在等待 MITM 拦截数据。确保：
- 终端 1 的 MITM 代理正在运行
- 没有网络问题
- 最多等待 8 秒，超过则自动跳过

### Q2：重试后还有失败的记录？
**A：** 再次运行 `python retry_failed.py --run` 清理，然后重试。某些记录可能需要多次尝试。

### Q3：能否同时重试多个失败的申请号？
**A：** `main_automation.py` 已经支持断点续传，会自动逐个处理。如果中途中断，重新运行会从上次失败的地方继续。

### Q4：如何恢复被删除的失败记录？
**A：** 已自动创建备份文件 `detection_log_backup_*.json`，可以恢复：
```bash
cp data/results/detection_log_backup_20260301_120000.json data/results/detection_log.json
```

---

## 💡 最佳实践

1. **总是先查看**：运行 `python retry_failed.py` 查看失败记录详情
2. **备份是自动的**：`--run` 会自动备份原日志文件
3. **先小规模测试**：用 `--test 5` 验证配置正确
4. **监控进度**：在第二个终端查看日志大小增长

---

## 📌 总结

最简单的手动重试流程：

```bash
# 1. 检查失败记录（不修改）
python retry_failed.py

# 2. 清理失败记录并准备重试
python retry_failed.py --run

# 3. 两个终端并行运行（需要同时启动）
# 终端 1：
python start_mitm_proxy.py

# 终端 2：
USE_MITM_PROXY=true python main_automation.py
```

**预计总耗时：** 15-20 分钟（从检查到重试完成）

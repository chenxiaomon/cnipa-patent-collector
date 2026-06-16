# import_from_cache.py 使用指南

## 📋 功能说明

**目的**：将 Phase 0 手动采集的数据（缓存）导入到最终日志中

**数据流**：
```
用户手动浏览 CNIPA（按申请人搜索）
    ↓
MITM 代理拦截 API 响应
    ↓
patent_cache.json（临时缓存）
    ↓
python import_from_cache.py（本脚本）
    ↓
detection_log.json（最终日志）
    ↓
collect_fwxx.py（Phase 2 采集发文）
```

## 🚀 使用步骤

### Step 1：启动 MITM 代理
```bash
# 终端 1
python start_mitm_proxy.py
```

### Step 2：手动浏览 CNIPA
```bash
# 终端 2：打开浏览器，访问 CNIPA
# 按申请人名称搜索，浏览多页
# MITM 会自动拦截 API 响应，写入 patent_cache.json
```

### Step 3：检查缓存
```bash
# 查看缓存是否有数据
cat data/patent_cache.json | jq 'keys | length'

# 应该看到类似输出：681（即有 681 条数据）
```

### Step 4：执行导入
```bash
# 终端 2
python import_from_cache.py
```

### Step 5：验证结果
```bash
# 查看导入后的日志
cat data/results/detection_log.json | jq '.records | length'
# 应该看到更多的记录数（取决于导入了多少新数据）
```

## 📊 导入结果说明

执行 `python import_from_cache.py` 后会显示统计：

```
📊 导入统计
✓ 新增: N 条（真正导入的新记录）
→ 跳过: M 条（已存在日志中的记录）
✗ 失败: K 条（格式错误或其他问题）
```

**说明**：
- **新增** > 0：有新数据被导入，说明成功
- **跳过** > 0：这些数据已在日志中，说明去重机制工作正常
- **失败** > 0：某些缓存数据有问题，需要检查

## 🔄 重复执行

脚本是**幂等的**，可以安全地多次执行：

1. 第一次运行：导入所有新数据
2. 第二次运行：所有数据都会被跳过（因为已在日志中）
3. 第三次…：同上

这意味着即使你运行多次也不会导致重复数据。

## 🗑️ 缓存清空

导入成功后，脚本会自动清空 `patent_cache.json`（改为 `{}`）：

```bash
# 导入前：patent_cache.json 有 681 条
# 导入后：patent_cache.json 变为 {} （空）
```

**优点**：
- ✅ 缓存保持"干净"状态
- ✅ 避免下次手动浏览时的混乱
- ✅ 每次导入都知道缓存中是"新数据"

## ⚙️ 技术细节

### 申请号格式转换

脚本自动处理两种申请号格式：

```
输入格式                 转换结果
CN202511000000.1   →   2025110000001
2025110000001      →   2025110000001
CN202511000000     →   202511000000
```

### 去重机制

使用 `get_processed_applications()` 从日志中获取已有申请号，并标准化比对：

```python
# 日志中有：CN202511000000.1
# 缓存中有：2025110000001
# 标准化后都是：2025110000001
# → 识别为重复，跳过
```

## 🐛 故障排查

### 问题 1：缓存为空
```
[!] 缓存为空，无数据可导入
```

**原因**：MITM 没有拦截到 API，或缓存文件没有被创建

**解决**：
1. 检查 MITM 代理是否正常运行
2. 检查浏览器是否配置了代理（127.0.0.1:8080）
3. 尝试手动刷新 CNIPA 页面

### 问题 2：导入失败
```
[!] 采集过程出错: ...
```

**原因**：检查日志文件是否损坏，或权限问题

**解决**：
1. 确保有 `data/results/` 目录的写权限
2. 备份并删除 `detection_log.json`，让脚本重新创建
3. 重新运行导入

## 📚 相关文档

- `start_mitm_proxy.py` - MITM 代理启动脚本
- `detection_logger.py` - 日志记录器（使用 DetectionLogger）
- `collect_fwxx.py` - Phase 2 发文采集程序

---

**注意**：本脚本是 Phase 0 的补充，配合 Phase 1（main_automation.py）和 Phase 2（collect_fwxx.py）完成完整的三阶段采集。

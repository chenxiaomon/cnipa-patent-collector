# Phase 0 完整使用指南

## 📋 快速概览

**Phase 0** 是一个**手动采集模式**，用于按申请人名称搜索 CNIPA 并采集数据。

**三个脚本配合工作**：

| 脚本 | 作用 | 启动方式 |
|------|------|--------|
| `start_mitm_proxy.py` | 启动 MITM 代理，拦截 API 响应 | `python start_mitm_proxy.py` |
| `start_browser_for_phase0.py` | 启动配好代理的浏览器 | `python start_browser_for_phase0.py` |
| `import_from_cache.py` | 将缓存数据导入日志 | `python import_from_cache.py` |

## 🚀 完整流程（5 步）

### Step 1：启动 MITM 代理（终端 1）

```bash
python start_mitm_proxy.py
```

**预期输出**：
```
Listening on http://127.0.0.1:8080
```

✅ 保持这个终端打开，不要关闭

### Step 2：启动配好代理的浏览器（终端 2）

```bash
python start_browser_for_phase0.py
```

**预期输出**：
```
✓ MITM 代理 (127.0.0.1:8080) 已启动
✓ 浏览器启动成功!
✓ 页面已加载

📋 使用说明
=========
✅ 浏览器已配置代理 (127.0.0.1:8080)
✅ HTTPS 证书错误已忽略
✅ MITM 代理会自动拦截 API 响应

操作步骤：
  1. 手动登录 CNIPA 账户
  2. 在搜索框中输入申请人名称（如"华为"、"小米"等）
  3. 点击搜索
  4. 逐页浏览查看结果（MITM 会自动拦截每页的 API）
  5. 浏览完毕后，关闭浏览器窗口
```

✅ 浏览器会自动打开并指向 CNIPA

### Step 3：手动浏览和搜索（浏览器窗口）

**在打开的浏览器中操作**：

1. 登录 CNIPA 账户（如需要）
2. 在搜索框输入申请人名称，例如：
   - `华为`
   - `小米`
   - `HUAWEI`
   - `腾讯`
3. 点击「查询」或「搜索」
4. 逐页翻阅搜索结果

**监控 MITM 拦截**：
- 在终端 1（MITM）中可以看到类似的输出：
  ```
  [*] 拦截到 JSON 响应: /api/view/gn...
  [✓] 已缓存: 2025110000001 - 一种新型...
  [✓] 已缓存: 2025110000002 - 另一种...
  ```

### Step 4：关闭浏览器

当满足以下条件时，关闭浏览器窗口：
- ✅ 已浏览完所有想要的搜索结果
- ✅ MITM 终端已显示"已缓存"的记录

**关闭后**，脚本会自动停止：
```
[✓] 浏览器已关闭
```

### Step 5：导入缓存数据（终端 2 或新终端）

```bash
python import_from_cache.py
```

**预期输出**：
```
📥 Phase 0 缓存导入程序

[*] 加载缓存...
[✓] 缓存已加载: 35 条记录

[*] 加载日志...
[✓] 日志中已有 5888 条记录

[*] 开始导入...
───────────────────────────────
  [1] [✓] 导入成功: 2025110000001 - 一种新型...
  [2] [✓] 导入成功: 2025110000002 - 另一种...
  ...
───────────────────────────────

📊 导入统计
✓ 新增: 35 条
→ 跳过: 0 条（已有）
✗ 失败: 0 条
📈 总处理: 35 条

[✓] 缓存已清空
```

✅ 新数据已导入 `detection_log.json`

## 📊 数据流图

```
终端 1                      浏览器                    终端 2
┌──────────────┐      ┌──────────────┐         ┌──────────────┐
│ start_mitm   │◄────►│   Chrome     │◄─────►│ import_from  │
│   _proxy.py  │      │  + 代理      │        │   _cache.py  │
└──────────────┘      └──────────────┘        └──────────────┘
     监听                  手动搜索              导入数据
  127.0.0.1:8080          申请人             patent_cache.json
     拦截 API              翻页                  ↓
     写入缓存                            detection_log.json
```

## 🎯 各脚本详细说明

### start_mitm_proxy.py（MITM 代理启动）

**作用**：
- 监听 127.0.0.1:8080 端口
- 拦截浏览器发向 CNIPA 的 API 请求
- 解析 JSON 响应并提取专利数据
- 写入 `data/patent_cache.json`

**保持运行**：
- ✅ 整个 Phase 0 采集过程中需要持续运行
- ✅ 不要关闭或中断这个终端

### start_browser_for_phase0.py（浏览器启动）

**作用**：
- 检查 MITM 代理是否运行
- 启动一个配好代理的 Chrome 浏览器
- 自动打开 CNIPA 网址
- 监听浏览器窗口，直到用户关闭

**特殊配置**：
- `--proxy-server=http://127.0.0.1:8080`：代理地址
- `--ignore-certificate-errors`：忽略 HTTPS 证书错误（因为 MITM 会改变证书）
- `--ignore-certificate-errors-spki-list`：忽略证书列表检查

### import_from_cache.py（数据导入）

**作用**：
- 读取 `patent_cache.json`（MITM 写入的临时缓存）
- 读取 `detection_log.json`（现有日志）
- 对新数据进行格式转换和去重
- 创建 `DetectionRecord` 并写入日志
- 自动清空缓存文件

**幂等性**：
- ✅ 可以安全地多次执行
- ✅ 不会导致重复数据

## 🔧 常见问题

### Q1: 浏览器启动失败，报错"MITM 代理未响应"

**原因**：MITM 代理还没启动

**解决**：
```bash
# 终端 1：先启动 MITM
python start_mitm_proxy.py

# 等待 1-2 秒，看到 "Listening on..." 后，再启动浏览器
# 终端 2
python start_browser_for_phase0.py
```

### Q2: MITM 未拦截到任何数据

**原因**：浏览器可能没配好代理，或 CNIPA 的 API 没被触发

**解决**：
1. 在浏览器中手动刷新页面（Ctrl+R）
2. 在搜索框输入申请人并点击搜索
3. 查看 MITM 终端是否有 "[✓] 已缓存" 的输出

### Q3: 缓存文件不存在

**原因**：没有拦截到任何数据，所以 `patent_cache.json` 没被创建

**解决**：
1. 检查 MITM 是否正常运行（看终端输出）
2. 检查浏览器代理是否生效
3. 尝试多搜索几个不同的申请人

### Q4: 导入时显示"缓存为空"

**原因**：之前没有成功拦截到数据

**解决**：
1. 确保浏览器完全使用代理（不是部分）
2. 重新操作浏览器进行搜索和翻页
3. 查看 MITM 的输出确认已拦截

## 💡 优化建议

### 批量搜索多个申请人

**效率更高的方式**：
```
搜索第 1 个申请人 → 浏览 3-5 页
搜索第 2 个申请人 → 浏览 3-5 页
搜索第 3 个申请人 → 浏览 3-5 页
...
浏览完所有申请人后，关闭浏览器
执行一次 import_from_cache.py，所有数据一次导入
```

### 监控采集进度

**在另一个终端实时查看缓存大小**：
```bash
# 每秒更新一次缓存中的记录数
while true; do
  count=$(cat data/patent_cache.json 2>/dev/null | jq 'keys | length' 2>/dev/null || echo 0)
  echo "缓存数据: $count 条"
  sleep 1
done
```

## 🔄 重复使用

**对同一批数据重复导入**：

脚本会自动去重，所以即使运行多次也不会产生重复：

```bash
python import_from_cache.py  # 第 1 次：导入 100 条新数据
python import_from_cache.py  # 第 2 次：100 条都被跳过（已有）
python import_from_cache.py  # 第 3 次：同上
```

**添加新数据后重新导入**：

```bash
# 浏览更多申请人（缓存中又有新数据）
# 缓存现在包含：前 100 条（已导入） + 新增 50 条（未导入）

python import_from_cache.py
# 结果：
# ✓ 新增: 50 条
# → 跳过: 100 条（已有）
# ✗ 失败: 0 条
```

## 📈 与其他 Phase 的联动

### Phase 0 + Phase 1（自动批量采集）

```
Phase 0                    Phase 1                Phase 2
手动按申请人搜索   ←────→   自动按申请号搜索   ←→  采集发文信息
(数量可控，灵活)         (量大，无人值守)      (精准采集)

detection_log.json（统一日志，所有数据汇聚）
```

两个 Phase 采集的数据会自动合并到同一个日志中，无需手动干预。

## ✅ 完成检查表

运行 Phase 0 采集后，检查以下项目：

- [ ] MITM 代理成功启动
- [ ] 浏览器自动打开并配好代理
- [ ] 在 MITM 终端看到"[✓] 已缓存"的输出
- [ ] 成功浏览了多个申请人的搜索结果
- [ ] 浏览器正常关闭
- [ ] `import_from_cache.py` 成功运行，显示"新增 N 条"
- [ ] `detection_log.json` 的记录数增加了
- [ ] `patent_cache.json` 被自动清空（变为 `{}`）

全部勾选后，Phase 0 采集完成！ ✅

## 🎓 技术细节

### 浏览器代理配置原理

```python
options = uc.ChromeOptions()
options.add_argument("--proxy-server=http://127.0.0.1:8080")
options.add_argument("--ignore-certificate-errors")
```

- `--proxy-server`：所有 HTTP/HTTPS 流量都经过 127.0.0.1:8080
- `--ignore-certificate-errors`：MITM 会改变 SSL 证书，所以需要忽略证书验证

### 为什么需要忽略证书错误

```
正常情况：浏览器 ──HTTPS→ CNIPA
MITM 情况：浏览器 ──HTTPS→ MITM（伪造证书）──HTTPS→ CNIPA
```

因为 MITM 要拦截加密流量，所以它会用自己的证书替换真实的证书。浏览器会看到不匹配的证书，所以需要忽略证书检查。

---

**现在你拥有完整的 Phase 0 采集系统了！** 🎉

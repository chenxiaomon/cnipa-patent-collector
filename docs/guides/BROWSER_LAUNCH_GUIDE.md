# 浏览器代理启动完全指南

## 🎯 问题

如何确保浏览器的流量 **100% 经过** MITM 代理？

**关键要素**：
- ✅ 在浏览器启动时指定代理参数
- ✅ 不仅仅依赖系统代理设置
- ✅ 确保所有请求都被拦截

---

## 方案 1️⃣：Python 脚本启动（推荐）

**最简单、最可靠**

### 步骤

#### 终端 1：启动 MITM 代理
```bash
python start_mitm_public_search.py
```

#### 终端 2：启动配置好代理的浏览器
```bash
python launch_browser_with_proxy.py
```

**脚本会**：
1. ✅ 检查 MITM 代理是否运行
2. ✅ 启动 Chrome 浏览器并配置代理到 127.0.0.1:8080
3. ✅ 打开 CNIPA 公开搜索页面
4. ✅ 显示使用说明

**你只需在浏览器中**：
- 输入查询条件
- 点击查询
- 手动点击下一页
- 重复直到完成

---

## 方案 2️⃣：Windows 命令行启动 Chrome

**如果不想用 Python 脚本**

### 步骤 1：找到 Chrome 安装位置

```bash
# Windows 通常在这些位置：
"C:\Program Files\Google\Chrome\Application\chrome.exe"
或
"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
```

### 步骤 2：用代理启动 Chrome

```bash
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --proxy-server=http://127.0.0.1:8080 ^
  --ignore-certificate-errors ^
  "https://cponline.cnipa.gov.cn/publicSearch"
```

**参数说明**：
- `--proxy-server=http://127.0.0.1:8080` → 设置代理
- `--ignore-certificate-errors` → 支持 HTTPS 拦截
- `https://cponline.cnipa.gov.cn/publicSearch` → 自动打开页面

### 步骤 3：保存为批处理文件（可选）

创建 `launch_browser.bat` 文件：

```batch
@echo off
REM 启动配置代理的 Chrome 浏览器

"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --proxy-server=http://127.0.0.1:8080 ^
  --ignore-certificate-errors ^
  --no-sandbox ^
  "https://cponline.cnipa.gov.cn/publicSearch"
```

然后双击运行即可。

---

## 方案 3️⃣：Firefox 命令行启动

### 步骤 1：找到 Firefox 安装位置

```bash
# Windows 通常在：
"C:\Program Files\Mozilla Firefox\firefox.exe"
或
"C:\Program Files (x86)\Mozilla Firefox\firefox.exe"
```

### 步骤 2：用代理启动 Firefox

Firefox 需要通过 profile 配置代理。创建 `launch_firefox.bat`：

```batch
@echo off
REM 启动配置代理的 Firefox 浏览器

set MOZ_DISABLE_XPCONNECT_WORKAROUND=1

"C:\Program Files\Mozilla Firefox\firefox.exe" ^
  --new-instance ^
  "https://cponline.cnipa.gov.cn/publicSearch"

REM Firefox 需要在浏览器设置中手动配置代理：
REM 1. 打开 Firefox
REM 2. 设置 → Network Settings
REM 3. 手动代理：HTTP 127.0.0.1:8080
```

> ⚠️ **注意**：Firefox 的代理配置较复杂，**推荐用 Chrome 或 Python 脚本**。

---

## 📊 三种方案对比

| 方案 | 复杂度 | 可靠性 | 推荐度 |
|------|--------|--------|--------|
| **Python 脚本** | ⭐ 极简 | ⭐⭐⭐ 100% | ✅ 推荐 |
| **Chrome 命令行** | ⭐⭐ 简单 | ⭐⭐⭐ 100% | ✅ 推荐 |
| **Firefox 命令行** | ⭐⭐⭐ 复杂 | ⭐⭐ 80% | ❌ 不推荐 |

---

## ✅ 完整流程（推荐方案）

### 终端 1：启动 MITM 代理
```bash
cd C:\Users\cxm\Desktop\vibe
python start_mitm_public_search.py
```

**输出**：
```
[+] 启动 mitmproxy 公开搜索模式...
[*] 监听地址: 127.0.0.1:8080
按 Ctrl+C 停止服务器
```

### 终端 2：启动浏览器（选择方案）

**方案 A：Python 脚本（最简单）**
```bash
python launch_browser_with_proxy.py
```

**方案 B：Chrome 命令行**
```bash
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --proxy-server=http://127.0.0.1:8080 ^
  --ignore-certificate-errors ^
  "https://cponline.cnipa.gov.cn/publicSearch"
```

### 浏览器中操作

1. 在浏览器中输入查询条件
2. 点击"查询"按钮
3. 看终端 1 的输出（MITM 拦截信息）
4. 手动点击"下一页"按钮
5. 重复直到完成

**终端 1 的输出示例**：
```
[+] 拦截到公开搜索 API: ...publicSearch?pageNo=1
[*] 成功提取 30 条数据
[✓] 本页新增 30 条数据，累计 30 条
[✓] 已保存原始响应: undomestic_0001_20260324_120530.json

[+] 拦截到公开搜索 API: ...publicSearch?pageNo=2
[*] 成功提取 30 条数据
[✓] 本页新增 28 条数据，累计 58 条
```

### 导出数据

采集完成后，运行：
```bash
python export_public_search.py
```

---

## 🔍 验证代理是否生效

### 方法 1：看 MITM 日志

运行浏览器访问任何网站，如果终端 1 显示日志，说明代理工作正常：

```
[DEBUG] CNIPA 请求: GET https://cponline.cnipa.gov.cn/...
[DEBUG] CNIPA 请求: POST https://cponline.cnipa.gov.cn/api/...
```

### 方法 2：访问测试网站

访问 `http://httpbin.org/ip`，应该看到你的本地 IP（说明代理在工作）。

### 方法 3：查看生成的文件

手动点击一次翻页后，检查 `data/raw_responses/` 目录：

```bash
ls -lh data/raw_responses/
```

如果有新的 `undomestic_*.json` 文件，说明代理采集成功。

---

## ⚠️ 常见问题

### Q1: Python 脚本启动浏览器后立即关闭

**原因**：通常是 Chrome 版本不匹配

**解决**：
```bash
# 更新 undetected-chromedriver
pip install --upgrade undetected-chromedriver
```

### Q2: Chrome 命令行启动无效

**原因**：可能是 Chrome 路径错误

**解决**：
```bash
# 查找 Chrome 安装位置
where chrome
# 或者
Get-Command chrome | Select-Object Source  # PowerShell
```

### Q3: 代理配置后浏览器无法访问网站

**原因**：MITM 代理未运行或端口错误

**解决**：
1. 检查终端 1 MITM 代理输出
2. 确认代理地址是 127.0.0.1:8080
3. 重启浏览器

### Q4: HTTPS 页面显示证书错误

**这是正常的** —— MITM 代理拦截 HTTPS 需要重签证书。只要以下选项已配置，就可以忽略：
```
--ignore-certificate-errors
```

---

## 💡 最佳实践

1. **用 Python 脚本**（最简单）
   ```bash
   python launch_browser_with_proxy.py
   ```

2. **如果 Python 脚本有问题**，用命令行
   ```bash
   "C:\Program Files\Google\Chrome\Application\chrome.exe" \
     --proxy-server=http://127.0.0.1:8080 \
     --ignore-certificate-errors \
     "https://cponline.cnipa.gov.cn/publicSearch"
   ```

3. **监控 MITM 日志**，确认流量被拦截

4. **导出数据**
   ```bash
   python export_public_search.py
   ```

---

**总结**：确保浏览器 100% 走过 MITM 代理的秘诀就是**在启动参数中指定代理**，而不是依赖系统设置。✅


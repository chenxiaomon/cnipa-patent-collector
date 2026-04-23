#!/bin/bash

# 快速重试失败申请号的一键脚本

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║        快速重试失败申请号 - 一键启动脚本
║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# 检查重试列表是否存在
if [ ! -f "data/search_list_retry.txt" ]; then
    echo "❌ 错误：data/search_list_retry.txt 不存在"
    echo "请先运行: python3 retry_failed_applications.py"
    exit 1
fi

# 替换搜索列表
echo "【步骤 1】替换搜索列表..."
cp data/search_list_retry.txt data/search_list.txt
count=$(wc -l < data/search_list.txt)
echo "✅ 搜索列表已替换，包含 $count 个申请号"
echo ""

# 显示操作说明
echo "【步骤 2】启动 MITM 代理（在另一个终端执行）"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "$ cd /home/cxm/Desktop/vib3"
echo "$ python start_mitm_proxy.py"
echo ""
echo "预期输出："
echo "  Listening on http://127.0.0.1:8080"
echo ""

# 提示用户准备好后启动采集
echo "【步骤 3】启动采集程序（在第三个终端执行）"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "$ cd /home/cxm/Desktop/vib3"
echo "$ USE_MITM_PROXY=true python main_automation.py"
echo ""

echo "📊 采集参数"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "• 待采集申请号：$count 个"
echo "• 预计耗时：$(($count * 10 / 60)) 分钟 $(($count * 10 % 60)) 秒"
echo "• 防爬虫等待：每 10 条随机等待 2-5 秒"
echo ""

echo "✅ 准备工作已完成！"
echo ""
echo "⚠️  重要提示："
echo "1. 确保 MITM 代理已启动并显示 'Listening on...'"
echo "2. 不要手动关闭浏览器窗口"
echo "3. 采集中断可重新启动，会自动继续"
echo ""


#!/usr/bin/env python3
"""
重试失败的申请号采集脚本

该脚本会：
1. 读取 failed_applications.txt 中的失败申请号
2. 从 detection_log.json 中删除这些失败的记录
3. 重新添加到 search_list.txt 中用于重新采集
4. 为重试采集做准备
"""

import json
import os

def retry_failed_applications():
    """重试失败的申请号"""
    
    failed_file = 'data/failed_applications.txt'
    log_file = 'data/results/detection_log.json'
    search_list = 'data/search_list.txt'
    
    # 1. 读取失败的申请号
    print("📋 读取失败的申请号...")
    if not os.path.exists(failed_file):
        print(f"❌ 文件不存在: {failed_file}")
        return
    
    with open(failed_file, 'r', encoding='utf-8') as f:
        failed_apps = [line.strip() for line in f if line.strip()]
    
    print(f"✅ 找到 {len(failed_apps)} 条失败的申请号")
    
    # 2. 从日志中删除失败的记录
    print(f"\n🔄 从 {log_file} 中删除失败的记录...")
    with open(log_file, 'r', encoding='utf-8') as f:
        log_data = json.load(f)
    
    original_count = len(log_data['records'])
    
    # 保留成功的记录
    log_data['records'] = [
        r for r in log_data['records']
        if r.get('application_no') not in failed_apps
    ]
    
    deleted_count = original_count - len(log_data['records'])
    print(f"✅ 删除了 {deleted_count} 条失败的记录")
    print(f"   剩余 {len(log_data['records'])} 条成功的记录")
    
    # 保存更新后的日志
    with open(log_file, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    print(f"✅ 日志已更新")
    
    # 3. 创建重试搜索列表
    print(f"\n📝 创建重试搜索列表...")
    retry_search_list = 'data/search_list_retry.txt'
    
    with open(retry_search_list, 'w', encoding='utf-8') as f:
        for app_no in failed_apps:
            f.write(app_no + '\n')
    
    print(f"✅ 重试列表已创建: {retry_search_list}")
    print(f"   包含 {len(failed_apps)} 个申请号")
    
    # 4. 显示统计信息
    print("\n" + "="*60)
    print("📊 操作完成统计")
    print("="*60)
    print(f"原始记录数       : {original_count}")
    print(f"删除失败记录     : {deleted_count}")
    print(f"保留成功记录     : {len(log_data['records'])}")
    print(f"新的成功率       : {100*len(log_data['records'])/original_count:.1f}%")
    print()
    print("📌 下一步操作:")
    print(f"1. 检查重试列表: cat {retry_search_list}")
    print(f"2. 替换搜索列表: cp {retry_search_list} {search_list}")
    print(f"3. 启动 MITM: python start_mitm_proxy.py")
    print(f"4. 重新采集: USE_MITM_PROXY=true python main_automation.py")
    print("="*60)

if __name__ == '__main__':
    retry_failed_applications()


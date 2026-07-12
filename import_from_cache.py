#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 0 缓存导入脚本

功能：将 MITM 拦截的缓存数据（patent_cache.json）转换为 DetectionRecord
     并导入到最终日志（detection_log.json）

使用场景：
  1. 用户手动浏览 CNIPA（按申请人搜索），MITM 拦截 API
  2. 缓存数据自动写入 patent_cache.json
  3. 运行本脚本，将缓存导入日志
  4. 缓存清空，准备下一轮采集

使用方式：
  python import_from_cache.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from detection_logger import DetectionLogger, DetectionRecord
from cache_utils import normalize_app_no, read_json_cache
from atomic_write import write_json_atomic
from settings import PATENT_CACHE_FILE, DETECTION_LOG_JSONL_FILE

CACHE_FILE = str(PATENT_CACHE_FILE)
LOG_FILE = str(DETECTION_LOG_JSONL_FILE)


# ============================================================================
# 工具函数
# ============================================================================

def load_cache() -> dict:
    """
    读取 MITM 缓存文件

    Returns:
        缓存字典，格式：{申请号: {专利字段}}
    """
    if not os.path.exists(CACHE_FILE):
        print(f"[!] 缓存文件不存在: {CACHE_FILE}")
        return {}

    cache_data = read_json_cache(CACHE_FILE)
    if not cache_data:
        print(f"[!] 缓存文件格式错误或为空")
        return {}

    print(f"[✓] 缓存已加载: {len(cache_data)} 条记录")
    return cache_data


def load_processed_apps(logger: DetectionLogger) -> set:
    """
    读取日志中已有的申请号集合（标准格式）

    Returns:
        已处理申请号的集合（统一为标准格式）
    """
    processed = logger.get_processed_applications()

    # 将所有已有申请号标准化
    processed_normalized = {normalize_app_no(app) for app in processed}
    processed_normalized.discard(None)  # 移除 None

    return processed_normalized


def convert_cache_to_record(cache_item: dict, app_no_normalized: str) -> DetectionRecord:
    """
    将缓存条目转换为 DetectionRecord

    Args:
        cache_item: 来自 patent_cache.json 的单条数据
        app_no_normalized: 标准化后的申请号

    Returns:
        DetectionRecord 对象
    """
    record = DetectionRecord(
        # 基础字段
        application_no=app_no_normalized,
        status_code=200,  # MITM 拦截的都是 200
        response_time_ms=None,  # 缓存中没有
        detected=False,  # 不是检测，是查询
        response_summary='Phase 0 手动采集（MITM 缓存导入）',
        error_message=None,

        # 14 个专利字段（直接从缓存映射）
        famingzlsqgbg=cache_item.get('famingzlsqgbg'),
        shouquanggh=cache_item.get('shouquanggh'),
        zhuanlimc=cache_item.get('zhuanlimc'),
        shenqingrxm=cache_item.get('shenqingrxm'),
        zhuanlilx=cache_item.get('zhuanlilx'),  # 已在 MITM 中转换
        shenqingr=cache_item.get('shenqingr'),
        gongkaiggh=cache_item.get('gongkaiggh'),
        falvzt=cache_item.get('falvzt'),
        gongkaiggr=cache_item.get('gongkaiggr'),
        shouquanggr=cache_item.get('shouquanggr'),
        zhufenlh=cache_item.get('zhufenlh'),
        anjianbh=cache_item.get('anjianbh'),
        anjianywzt=cache_item.get('anjianywzt'),

        # 发文信息字段（Phase 0 不涉及，均为 None）
        fwxx_list=None,
        bhsjtzs_xiazaisj=None,
        bhsjtzs_data=None,
    )

    return record


def clear_cache() -> bool:
    """
    清空缓存文件（导入成功后执行）

    Returns:
        成功返回 True，否则 False
    """
    try:
        write_json_atomic(CACHE_FILE, {})
        print(f"[✓] 缓存已清空")
        return True
    except Exception as e:
        print(f"[!] 清空缓存失败: {e}")
        return False


# ============================================================================
# 主导入逻辑
# ============================================================================

def import_from_cache() -> bool:
    """
    主导入函数：缓存 → DetectionRecord → 日志

    Returns:
        导入成功返回 True，否则 False
    """
    print("\n" + "="*70)
    print("📥 Phase 0 缓存导入程序")
    print("="*70)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 步骤 1：加载数据
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    print("\n[*] 加载缓存...")
    cache_data = load_cache()

    if not cache_data:
        print("[!] 缓存为空，无数据可导入")
        return False

    logger = DetectionLogger(LOG_FILE)

    print("\n[*] 加载日志...")
    processed_normalized = load_processed_apps(logger)
    print(f"[✓] 日志中已有 {len(processed_normalized)} 条记录")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 步骤 2：转换后批量导入（单事务，替代逐条 add_record 的逐条建连开销）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    imported = 0
    skipped = 0
    failed = 0
    new_records = []

    print("\n[*] 开始导入...")
    print("-" * 70)

    for idx, (cache_app_no, cache_item) in enumerate(cache_data.items(), 1):
        # 标准化申请号
        normalized = normalize_app_no(cache_app_no)

        if not normalized:
            print(f"  [{idx}] [!] 申请号格式错误: {cache_app_no}")
            failed += 1
            continue

        # 检查是否已存在
        if normalized in processed_normalized:
            print(f"  [{idx}] [→] 跳过已有: {normalized}")
            skipped += 1
            continue

        # 转换为 DetectionRecord
        try:
            new_records.append(convert_cache_to_record(cache_item, normalized))
            print(f"  [{idx}] [✓] 已转换: {normalized} - {cache_item.get('zhuanlimc', 'N/A')[:30]}")
        except Exception as e:
            failed += 1
            print(f"  [{idx}] [!] 转换失败: {normalized} - {str(e)[:50]}")

    try:
        imported = logger.add_records(new_records)
    except Exception as e:
        print(f"  [!] 批量写入失败: {e}")
        failed += len(new_records)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 步骤 3：统计和清理
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    print("-" * 70)
    print("\n" + "="*70)
    print("📊 导入统计")
    print("="*70)
    print(f"✓ 新增: {imported} 条")
    print(f"→ 跳过: {skipped} 条（已有）")
    print(f"✗ 失败: {failed} 条")
    print(f"📈 总处理: {imported + skipped + failed} 条")

    # 只要处理过（无论新增还是跳过）就清空缓存，避免旧数据下次重复扫描
    if imported > 0 or skipped > 0:
        print("\n[*] 清理缓存...")
        clear_cache()

    print("\n" + "="*70)

    return imported > 0 or skipped > 0


# ============================================================================
# 入口
# ============================================================================

if __name__ == '__main__':
    success = import_from_cache()

    if success:
        print("\n✅ 导入完成！数据已写入 patents.db")
        sys.exit(0)
    else:
        print("\n[!] 导入失败或无数据")
        sys.exit(1)

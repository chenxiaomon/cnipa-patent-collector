#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
专利数据内存缓存模块

用途：
- MITM 脚本拦截 API 响应后，存入缓存
- main_automation.py 创建 DetectionRecord 时，查询缓存获取专利数据
- 实现两个独立模块的数据同步

好处：
- 解除过度解耦的问题
- 避免重复查询或写入
- 支持实时数据流转
"""

import threading
from typing import Optional, Dict, Any


class PatentDataCache:
    """
    单例模式的专利数据缓存

    在 MITM 拦截器和主自动化程序间传递数据
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """单例实现"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """初始化缓存"""
        if not self._initialized:
            self._data: Dict[str, Dict[str, Any]] = {}
            self._lock = threading.Lock()
            self._initialized = True

    def add_patent_data(self, application_no: str, patent_data: dict) -> None:
        """
        MITM 脚本调用此方法存入拦截到的专利数据

        Args:
            application_no: 申请号（唯一标识）
            patent_data: API 返回的专利数据字典
        """
        with self._lock:
            self._data[application_no] = patent_data
            print(f"[✓] 缓存更新: {application_no}")

    def get_patent_data(self, application_no: str) -> Optional[dict]:
        """
        main_automation.py 调用此方法获取缓存的专利数据

        Args:
            application_no: 申请号

        Returns:
            专利数据字典，如果不存在返回 None
        """
        with self._lock:
            return self._data.get(application_no)

    def has_data(self, application_no: str) -> bool:
        """
        检查是否有指定申请号的缓存数据

        Args:
            application_no: 申请号

        Returns:
            True 如果有缓存，False 否则
        """
        with self._lock:
            return application_no in self._data

    def get_cache_size(self) -> int:
        """获取缓存中的数据条数"""
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        """清空缓存（用于测试）"""
        with self._lock:
            count = len(self._data)
            self._data.clear()
            print(f"[✓] 缓存已清空（{count} 条数据）")

    def get_all_data(self) -> dict:
        """获取全部缓存数据（用于调试）"""
        with self._lock:
            return dict(self._data)

    def print_status(self) -> None:
        """打印缓存状态"""
        with self._lock:
            print(f"\n{'='*60}")
            print(f"📊 专利数据缓存状态")
            print(f"{'='*60}")
            print(f"缓存条数: {len(self._data)}")
            if len(self._data) > 0:
                print(f"申请号样本 (前 5 个):")
                for i, app_no in enumerate(list(self._data.keys())[:5], 1):
                    print(f"  [{i}] {app_no}")
            print(f"{'='*60}\n")


# 全局缓存实例
_cache = PatentDataCache()


def get_cache() -> PatentDataCache:
    """获取全局缓存实例"""
    return _cache


if __name__ == '__main__':
    # 测试示例
    cache = PatentDataCache()

    # 模拟 MITM 脚本存入数据
    test_data = {
        'zhuanlisqh': 'CN202310641887.1',
        'zhuanlimc': '一种步态训练双梯',
        'shenqingrxm': '黄山金富医疗器械有限公司',
        'zhuanlilx': '发明',
        'shenqingr': '2023-06-01',
        'famingzlsqgbg': 'CN116889710A',
        'shouquanggh': None,
        'gongkaiggh': None,
        'falvzt': '--',
        'gongkaiggr': None,
        'shouquanggr': None,
        'zhufenlh': None,
        'anjianbh': None,
        'anjianywzt': None,
    }

    cache.add_patent_data('CN202310641887.1', test_data)
    print(f"\n✅ 数据存入缓存")

    # 模拟 main_automation.py 查询数据
    data = cache.get_patent_data('CN202310641887.1')
    if data:
        print(f"✅ 数据查询成功")
        print(f"   申请号: {data.get('zhuanlisqh')}")
        print(f"   名称: {data.get('zhuanlimc')}")
        print(f"   申请人: {data.get('shenqingrxm')}")
    else:
        print(f"❌ 数据查询失败")

    # 查看缓存状态
    cache.print_status()

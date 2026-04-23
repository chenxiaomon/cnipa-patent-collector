#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
检测数据记录模块
- 实时写入检测结果到 JSON 日志
- 支持断点续传（跳过已处理的申请号）
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

try:
    import pandas as pd
except ImportError:
    pd = None


class DetectionRecord:
    """单条检测记录（包含完整的专利信息字段）"""

    def __init__(
        self,
        application_no: str,
        status_code: Optional[int] = None,
        response_time_ms: Optional[float] = None,
        detected: Optional[bool] = None,
        response_summary: Optional[str] = None,
        error_message: Optional[str] = None,
        # 14 个专利信息字段
        famingzlsqgbg: Optional[str] = None,  # 发明公布号
        shouquanggh: Optional[str] = None,    # 授权公告号
        zhuanlimc: Optional[str] = None,      # 专利名称
        shenqingrxm: Optional[str] = None,    # 申请人
        zhuanlilx: Optional[str] = None,      # 专利类型
        shenqingr: Optional[str] = None,      # 申请日
        gongkaiggh: Optional[str] = None,     # 公开公告号
        falvzt: Optional[str] = None,         # 法律状态
        gongkaiggr: Optional[str] = None,     # 公开公告日
        shouquanggr: Optional[str] = None,    # 授权公告日
        zhufenlh: Optional[str] = None,       # 主分类号
        anjianbh: Optional[str] = None,       # 案件编号
        anjianywzt: Optional[str] = None,     # 案件业务状态
        # 发文信息字段（仅在状态="驳回等复审请求"时填充）
        fwxx_list: Optional[list] = None,     # 完整的发文列表
        bhsjtzs_xiazaisj: Optional[str] = None,  # 驳回决定的时间
        bhsjtzs_data: Optional[dict] = None,     # 驳回决定的完整对象
    ):
        self.application_no = application_no
        self.status_code = status_code
        self.response_time_ms = response_time_ms
        self.detected = detected
        self.response_summary = response_summary
        self.error_message = error_message
        self.timestamp = datetime.utcnow().isoformat() + 'Z'

        # 专利字段
        self.famingzlsqgbg = famingzlsqgbg
        self.shouquanggh = shouquanggh
        self.zhuanlimc = zhuanlimc
        self.shenqingrxm = shenqingrxm
        self.zhuanlilx = zhuanlilx
        self.shenqingr = shenqingr
        self.gongkaiggh = gongkaiggh
        self.falvzt = falvzt
        self.gongkaiggr = gongkaiggr
        self.shouquanggr = shouquanggr
        self.zhufenlh = zhufenlh
        self.anjianbh = anjianbh
        self.anjianywzt = anjianywzt

        # 发文信息字段
        self.fwxx_list = fwxx_list
        self.bhsjtzs_xiazaisj = bhsjtzs_xiazaisj
        self.bhsjtzs_data = bhsjtzs_data

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'application_no': self.application_no,
            'status_code': self.status_code,
            'response_time_ms': self.response_time_ms,
            'detected': self.detected,
            'response_summary': self.response_summary,
            'timestamp': self.timestamp,
            'error_message': self.error_message,
            # 14 个专利字段
            'famingzlsqgbg': self.famingzlsqgbg,
            'shouquanggh': self.shouquanggh,
            'zhuanlimc': self.zhuanlimc,
            'shenqingrxm': self.shenqingrxm,
            'zhuanlilx': self.zhuanlilx,
            'shenqingr': self.shenqingr,
            'gongkaiggh': self.gongkaiggh,
            'falvzt': self.falvzt,
            'gongkaiggr': self.gongkaiggr,
            'shouquanggr': self.shouquanggr,
            'zhufenlh': self.zhufenlh,
            'anjianbh': self.anjianbh,
            'anjianywzt': self.anjianywzt,
            # 发文信息字段
            'fwxx_list': self.fwxx_list,
            'bhsjtzs_xiazaisj': self.bhsjtzs_xiazaisj,
            'bhsjtzs_data': self.bhsjtzs_data,
        }


class DetectionLogger:
    """检测数据日志记录器"""

    def __init__(self, log_file: str = None):
        """
        初始化日志记录器

        Args:
            log_file: 日志文件路径，默认为 data/results/detection_log.json
        """
        if log_file is None:
            log_file = os.path.join(
                os.path.dirname(__file__),
                'data', 'results', 'detection_log.json'
            )

        self.log_file = log_file
        self.log_dir = os.path.dirname(log_file)

        # 确保目录存在
        os.makedirs(self.log_dir, exist_ok=True)

        # 初始化或加载日志
        self._init_log()

    def _init_log(self):
        """初始化日志文件"""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
                    if 'records' not in self.data:
                        self.data = {'records': []}
            except json.JSONDecodeError:
                self.data = {'records': []}
        else:
            self.data = {'records': []}

    def add_record(self, record: DetectionRecord) -> None:
        """添加一条记录并立即保存"""
        self.data['records'].append(record.to_dict())
        self._save()

    def _save(self) -> None:
        """保存日志到文件"""
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get_processed_applications(self) -> set:
        """获取已处理的申请号集合"""
        return {record['application_no'] for record in self.data['records']}

    def get_pending_applications(self, all_applications: list) -> list:
        """
        获取未处理的申请号列表

        Args:
            all_applications: 所有申请号列表

        Returns:
            未处理的申请号列表
        """
        processed = self.get_processed_applications()
        return [app for app in all_applications if app not in processed]

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        records = self.data['records']
        if not records:
            return {
                'total': 0,
                'success': 0,
                'failed': 0,
                'detected': 0,
                'average_response_time_ms': 0,
            }

        success_count = sum(1 for r in records if r['status_code'] == 200)
        failed_count = len(records) - success_count
        detected_count = sum(1 for r in records if r['detected'])

        response_times = [r['response_time_ms'] for r in records if r['response_time_ms']]
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0

        return {
            'total': len(records),
            'success': success_count,
            'failed': failed_count,
            'detected': detected_count,
            'average_response_time_ms': round(avg_response_time, 2),
        }

    def print_summary(self) -> None:
        """打印统计摘要"""
        stats = self.get_stats()

        print("\n" + "="*60)
        print("📊 检测数据统计")
        print("="*60)
        print(f"总计处理: {stats['total']} 个申请号")
        print(f"成功: {stats['success']} 个 ({100*stats['success']//max(1,stats['total'])}%)")
        print(f"失败: {stats['failed']} 个")
        print(f"被检测: {stats['detected']} 个")
        print(f"平均响应时间: {stats['average_response_time_ms']}ms")
        print("="*60 + "\n")

    def export_to_excel(self, excel_file: str = None) -> bool:
        """
        导出记录到 Excel 文件（两个 Sheet）

        Sheet1：专利主信息（所有记录）
        Sheet2：发文信息（仅包含 fwxx_list 不为空的记录）

        Args:
            excel_file: 输出文件路径，默认为 data/results/patents_data.xlsx

        Returns:
            成功返回 True，否则 False
        """
        if pd is None:
            print("❌ pandas 未安装，无法导出 Excel")
            return False

        try:
            if excel_file is None:
                excel_file = os.path.join(
                    os.path.dirname(self.log_file),
                    'patents_data.xlsx'
                )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Sheet1：专利主信息
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

            column_mapping = {
                'application_no': '专利申请号',
                'famingzlsqgbg': '发明公布号',
                'shouquanggh': '授权公告号',
                'zhuanlimc': '专利名称',
                'shenqingrxm': '申请人',
                'zhuanlilx': '专利类型',
                'shenqingr': '申请日',
                'gongkaiggh': '公开公告号',
                'falvzt': '法律状态',
                'gongkaiggr': '公开公告日',
                'shouquanggr': '授权公告日',
                'zhufenlh': '主分类号',
                'anjianbh': '案件编号',
                'anjianywzt': '案件业务状态',
                'status_code': '状态码',
                'response_time_ms': '响应时间(ms)',
                'timestamp': '时间戳',
                'fwxx_list': '发文列表',
                'bhsjtzs_xiazaisj': '驳回时间',
                'bhsjtzs_data': '驳回决定详情',
            }

            # 转换为 DataFrame
            df = pd.DataFrame(self.data['records'])

            # 重命名列
            df = df.rename(columns=column_mapping)

            # 只保留有用的列
            keep_cols = [v for v in column_mapping.values() if v in df.columns]
            df = df[keep_cols]

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Sheet2：发文信息（展开）
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

            fwxx_rows = []
            fwxx_column_mapping = {
                'tongzhismc': '通知书名称',
                'fawenr': '发文日',
                'shoujianrxm': '收件人姓名',
                'shoujianryb': '收件人邮编',
                'fawenfs': '发文方式',
                'xiazaisj': '下载时间',
                'xiazaiip': '下载IP',
            }

            for record in self.data['records']:
                fwxx_list = record.get('fwxx_list')
                if fwxx_list:
                    app_no = record.get('application_no')
                    # 展开为多行
                    for fwxx_item in fwxx_list:
                        row = {'专利申请号': app_no}
                        for key, col_name in fwxx_column_mapping.items():
                            row[col_name] = fwxx_item.get(key)
                        fwxx_rows.append(row)

            # 保存到 Excel（两个 Sheet）
            with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
                # Sheet1：专利主信息
                df.to_excel(writer, sheet_name='专利主信息', index=False)

                # Sheet2：发文信息（仅当有数据时）
                if fwxx_rows:
                    df_fwxx = pd.DataFrame(fwxx_rows)
                    df_fwxx.to_excel(writer, sheet_name='发文信息', index=False)

            print(f"✅ 数据已导出至: {excel_file}")
            if fwxx_rows:
                print(f"   Sheet1: 专利主信息 ({len(df)} 条)")
                print(f"   Sheet2: 发文信息 ({len(fwxx_rows)} 条)")
            else:
                print(f"   Sheet1: 专利主信息 ({len(df)} 条)")
                print(f"   (无发文信息数据)")

            return True

        except Exception as e:
            print(f"❌ 导出 Excel 失败: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == '__main__':
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DetectionLogger(os.path.join(tmpdir, 'test_log.json'))

        record1 = DetectionRecord(
            application_no='CN202310641887.1',
            status_code=200,
            response_time_ms=2345,
            detected=False,
            response_summary='Success - Record found',
        )
        logger.add_record(record1)

        record2 = DetectionRecord(
            application_no='CN202310869634.X',
            status_code=403,
            detected=True,
            error_message='Request blocked by anti-crawler system',
        )
        logger.add_record(record2)

        logger.print_summary()
        print("\n[测试完成，日志写入临时目录]")

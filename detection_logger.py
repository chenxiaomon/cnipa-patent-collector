#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
检测数据记录模块
- 主存储：SQLite（data/patents.db），支持 O(1) upsert 和索引查询
- 双写备份：add_record() 同时追加到 JSONL（用于 git 追踪）
- upsert_record() 仅写 DB，彻底消除 JSONL 重写写放大
- 公共接口与旧版完全兼容，所有调用方无需修改
"""

import json
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from settings import DETECTION_LOG_JSONL_FILE, PATENTS_DB_FILE
from atomic_write import write_json_atomic
from db_manager import PatentsDB
from cache_utils import normalize_app_no as _normalize_app_no
from payment_obligations import (
    LATE_FEE_APPLICABLE,
    LATE_FEE_INVALID_INTERVAL,
    LATE_FEE_MULTIPLE_APPLICABLE_BRACKETS,
    LATE_FEE_NOT_COLLECTED,
    LATE_FEE_NO_APPLICABLE_BRACKET,
    LATE_FEE_NO_SCHEDULE,
    build_current_late_fee_analysis,
    build_payable_fee_analysis,
)


try:
    import pandas as pd
except ImportError:
    pd = None


_CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


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
        famingzlsqgbg: Optional[str] = None,
        shouquanggh: Optional[str] = None,
        zhuanlimc: Optional[str] = None,
        shenqingrxm: Optional[str] = None,
        zhuanlilx: Optional[str] = None,
        shenqingr: Optional[str] = None,
        gongkaiggh: Optional[str] = None,
        falvzt: Optional[str] = None,
        gongkaiggr: Optional[str] = None,
        shouquanggr: Optional[str] = None,
        zhufenlh: Optional[str] = None,
        anjianbh: Optional[str] = None,
        anjianywzt: Optional[str] = None,
        fwxx_list: Optional[list] = None,
        bhsjtzs_xiazaisj: Optional[str] = None,
        bhsjtzs_data: Optional[dict] = None,
        paid_fee_records: Optional[list] = None,
        fee_receipt_dispatch_records: Optional[list] = None,
        daili_jg: Optional[str] = None,
        daili_r: Optional[str] = None,
        payable_fee_records: Optional[list] = None,
        late_fee_schedule_records: Optional[list] = None,
        fee_snapshot_at: Optional[str] = None,
    ):
        self.application_no = application_no
        self.status_code = status_code
        self.response_time_ms = response_time_ms
        self.detected = detected
        self.response_summary = response_summary
        self.error_message = error_message
        self.timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

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

        self.fwxx_list = fwxx_list
        self.bhsjtzs_xiazaisj = bhsjtzs_xiazaisj
        self.bhsjtzs_data = bhsjtzs_data
        self.payable_fee_records = payable_fee_records
        self.late_fee_schedule_records = late_fee_schedule_records
        self.paid_fee_records = paid_fee_records
        self.fee_receipt_dispatch_records = fee_receipt_dispatch_records
        self.fee_snapshot_at = fee_snapshot_at
        self.daili_jg = daili_jg
        self.daili_r = daili_r

    def to_dict(self) -> Dict[str, Any]:
        return {
            'application_no': self.application_no,
            'status_code': self.status_code,
            'response_time_ms': self.response_time_ms,
            'detected': self.detected,
            'response_summary': self.response_summary,
            'timestamp': self.timestamp,
            'error_message': self.error_message,
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
            'fwxx_list': self.fwxx_list,
            'bhsjtzs_xiazaisj': self.bhsjtzs_xiazaisj,
            'bhsjtzs_data': self.bhsjtzs_data,
            'payable_fee_records': self.payable_fee_records,
            'late_fee_schedule_records': self.late_fee_schedule_records,
            'paid_fee_records': self.paid_fee_records,
            'fee_receipt_dispatch_records': self.fee_receipt_dispatch_records,
            'fee_snapshot_at': self.fee_snapshot_at,
            'daili_jg': self.daili_jg,
            'daili_r': self.daili_r,
        }


class DetectionLogger:
    """检测数据日志记录器（SQLite 主存储 + JSONL 双写备份）"""

    def __init__(self, log_file: str = None):
        if log_file is None:
            log_file = str(DETECTION_LOG_JSONL_FILE)

        # 兼容旧调用：若传入 .json 路径，自动转为 .jsonl
        if log_file.endswith('.json') and not log_file.endswith('.jsonl'):
            log_file = log_file[:-5] + '.jsonl'

        self.log_file = log_file
        self.log_dir = os.path.dirname(log_file)
        os.makedirs(self.log_dir, exist_ok=True)

        Path(self.log_file).touch()

        self._db = PatentsDB(PATENTS_DB_FILE)
        self._writes_since_backup = 0

    # ------------------------------------------------------------------
    # 读（内部）
    # ------------------------------------------------------------------

    def _load_records(self) -> list:
        """从 SQLite 读取全部记录（仅导出时调用）"""
        return self._db.get_all_records()

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def add_record(self, record: DetectionRecord) -> None:
        """
        追加一条新记录。
        - DB：ON CONFLICT upsert（O(1)，传入 NULL 不擦除已有业务字段）
        - JSONL：追加（双写备份，用于 git 追踪，O(1)）
        """
        d = record.to_dict()
        d['application_no'] = _normalize_app_no(d['application_no'])

        # 1. 写 SQLite
        self._db.upsert(d)

        # 2. 追加 JSONL（双写备份）。不 fsync：DB 已提交（SSOT），
        #    JSONL 尾行丢失可由 export_to_jsonl() 完整重建
        line = json.dumps(d, ensure_ascii=False) + '\n'
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(line)

        self._auto_backup()

    def upsert_record(self, record: DetectionRecord) -> None:
        """
        更新已有记录（按 application_no），不存在则插入。
        - 仅写 SQLite，O(1)，彻底消除旧方案 O(n) JSONL 重写瓶颈
        - 用于强制重查模式
        """
        d = record.to_dict()
        d['application_no'] = _normalize_app_no(d['application_no'])
        self._db.upsert(d)
        self._auto_backup()

    def add_records(self, records: list) -> int:
        """批量追加：一次事务写 DB + 一次打开追加 JSONL。语义等同逐条 add_record。"""
        rows = []
        for record in records:
            d = record.to_dict()
            d['application_no'] = _normalize_app_no(d['application_no'])
            rows.append(d)
        if not rows:
            return 0
        self._db.upsert_batch(rows)
        with open(self.log_file, 'a', encoding='utf-8') as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + '\n')
        self._auto_backup(written=len(rows))
        return len(rows)

    # ------------------------------------------------------------------
    # 备份（本进程每写 500 条自动备份一次 JSONL）
    # ------------------------------------------------------------------

    def _auto_backup(self, written: int = 1, interval: int = 500) -> None:
        self._writes_since_backup += written
        if self._writes_since_backup < interval:
            return
        self._writes_since_backup = 0
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup = self.log_file.replace('.jsonl', f'_backup_{timestamp}.jsonl')
        shutil.copy2(self.log_file, backup)
        print(f"[✓] 自动备份: {os.path.basename(backup)} ({self._db.count()} 条)")
        self._prune_backups()

    def _prune_backups(self, keep: int = 5) -> None:
        import glob
        pattern = self.log_file.replace('.jsonl', '_backup_*.jsonl')
        for old in sorted(glob.glob(pattern))[:-keep]:
            try:
                os.remove(old)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 查询（公共接口，接口与旧版完全兼容）
    # ------------------------------------------------------------------

    def get_processed_applications(self) -> set:
        return self._db.get_processed_app_nos()

    def get_pending_applications(self, all_applications: list) -> list:
        processed = self.get_processed_applications()
        return [a for a in all_applications if _normalize_app_no(a) not in processed]

    def get_stats(self) -> Dict[str, Any]:
        return self._db.get_stats()

    def print_summary(self) -> None:
        stats = self.get_stats()
        print("\n" + "="*60)
        print("📊 数据库历史累计统计")
        print("="*60)
        print(f"总计处理: {stats['total']} 个申请号")
        print(f"成功: {stats['success']} 个 ({100*stats['success']//max(1,stats['total'])}%)")
        print(f"失败: {stats['failed']} 个")
        print(f"待采: {stats['pending']} 个")
        print(f"被检测: {stats['detected']} 个")
        print(f"平均响应时间: {stats['average_response_time_ms']}ms")
        print("="*60 + "\n")

    # ------------------------------------------------------------------
    # 导出（内部改为从 DB 读取）
    # ------------------------------------------------------------------

    def export_to_excel(
        self,
        excel_file: str = None,
        fee_analysis_date: Optional[date] = None,
    ) -> bool:
        """导出专利主信息、发文信息、四类费用明细和待缴分析。"""
        if pd is None:
            print("❌ pandas 未安装，无法导出 Excel")
            return False
        try:
            if excel_file is None:
                excel_file = os.path.join(os.path.dirname(self.log_file), 'patents_data.xlsx')

            records = self._load_records()
            analysis_date = fee_analysis_date or datetime.now(_CHINA_STANDARD_TIME).date()

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
                'payable_fee_records': '应缴费记录',
                'late_fee_schedule_records': '应缴滞纳金记录',
                'paid_fee_records': '已缴费记录',
                'fee_receipt_dispatch_records': '收据发文记录',
                'fee_snapshot_at': '费用采集时间',
                'daili_jg': '代理机构',
                'daili_r': '代理人',
            }

            # 读取手动补录的企业实际专利总数
            try:
                from settings import COMPANY_META_FILE
                import json as _json
                _meta_raw = COMPANY_META_FILE.read_text(encoding='utf-8') if COMPANY_META_FILE.exists() else '{}'
                _company_meta = _json.loads(_meta_raw)
            except Exception:
                _company_meta = {}
            real_total_map = {
                name: info.get('real_total')
                for name, info in _company_meta.items()
                if isinstance(info, dict) and info.get('real_total') is not None
            }

            df = pd.DataFrame(records)
            df = df.rename(columns=column_mapping)
            keep_cols = [v for v in column_mapping.values() if v in df.columns]
            df = df[keep_cols]

            # 在「申请人」列后插入「企业实际专利总数」列（无补录数据则留空）
            if '申请人' in df.columns:
                df.insert(
                    df.columns.get_loc('申请人') + 1,
                    '企业实际专利总数',
                    df['申请人'].map(lambda x: real_total_map.get(x, '') if pd.notna(x) else '')
                )

            fwxx_column_mapping = {
                'tongzhismc': '通知书名称', 'fawenr': '发文日',
                'shoujianrxm': '收件人姓名', 'shoujianryb': '收件人邮编',
                'fawenfs': '发文方式', 'xiazaisj': '下载时间', 'xiazaiip': '下载IP',
            }
            fwxx_rows = []
            for record in records:
                fwxx_list = record.get('fwxx_list')
                if fwxx_list:
                    app_no = record.get('application_no')
                    for item in fwxx_list:
                        row = {'专利申请号': app_no}
                        for k, col in fwxx_column_mapping.items():
                            row[col] = item.get(k)
                        fwxx_rows.append(row)

            payable_fee_column_mapping = {
                'yingjiaoffyzlmc': '费用种类',
                'yingjiaoje': '应缴金额',
                'jiaofeijzr': '缴费截止日',
                'yingjiaoffyzt': '费用状态',
            }
            payable_fee_rows = []
            for record in records:
                app_no = record.get('application_no')
                for item in record.get('payable_fee_records') or []:
                    row = {'专利申请号': app_no}
                    for key, column in payable_fee_column_mapping.items():
                        row[column] = item.get(key)
                    row['费用采集时间'] = record.get('fee_snapshot_at')
                    payable_fee_rows.append(row)

            late_fee_column_mapping = {
                'zhinajjfsj': '缴费时间区间',
                'zhinajdqnfje': '当前年费金额',
                'zhinajyjznje': '应缴滞纳金额',
                'zhinajzj': '总计',
            }
            late_fee_schedule_rows = []
            for record in records:
                app_no = record.get('application_no')
                for item in record.get('late_fee_schedule_records') or []:
                    row = {'专利申请号': app_no}
                    for key, column in late_fee_column_mapping.items():
                        row[column] = item.get(key)
                    row['费用采集时间'] = record.get('fee_snapshot_at')
                    late_fee_schedule_rows.append(row)

            payable_analysis_column_mapping = {
                'analysis_date': '分析日期',
                'application_no': '专利申请号',
                'zhuanlimc': '专利名称',
                'shenqingrxm': '申请人',
                'zhuanlilx': '专利类型',
                'anjianywzt': '案件业务状态',
                'yingjiaoffyzlmc': '费用种类',
                'yingjiaoje': '应缴金额',
                'jiaofeijzr': '缴费截止日',
                'yingjiaoffyzt': '费用状态',
                'days_to_deadline': '距截止日天数',
                'deadline_bucket': '处理分类',
                'fee_snapshot_at': '费用采集时间',
            }
            payable_analysis_rows = [
                {
                    column: obligation.get(field)
                    for field, column in payable_analysis_column_mapping.items()
                }
                for obligation in build_payable_fee_analysis(records, analysis_date)
            ]

            late_fee_status_labels = {
                LATE_FEE_APPLICABLE: '当前适用',
                LATE_FEE_NOT_COLLECTED: '接口未提供或尚未采集',
                LATE_FEE_NO_SCHEDULE: '无滞纳金区间',
                LATE_FEE_NO_APPLICABLE_BRACKET: '当前无适用区间',
                LATE_FEE_INVALID_INTERVAL: '区间无法解析，需核验',
                LATE_FEE_MULTIPLE_APPLICABLE_BRACKETS: '多个区间同时适用，需核验',
            }
            late_fee_analysis_column_mapping = {
                'analysis_date': '分析日期',
                'application_no': '专利申请号',
                'zhuanlimc': '专利名称',
                'shenqingrxm': '申请人',
                'anjianywzt': '案件业务状态',
                'late_fee_analysis_status': '滞纳金分析状态',
                'zhinajjfsj': '当前适用区间',
                'interval_start': '区间开始日',
                'interval_end': '区间结束日',
                'zhinajdqnfje': '当前年费金额',
                'zhinajyjznje': '应缴滞纳金额',
                'zhinajzj': '总计',
                'invalid_interval_count': '无效区间数',
                'applicable_bracket_count': '适用档数',
                'fee_snapshot_at': '费用采集时间',
            }
            fee_snapshot_records = [
                record
                for record in records
                if record.get('fee_snapshot_at')
                or record.get('late_fee_schedule_records') is not None
            ]
            late_fee_analysis_rows = []
            for late_fee_analysis in build_current_late_fee_analysis(
                fee_snapshot_records,
                analysis_date,
            ):
                row = {
                    column: late_fee_analysis.get(field)
                    for field, column in late_fee_analysis_column_mapping.items()
                }
                row['滞纳金分析状态'] = late_fee_status_labels.get(
                    late_fee_analysis.get('late_fee_analysis_status'),
                    late_fee_analysis.get('late_fee_analysis_status'),
                )
                late_fee_analysis_rows.append(row)

            paid_fee_column_mapping = {
                'yijiaofjfzlmc': '费用种类',
                'yijiaofjfje': '应缴金额',
                'yijiaofjfrq': '缴费日期',
                'yijiaofjfrxm': '缴费人姓名',
                'yijiaofpjdm': '票据代码',
                'yijiaofpjhm': '票据号码',
            }
            paid_fee_rows = []
            for record in records:
                app_no = record.get('application_no')
                for item in record.get('paid_fee_records') or []:
                    row = {'专利申请号': app_no}
                    for key, column in paid_fee_column_mapping.items():
                        value = item.get(key)
                        if key in {'yijiaofpjdm', 'yijiaofpjhm'} and value is not None:
                            value = str(value)
                        row[column] = value
                    paid_fee_rows.append(row)

            receipt_dispatch_column_mapping = {
                'shoujufwfyzlmc': '费用种类',
                'shoujufwjfje': '缴费金额',
                'shoujufwjfrxm': '缴费人姓名',
                'shoujufwjfsj': '缴费时间',
                'shoujufwsjh': '收据号',
                'shoujufwsjtt': '收据抬头',
                'shoujufwyjdz': '收据邮寄地址',
                'shoujufwtkrq': '汇款日期',
                'shoujufwsfjc': '是否寄出',
                'shoujufwfwrq': '发文日期',
                'shoujufwghhm': '挂号号码',
                'shoujufwtkhcrq': '退款汇出日期',
            }
            fee_receipt_dispatch_rows = []
            for record in records:
                app_no = record.get('application_no')
                for item in record.get('fee_receipt_dispatch_records') or []:
                    row = {'专利申请号': app_no}
                    for key, column in receipt_dispatch_column_mapping.items():
                        value = item.get(key)
                        if key in {'shoujufwsjh', 'shoujufwghhm'} and value is not None:
                            value = str(value)
                        row[column] = value
                    fee_receipt_dispatch_rows.append(row)

            with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='专利主信息', index=False)
                if fwxx_rows:
                    pd.DataFrame(fwxx_rows).to_excel(writer, sheet_name='发文信息', index=False)
                if payable_fee_rows:
                    pd.DataFrame(payable_fee_rows).to_excel(
                        writer,
                        sheet_name='应缴费信息',
                        index=False,
                    )
                    payable_fee_sheet = writer.sheets['应缴费信息']
                    payable_fee_sheet.freeze_panes = 'A2'
                    payable_fee_sheet.auto_filter.ref = payable_fee_sheet.dimensions
                    for column, width in {
                        'A': 16, 'B': 42, 'C': 14, 'D': 14, 'E': 12, 'F': 24,
                    }.items():
                        payable_fee_sheet.column_dimensions[column].width = width
                    for cell in payable_fee_sheet['A'][1:]:
                        cell.number_format = '@'
                if late_fee_schedule_rows:
                    pd.DataFrame(late_fee_schedule_rows).to_excel(
                        writer,
                        sheet_name='应缴滞纳金信息',
                        index=False,
                    )
                    late_fee_sheet = writer.sheets['应缴滞纳金信息']
                    late_fee_sheet.freeze_panes = 'A2'
                    late_fee_sheet.auto_filter.ref = late_fee_sheet.dimensions
                    for column, width in {
                        'A': 16, 'B': 34, 'C': 16, 'D': 16, 'E': 16, 'F': 24,
                    }.items():
                        late_fee_sheet.column_dimensions[column].width = width
                    for cell in late_fee_sheet['A'][1:]:
                        cell.number_format = '@'
                if payable_analysis_rows:
                    pd.DataFrame(payable_analysis_rows).to_excel(
                        writer,
                        sheet_name='待缴费分析',
                        index=False,
                    )
                    payable_analysis_sheet = writer.sheets['待缴费分析']
                    payable_analysis_sheet.freeze_panes = 'A2'
                    payable_analysis_sheet.auto_filter.ref = payable_analysis_sheet.dimensions
                    for column, width in {
                        'A': 12, 'B': 16, 'C': 42, 'D': 36, 'E': 12,
                        'F': 20, 'G': 40, 'H': 14, 'I': 14, 'J': 12,
                        'K': 14, 'L': 14, 'M': 24,
                    }.items():
                        payable_analysis_sheet.column_dimensions[column].width = width
                    for cell in payable_analysis_sheet['B'][1:]:
                        cell.number_format = '@'
                if late_fee_analysis_rows:
                    pd.DataFrame(late_fee_analysis_rows).to_excel(
                        writer,
                        sheet_name='当前滞纳金',
                        index=False,
                    )
                    late_fee_analysis_sheet = writer.sheets['当前滞纳金']
                    late_fee_analysis_sheet.freeze_panes = 'A2'
                    late_fee_analysis_sheet.auto_filter.ref = late_fee_analysis_sheet.dimensions
                    for column, width in {
                        'A': 12, 'B': 16, 'C': 42, 'D': 36, 'E': 20,
                        'F': 28, 'G': 34, 'H': 14, 'I': 14, 'J': 16,
                        'K': 16, 'L': 14, 'M': 14, 'N': 12, 'O': 24,
                    }.items():
                        late_fee_analysis_sheet.column_dimensions[column].width = width
                    for cell in late_fee_analysis_sheet['B'][1:]:
                        cell.number_format = '@'
                if paid_fee_rows:
                    pd.DataFrame(paid_fee_rows).to_excel(writer, sheet_name='已缴费信息', index=False)
                    paid_fee_sheet = writer.sheets['已缴费信息']
                    paid_fee_sheet.freeze_panes = 'A2'
                    paid_fee_sheet.auto_filter.ref = paid_fee_sheet.dimensions
                    for column, width in {
                        'A': 16, 'B': 28, 'C': 12, 'D': 14,
                        'E': 46, 'F': 14, 'G': 16,
                    }.items():
                        paid_fee_sheet.column_dimensions[column].width = width
                    for column in ('A', 'F', 'G'):
                        for cell in paid_fee_sheet[column][1:]:
                            cell.number_format = '@'
                if fee_receipt_dispatch_rows:
                    pd.DataFrame(fee_receipt_dispatch_rows).to_excel(
                        writer,
                        sheet_name='收据发文信息',
                        index=False,
                    )
                    receipt_dispatch_sheet = writer.sheets['收据发文信息']
                    receipt_dispatch_sheet.freeze_panes = 'A2'
                    receipt_dispatch_sheet.auto_filter.ref = receipt_dispatch_sheet.dimensions
                    for column, width in {
                        'A': 16, 'B': 28, 'C': 12, 'D': 46, 'E': 14,
                        'F': 16, 'G': 46, 'H': 24, 'I': 14, 'J': 12,
                        'K': 14, 'L': 16, 'M': 18,
                    }.items():
                        receipt_dispatch_sheet.column_dimensions[column].width = width
                    for column in ('A', 'F', 'L'):
                        for cell in receipt_dispatch_sheet[column][1:]:
                            cell.number_format = '@'

            print(f"✅ 数据已导出至: {excel_file}")
            print(f"   Sheet1: 专利主信息 ({len(df)} 条)")
            if fwxx_rows:
                print(f"   Sheet2: 发文信息 ({len(fwxx_rows)} 条)")
            if payable_fee_rows:
                print(f"   应缴费信息 ({len(payable_fee_rows)} 条)")
            if late_fee_schedule_rows:
                print(f"   应缴滞纳金信息 ({len(late_fee_schedule_rows)} 条)")
            if payable_analysis_rows:
                print(f"   待缴费分析 ({len(payable_analysis_rows)} 条，基准日 {analysis_date})")
            if late_fee_analysis_rows:
                print(f"   当前滞纳金 ({len(late_fee_analysis_rows)} 件，基准日 {analysis_date})")
            if paid_fee_rows:
                print(f"   已缴费信息 ({len(paid_fee_rows)} 条)")
            if fee_receipt_dispatch_rows:
                print(f"   收据发文信息 ({len(fee_receipt_dispatch_rows)} 条)")
            return True
        except Exception as e:
            print(f"❌ 导出 Excel 失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def export_to_json(self, json_file: str = None) -> bool:
        """导出为 JSON 格式（兼容旧格式）"""
        if json_file is None:
            json_file = self.log_file.replace('.jsonl', '.json')
        records = self._load_records()
        success = sum(1 for r in records if r.get('status_code') == 200)
        data = {
            'metadata': {
                'total_records': len(records),
                'successful': success,
                'failed': len(records) - success,
                'exported_at': datetime.utcnow().isoformat() + 'Z',
            },
            'records': records,
        }
        write_json_atomic(json_file, data)
        print(f"✅ JSON 已导出: {json_file} ({len(records)} 条)")
        return True


if __name__ == '__main__':
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # 测试时使用临时 DB
        import db_manager as _dm
        _orig = _dm.PatentsDB.__init__

        logger = DetectionLogger(os.path.join(tmpdir, 'test_log.json'))

        logger.add_record(DetectionRecord(
            application_no='CN202310641887.1',
            status_code=200,
            response_time_ms=2345,
            detected=False,
            response_summary='Success',
        ))
        logger.add_record(DetectionRecord(
            application_no='CN202310869634.X',
            status_code=0,
            error_message='MITM timeout',
        ))

        logger.print_summary()
        print(f"[✓] 记录数: {logger._db.count()}")

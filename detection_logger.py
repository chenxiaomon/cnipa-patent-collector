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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from settings import DETECTION_LOG_JSONL_FILE, RESULTS_DIR, PATENTS_DB_FILE
from db_manager import PatentsDB


def _normalize_app_no(app_no: str) -> str:
    """移除 CN 前缀和点号，统一申请号格式。"""
    return str(app_no).upper().replace('CN', '').replace('.', '') if app_no else app_no


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
        - DB：INSERT OR REPLACE（O(1)）
        - JSONL：追加（双写备份，用于 git 追踪，O(1)）
        """
        d = record.to_dict()
        d['application_no'] = _normalize_app_no(d['application_no'])

        # 1. 写 SQLite
        self._db.upsert(d)

        # 2. 追加 JSONL（双写备份）
        line = json.dumps(d, ensure_ascii=False) + '\n'
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

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

    # ------------------------------------------------------------------
    # 备份（每 500 条 DB 记录自动备份一次 JSONL）
    # ------------------------------------------------------------------

    def _auto_backup(self, interval: int = 500) -> None:
        try:
            count = self._db.count()
        except Exception:
            return
        if count > 0 and count % interval == 0:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup = self.log_file.replace('.jsonl', f'_backup_{timestamp}.jsonl')
            shutil.copy2(self.log_file, backup)
            print(f"[✓] 自动备份: {os.path.basename(backup)} ({count} 条)")
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
        return self._db.get_all_app_nos()

    def get_pending_applications(self, all_applications: list) -> list:
        processed = self.get_processed_applications()
        return [a for a in all_applications if a not in processed]

    def get_stats(self) -> Dict[str, Any]:
        return self._db.get_stats()

    def print_summary(self) -> None:
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

    # ------------------------------------------------------------------
    # 导出（内部改为从 DB 读取）
    # ------------------------------------------------------------------

    def export_to_excel(self, excel_file: str = None) -> bool:
        """导出到 Excel（Sheet1：专利主信息，Sheet2：发文信息）"""
        if pd is None:
            print("❌ pandas 未安装，无法导出 Excel")
            return False
        try:
            if excel_file is None:
                excel_file = os.path.join(os.path.dirname(self.log_file), 'patents_data.xlsx')

            records = self._load_records()

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

            df = pd.DataFrame(records)
            df = df.rename(columns=column_mapping)
            keep_cols = [v for v in column_mapping.values() if v in df.columns]
            df = df[keep_cols]

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

            with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='专利主信息', index=False)
                if fwxx_rows:
                    pd.DataFrame(fwxx_rows).to_excel(writer, sheet_name='发文信息', index=False)

            print(f"✅ 数据已导出至: {excel_file}")
            print(f"   Sheet1: 专利主信息 ({len(df)} 条)")
            if fwxx_rows:
                print(f"   Sheet2: 发文信息 ({len(fwxx_rows)} 条)")
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
        tmp = json_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, json_file)
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

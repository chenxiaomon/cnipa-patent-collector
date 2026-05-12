#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
检测数据记录模块
- 以 JSONL 格式追加写入，每条记录一行，中断安全
- 支持断点续传（跳过已处理的申请号）
- upsert_record 原子重写整文件（强制更新模式专用，调用频率低）
"""

import glob
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from settings import DETECTION_LOG_JSONL_FILE, RESULTS_DIR


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
        # 14 个专利信息字段
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
        # 发文信息字段（仅在状态="驳回等复审请求"时填充）
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
        self.timestamp = datetime.utcnow().isoformat() + 'Z'

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
    """检测数据日志记录器（JSONL 存储，追加写入，中断安全）"""

    def __init__(self, log_file: str = None):
        if log_file is None:
            log_file = str(DETECTION_LOG_JSONL_FILE)

        # 兼容旧调用：若传入 .json 路径，自动转为 .jsonl
        if log_file.endswith('.json') and not log_file.endswith('.jsonl'):
            log_file = log_file[:-5] + '.jsonl'

        self.log_file = log_file
        self.log_dir = os.path.dirname(log_file)
        os.makedirs(self.log_dir, exist_ok=True)

        if not os.path.exists(self.log_file):
            open(self.log_file, 'a').close()

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def _load_records(self) -> list:
        """读取全部记录；损坏行跳过并警告。"""
        records = []
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        print(f"[!] 第 {line_no} 行解析失败，跳过（可能是上次中断残留）")
        except OSError:
            pass
        return records

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def add_record(self, record: DetectionRecord) -> None:
        """追加一条记录（O_APPEND + fsync，中断安全）"""
        d = record.to_dict()
        d['application_no'] = _normalize_app_no(d['application_no'])
        line = json.dumps(d, ensure_ascii=False) + '\n'
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        self._auto_backup()

    def upsert_record(self, record: DetectionRecord) -> None:
        """更新已有记录（按 application_no），不存在则追加。用于强制重查模式（调用频率低）。"""
        new = record.to_dict()
        new['application_no'] = _normalize_app_no(new['application_no'])
        records = self._load_records()
        found = False
        for i, r in enumerate(records):
            if r.get('application_no') == new['application_no']:
                records[i] = new
                found = True
                break
        if not found:
            records.append(new)
        self._rewrite(records)
        self._auto_backup()

    def _rewrite(self, records: list) -> None:
        """原子重写整个 JSONL 文件（tmp + os.replace）"""
        tmp = self.log_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.log_file)

    # ------------------------------------------------------------------
    # 备份
    # ------------------------------------------------------------------

    def _auto_backup(self, interval: int = 500) -> None:
        """每累积 interval 条记录自动生成带时间戳备份，保留最近 5 个"""
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                count = sum(1 for line in f if line.strip())
        except OSError:
            return
        if count > 0 and count % interval == 0:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup = self.log_file.replace('.jsonl', f'_backup_{timestamp}.jsonl')
            shutil.copy2(self.log_file, backup)
            print(f"[✓] 自动备份: {os.path.basename(backup)} ({count} 条)")
            self._prune_backups()

    def _prune_backups(self, keep: int = 5) -> None:
        pattern = self.log_file.replace('.jsonl', '_backup_*.jsonl')
        for old in sorted(glob.glob(pattern))[:-keep]:
            try:
                os.remove(old)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_processed_applications(self) -> set:
        return {r['application_no'] for r in self._load_records()}

    def get_pending_applications(self, all_applications: list) -> list:
        processed = self.get_processed_applications()
        return [a for a in all_applications if a not in processed]

    def get_stats(self) -> Dict[str, Any]:
        records = self._load_records()
        if not records:
            return {'total': 0, 'success': 0, 'failed': 0, 'detected': 0, 'average_response_time_ms': 0}
        success = sum(1 for r in records if r.get('status_code') == 200)
        detected = sum(1 for r in records if r.get('detected'))
        times = [r['response_time_ms'] for r in records if r.get('response_time_ms')]
        return {
            'total': len(records),
            'success': success,
            'failed': len(records) - success,
            'detected': detected,
            'average_response_time_ms': round(sum(times) / len(times), 2) if times else 0,
        }

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
    # 导出
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
        """导出为 JSON 格式（兼容旧格式，用于人工检查）"""
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
        print(f"[✓] JSONL 文件: {logger.log_file}")
        print(f"[✓] 记录数: {len(logger._load_records())}")

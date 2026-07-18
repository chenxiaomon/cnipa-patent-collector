#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite 数据库管理模块

替代 JSONL 方案，解决 O(n) 全表扫描和 upsert 写放大问题。
- 主存储：data/patents.db（SQLite，WAL 模式，支持并发）
- 接口：PatentsDB 类，供 detection_logger.py 和 web_dashboard.py 使用
- 迁移：python db_manager.py migrate  （从现有 JSONL 导入）
"""

import json
import re
import sqlite3
import sys
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

_JSON_FIELDS = {
    'fwxx_list',
    'bhsjtzs_data',
    'payable_fee_records',
    'late_fee_schedule_records',
    'paid_fee_records',
    'fee_receipt_dispatch_records',
}
PENDING_STATUS_CODE = -1
SYNC_CURSOR_FIELD = '_sync_updated_at'
_APPLICANT_SPLIT_RE = re.compile(r"[,，;；、\r\n]+")

_CREATE_REQUESTS_TABLE = """
CREATE TABLE IF NOT EXISTS requests (
    id         TEXT PRIMARY KEY,
    type       TEXT NOT NULL DEFAULT 'app_nos',
    payload    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    job_id     TEXT,
    requester  TEXT,
    note       TEXT,
    created_at TEXT NOT NULL
)
"""

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS patents (
    application_no      TEXT PRIMARY KEY,
    status_code         INTEGER,
    response_time_ms    REAL,
    detected            INTEGER,
    response_summary    TEXT,
    timestamp           TEXT,
    error_message       TEXT,
    famingzlsqgbg       TEXT,
    shouquanggh         TEXT,
    zhuanlimc           TEXT,
    shenqingrxm         TEXT,
    zhuanlilx           TEXT,
    shenqingr           TEXT,
    gongkaiggh          TEXT,
    falvzt              TEXT,
    gongkaiggr          TEXT,
    shouquanggr         TEXT,
    zhufenlh            TEXT,
    anjianbh            TEXT,
    anjianywzt          TEXT,
    fwxx_list           TEXT,
    bhsjtzs_xiazaisj    TEXT,
    bhsjtzs_data        TEXT,
    payable_fee_records TEXT,
    late_fee_schedule_records TEXT,
    paid_fee_records    TEXT,
    fee_receipt_dispatch_records TEXT,
    fee_snapshot_at     TEXT,
    previous_status     TEXT,
    updated_at          TEXT,
    daili_jg            TEXT,
    daili_r             TEXT
)
"""

_COLUMNS = [
    'application_no', 'status_code', 'response_time_ms', 'detected',
    'response_summary', 'timestamp', 'error_message',
    'famingzlsqgbg', 'shouquanggh', 'zhuanlimc', 'shenqingrxm',
    'zhuanlilx', 'shenqingr', 'gongkaiggh', 'falvzt', 'gongkaiggr',
    'shouquanggr', 'zhufenlh', 'anjianbh', 'anjianywzt',
    'fwxx_list', 'bhsjtzs_xiazaisj', 'bhsjtzs_data',
    'payable_fee_records', 'late_fee_schedule_records', 'paid_fee_records',
    'fee_receipt_dispatch_records', 'fee_snapshot_at', 'previous_status',
    'updated_at', 'daili_jg', 'daili_r',
]

_CREATE_DETAIL_ENRICHMENT_PENDING_INDEX = (
    "CREATE INDEX idx_detail_enrichment_pending ON patents(anjianywzt) "
    "WHERE fwxx_list IS NULL OR payable_fee_records IS NULL "
    "OR paid_fee_records IS NULL OR fee_receipt_dispatch_records IS NULL"
)


def _split_applicant_names(value: str | None) -> list[str]:
    if not value:
        return []

    names: list[str] = []
    seen: set[str] = set()
    for part in _APPLICANT_SPLIT_RE.split(str(value)):
        name = part.strip()
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _count_split_applicants(rows) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        for name in _split_applicant_names(row['shenqingrxm']):
            counts[name] += row['cnt']
    return counts


class PatentsDB:
    """SQLite 专利数据库（线程安全，WAL 模式）"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ── 内部工具 ──────────────────────────────────────────────────────────

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(_CREATE_TABLE)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_anjianywzt ON patents(anjianywzt)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp   ON patents(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_updated_at  ON patents(updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_shenqingrxm ON patents(shenqingrxm)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status_code ON patents(status_code)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_zhuanlilx_shenqingrxm ON patents(zhuanlilx, shenqingrxm)"
            )
            # 部分索引：只索引未采集发文信息的行，避免把多 KB 的 fwxx_list 文本收进索引
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fwxx_pending ON patents(anjianywzt) WHERE fwxx_list IS NULL"
            )
            conn.execute(_CREATE_REQUESTS_TABLE)
            # 对已有数据库做无损迁移：列已存在时 SQLite 抛 "duplicate column name"，忽略即可
            for col in (
                'daili_jg TEXT',
                'daili_r TEXT',
                'payable_fee_records TEXT',
                'late_fee_schedule_records TEXT',
                'paid_fee_records TEXT',
                'fee_receipt_dispatch_records TEXT',
                'fee_snapshot_at TEXT',
            ):
                try:
                    conn.execute(f"ALTER TABLE patents ADD COLUMN {col}")
                except sqlite3.OperationalError as e:
                    if 'duplicate column' not in str(e).lower():
                        raise  # 非"列已存在"的错误应当暴露
            index_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='idx_detail_enrichment_pending'"
            ).fetchone()
            current_index_sql = ' '.join(index_row['sql'].split()).lower() if index_row else None
            expected_index_sql = ' '.join(
                _CREATE_DETAIL_ENRICHMENT_PENDING_INDEX.split()
            ).lower()
            if current_index_sql != expected_index_sql:
                if index_row:
                    conn.execute("DROP INDEX idx_detail_enrichment_pending")
                conn.execute(_CREATE_DETAIL_ENRICHMENT_PENDING_INDEX)
            migration_timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            conn.execute(
                "UPDATE patents SET updated_at=COALESCE(NULLIF(timestamp, ''), ?) "
                "WHERE updated_at IS NULL OR updated_at=''",
                (migration_timestamp,),
            )
            conn.commit()

    @staticmethod
    def _encode(record: dict) -> dict:
        """将 dict/list 字段序列化为 JSON 字符串"""
        row = {}
        for col in _COLUMNS:
            if col == 'updated_at':
                row[col] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                continue
            v = record.get(col)
            if col in _JSON_FIELDS and v is not None:
                v = json.dumps(v, ensure_ascii=False)
            row[col] = v
        return row

    @staticmethod
    def _decode(row) -> dict:
        """将 sqlite3.Row 转为 dict，并反序列化 JSON 字段"""
        d = dict(row)
        d.pop('updated_at', None)
        for field in _JSON_FIELDS:
            v = d.get(field)
            if v and isinstance(v, str):
                try:
                    d[field] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    # ── 写入 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _upsert_statement(columns: list[str]) -> str:
        placeholders = ','.join('?' * len(columns))
        assignments = []
        for column in columns:
            if column == 'application_no':
                continue
            if column in {'error_message', 'updated_at'}:
                assignments.append(f'{column}=excluded.{column}')
            else:
                assignments.append(
                    f'{column}=COALESCE(excluded.{column}, patents.{column})'
                )
        return (
            f"INSERT INTO patents ({','.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(application_no) DO UPDATE SET {','.join(assignments)}"
        )

    def upsert(self, record: dict) -> None:
        """Upsert one record without erasing existing fields when incoming values are NULL."""
        row = self._encode(record)
        cols = list(row.keys())
        sql = self._upsert_statement(cols)
        with self._lock, self._connect() as conn:
            conn.execute(sql, [row[c] for c in cols])
            conn.commit()

    def update_fields(self, app_no: str, fields: dict) -> None:
        """
        对已有记录做部分字段更新（只更新传入的字段，不影响其他列）。
        记录不存在时静默跳过。

        注意：不经过 _encode()，避免把未传入的列填为 NULL。
        JSON 字段会在此处按需序列化。
        """
        if not fields:
            return
        # 只取合法列名，过滤掉不在 schema 中的键，并附加 updated_at
        valid = {k: v for k, v in fields.items() if k in _COLUMNS and k != 'updated_at'}
        if not valid:
            return
        for field in _JSON_FIELDS:
            if field in valid and valid[field] is not None and not isinstance(valid[field], str):
                valid[field] = json.dumps(valid[field], ensure_ascii=False)
        valid['updated_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        set_clause = ', '.join(f'{col}=?' for col in valid)
        sql = f"UPDATE patents SET {set_clause} WHERE application_no=?"
        with self._lock, self._connect() as conn:
            conn.execute(sql, [*valid.values(), app_no])
            conn.commit()

    def snapshot_previous_status(self) -> int:
        """
        采集前将当前 anjianywzt 快照到 previous_status（单条 SQL 全表完成），
        供采集后对比分析。跨机同步游标依赖 updated_at，因此快照同时刷新 updated_at。
        返回快照的记录数（anjianywzt 非空的行）。
        """
        stamped_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE patents SET previous_status=anjianywzt, updated_at=? "
                "WHERE anjianywzt IS NOT NULL AND anjianywzt != ''",
                (stamped_at,),
            )
            conn.commit()
            return cursor.rowcount

    def upsert_batch(self, records: list[dict]) -> int:
        """批量 upsert，用于初始迁移"""
        if not records:
            return 0
        rows = [self._encode(r) for r in records]
        cols = list(rows[0].keys())
        sql = self._upsert_statement(cols)
        with self._lock, self._connect() as conn:
            conn.executemany(sql, [[r[c] for c in cols] for r in rows])
            conn.commit()
        return len(rows)

    def summarize_record_import(self, records: list[dict]) -> dict:
        """Summarize how an external record set would change this database."""
        application_nos = {
            record.get('application_no')
            for record in records
            if record.get('application_no')
        }
        existing = self.get_all_app_nos()
        timestamps = sorted(
            str(record['timestamp'])
            for record in records
            if record.get('timestamp')
        )
        return {
            'records': len(records),
            'applications': len(application_nos),
            'new_applications': len(application_nos - existing),
            'updated_applications': len(application_nos & existing),
            'timestamp_from': timestamps[0] if timestamps else None,
            'timestamp_to': timestamps[-1] if timestamps else None,
        }

    # ── 查询 ──────────────────────────────────────────────────────────────

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM patents").fetchone()[0]

    def get_all_app_nos(self) -> set:
        with self._connect() as conn:
            rows = conn.execute("SELECT application_no FROM patents").fetchall()
        return {r[0] for r in rows}

    def get_processed_app_nos(self) -> set[str]:
        """Return applications with a completed collection attempt, successful or failed."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT application_no FROM patents "
                "WHERE status_code IS NOT NULL AND status_code != ?",
                (PENDING_STATUS_CODE,),
            ).fetchall()
        return {r[0] for r in rows}

    def count_unattempted_records(self) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM patents WHERE status_code IS NULL"
            ).fetchone()[0]

    def mark_unattempted_records_pending(self) -> int:
        """Assign the explicit pending status to legacy records with NULL status_code."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE patents SET status_code=?, updated_at=? WHERE status_code IS NULL",
                (
                    PENDING_STATUS_CODE,
                    datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                ),
            )
            conn.commit()
            return cursor.rowcount

    def export_delta(self, since: str) -> list[dict]:
        """Return records modified after the master-owned synchronization cursor."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM patents WHERE updated_at > ? "
                "ORDER BY updated_at ASC, application_no ASC",
                (since,)
            ).fetchall()
        records = []
        for row in rows:
            record = self._decode(row)
            record[SYNC_CURSOR_FIELD] = row['updated_at']
            records.append(record)
        return records

    def get_all_records(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM patents ORDER BY timestamp ASC").fetchall()
        return [self._decode(r) for r in rows]

    def list_applicants(self) -> list[tuple[str, int]]:
        """返回全部不同申请人及各自记录数，按数量降序。供筛选导出的下拉列表用。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT shenqingrxm, COUNT(*) AS cnt FROM patents "
                "WHERE shenqingrxm IS NOT NULL AND shenqingrxm != '' "
                "GROUP BY shenqingrxm ORDER BY cnt DESC"
            ).fetchall()
        counts = _count_split_applicants(rows)
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    def query_filtered(
        self,
        applicants: list[str] | None = None,
        ts_from: str | None = None,
        ts_to: str | None = None,
        rejection_from: str | None = None,
        rejection_to: str | None = None,
    ) -> list[dict]:
        """按条件筛选专利记录，条件之间为 OR 关系。

        各筛选维度：
          applicants:      申请人列表（精确匹配，多值 IN）
          ts_from/ts_to:   采集时间范围（ISO 字符串，闭区间）
          rejection_from/rejection_to: 驳回发文日期范围（YYYY-MM-DD，闭区间）

        维度内部是 AND（如 ts_from + ts_to 构成区间），
        维度之间是 OR（满足任一维度即选中）。
        所有维度都为空时返回全部记录。
        """
        clauses = []
        params: list = []
        applicant_set = {
            name
            for applicant in (applicants or [])
            for name in _split_applicant_names(applicant)
        }

        # 维度 1：申请人
        if applicant_set:
            clauses.append("shenqingrxm IS NOT NULL AND shenqingrxm != ''")

        # 维度 2：采集时间范围
        ts_parts = []
        if ts_from:
            ts_parts.append("timestamp >= ?")
            params.append(ts_from)
        if ts_to:
            ts_parts.append("timestamp <= ?")
            params.append(ts_to)
        if ts_parts:
            clauses.append("(" + " AND ".join(ts_parts) + ")")

        # 维度 3：驳回发文日期范围
        rej_parts = []
        if rejection_from:
            rej_parts.append("bhsjtzs_xiazaisj >= ?")
            params.append(rejection_from)
        if rejection_to:
            rej_parts.append("bhsjtzs_xiazaisj <= ?")
            params.append(rejection_to)
        if rej_parts:
            # 必须有值才参与（bhsjtzs_xiazaisj IS NOT NULL）
            clauses.append("(bhsjtzs_xiazaisj IS NOT NULL AND " + " AND ".join(rej_parts) + ")")

        if not clauses:
            return self.get_all_records()

        where = " OR ".join(clauses)
        sql = f"SELECT * FROM patents WHERE {where} ORDER BY timestamp ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        records = [self._decode(r) for r in rows]

        if not applicant_set:
            return records

        def matches_applicant(record: dict) -> bool:
            return bool(applicant_set & set(_split_applicant_names(record.get('shenqingrxm'))))

        def matches_timestamp(record: dict) -> bool:
            if not (ts_from or ts_to):
                return False
            timestamp = record.get('timestamp')
            if not timestamp:
                return False
            if ts_from and timestamp < ts_from:
                return False
            if ts_to and timestamp > ts_to:
                return False
            return True

        def matches_rejection_date(record: dict) -> bool:
            if not (rejection_from or rejection_to):
                return False
            rejection_date = record.get('bhsjtzs_xiazaisj')
            if not rejection_date:
                return False
            if rejection_from and rejection_date < rejection_from:
                return False
            if rejection_to and rejection_date > rejection_to:
                return False
            return True

        return [
            record for record in records
            if matches_applicant(record) or matches_timestamp(record) or matches_rejection_date(record)
        ]

    def query_update_candidates(self, status: str, freq_days: int) -> tuple[list[dict], list[dict]]:
        """
        Return records with anjianywzt=status, split by whether the update interval has elapsed.
        NULL timestamps are treated as overdue (needs update).

        Returns: (needs_update_records, not_yet_due_records)
        """
        interval = f'+{freq_days} days'
        with self._connect() as conn:
            needs_rows = conn.execute(
                """SELECT * FROM patents WHERE anjianywzt=?
                   AND (timestamp IS NULL
                        OR datetime(timestamp, ?) <= datetime('now'))
                   ORDER BY timestamp ASC""",
                (status, interval)
            ).fetchall()
            pending_rows = conn.execute(
                """SELECT * FROM patents WHERE anjianywzt=?
                   AND timestamp IS NOT NULL
                   AND datetime(timestamp, ?) > datetime('now')
                   ORDER BY timestamp ASC""",
                (status, interval)
            ).fetchall()
        return [self._decode(r) for r in needs_rows], [self._decode(r) for r in pending_rows]

    def get_status_timestamp_snapshot(self) -> list[dict]:
        """
        轻量快照：只返回每条记录的 (application_no, anjianywzt, timestamp)。
        供 update_by_strategy.analyze_updates 一次拉完所有记录后在 Python 侧分组，
        避免按每个状态分别发起全表扫描（N 个状态 = N 次查询的问题）。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT application_no, anjianywzt, timestamp FROM patents ORDER BY timestamp ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_record(self, app_no: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM patents WHERE application_no=?", (app_no,)
            ).fetchone()
        return self._decode(row) if row else None

    def fwxx_uncollected_app_nos(self, rejection_status: str = '驳回等复审请求') -> list[str]:
        """返回所有"驳回等复审请求"且尚未采集发文信息的申请号。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT application_no FROM patents WHERE anjianywzt=? AND fwxx_list IS NULL",
                (rejection_status,)
            ).fetchall()
        return [r['application_no'] for r in rows]

    def fwxx_collected_app_nos(self) -> set:
        """已采集发文信息（fwxx_list 非空）的申请号集合，供独立采集模式断点续传。

        故意不过滤 anjianywzt：续传语义是"任何已有 fwxx 的记录都跳过"，
        与 fwxx_uncollected_app_nos 的驳回状态过滤不同。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT application_no FROM patents WHERE fwxx_list IS NOT NULL"
            ).fetchall()
        return {r['application_no'] for r in rows}

    def detail_enrichment_pending_app_nos(
        self,
        rejection_status: str = '驳回等复审请求',
    ) -> list[str]:
        """返回发文或费用任一项尚未采集的驳回案件申请号。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT application_no FROM patents WHERE anjianywzt=? AND "
                "(fwxx_list IS NULL OR payable_fee_records IS NULL "
                "OR paid_fee_records IS NULL "
                "OR fee_receipt_dispatch_records IS NULL)",
                (rejection_status,),
            ).fetchall()
        return [row['application_no'] for row in rows]

    def detail_enrichment_completed_app_nos(self) -> set[str]:
        """返回发文、应缴、已缴和收据发文均已采集的申请号。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT application_no FROM patents WHERE fwxx_list IS NOT NULL "
                "AND payable_fee_records IS NOT NULL "
                "AND paid_fee_records IS NOT NULL "
                "AND fee_receipt_dispatch_records IS NOT NULL"
            ).fetchall()
        return {row['application_no'] for row in rows}

    # ── 需求队列 ──────────────────────────────────────────────────────────

    def submit_request(self, app_nos: list[str], requester_ip: str, note: str = '') -> str:
        """提交申请号采集需求，含去重检查。已有 pending 重叠时返回空字符串。"""
        import uuid
        payload = json.dumps(app_nos, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM requests WHERE status='pending'"
            ).fetchall()
            existing = {no for r in rows for no in json.loads(r['payload'])}
            overlap = [a for a in app_nos if a in existing]
            if overlap:
                return ''
            req_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            conn.execute(
                "INSERT INTO requests (id, payload, requester, note, created_at) VALUES (?,?,?,?,?)",
                (req_id, payload, requester_ip, note or '', now)
            )
            conn.commit()
        return req_id

    def list_requests(self, status: str | None = None) -> list[dict]:
        """列出需求记录，可按 status 过滤。"""
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM requests WHERE status=? ORDER BY created_at DESC",
                    (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM requests ORDER BY created_at DESC"
                ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d['payload'] = json.loads(d['payload'])
            result.append(d)
        return result

    def approve_request(self, req_id: str, job_id: str) -> None:
        """批准需求，记录 job_id，状态改为 executing。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE requests SET status='executing', job_id=? WHERE id=?",
                (job_id, req_id)
            )
            conn.commit()

    def reject_request(self, req_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE requests SET status='rejected' WHERE id=?", (req_id,))
            conn.commit()

    def sync_request_status(self, req_id: str, job_status: str, exit_code: int | None) -> None:
        """懒更新：根据 job 最终状态回写 request.status。"""
        if job_status != 'finished':
            return
        new_status = 'done' if exit_code == 0 else 'failed'
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE requests SET status=? WHERE id=? AND status='executing'",
                (new_status, req_id)
            )
            conn.commit()

    def get_stats(self) -> dict:
        sql = """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status_code=200 THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN status_code IS NOT NULL AND status_code NOT IN (200, ?) THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status_code IS NULL OR status_code=? THEN 1 ELSE 0 END) AS pending,
                AVG(CASE WHEN response_time_ms IS NOT NULL THEN response_time_ms END) AS avg_time
            FROM patents
        """
        with self._connect() as conn:
            row = conn.execute(sql, (PENDING_STATUS_CODE, PENDING_STATUS_CODE)).fetchone()
        total = row['total'] or 0
        success = row['success'] or 0
        failed = row['failed'] or 0
        return {
            'total': total,
            'success': success,
            'failed': failed,
            'pending': row['pending'] or 0,
            'detected': 0,
            'average_response_time_ms': round(row['avg_time'] or 0, 2),
        }

    def get_summary(self, rejection_status: str = '驳回等复审请求') -> dict:
        """
        一次数据库访问返回 build_summary() 所需的全部聚合数据。
        替代对 9000+ 条 JSONL 的多次 Python 遍历。
        """
        with self._connect() as conn:
            # 1. 主聚合
            agg = conn.execute("""
                SELECT
                    COUNT(*) AS unique_count,
                    SUM(CASE WHEN status_code=200 THEN 1 ELSE 0 END) AS success,
                    SUM(CASE WHEN status_code IS NOT NULL AND status_code NOT IN (200, ?) THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status_code IS NULL OR status_code=? THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN anjianywzt=? THEN 1 ELSE 0 END) AS rejection,
                    SUM(CASE WHEN fwxx_list IS NOT NULL THEN 1 ELSE 0 END) AS fwxx_collected,
                    SUM(CASE WHEN anjianywzt=? AND fwxx_list IS NULL THEN 1 ELSE 0 END) AS fwxx_pending,
                    SUM(CASE WHEN anjianywzt=? AND fwxx_list IS NOT NULL
                                  AND payable_fee_records IS NOT NULL
                                  AND paid_fee_records IS NOT NULL
                                  AND fee_receipt_dispatch_records IS NOT NULL
                             THEN 1 ELSE 0 END) AS detail_enrichment_completed,
                    SUM(CASE WHEN anjianywzt=? AND (
                                  fwxx_list IS NULL OR payable_fee_records IS NULL
                                  OR paid_fee_records IS NULL
                                  OR fee_receipt_dispatch_records IS NULL)
                             THEN 1 ELSE 0 END) AS detail_enrichment_pending
                FROM patents
            """, (
                PENDING_STATUS_CODE,
                PENDING_STATUS_CODE,
                rejection_status,
                rejection_status,
                rejection_status,
                rejection_status,
            )).fetchone()

            # 2. 业务状态分布 TOP 12
            status_rows = conn.execute("""
                SELECT anjianywzt, COUNT(*) AS cnt FROM patents
                WHERE anjianywzt IS NOT NULL
                GROUP BY anjianywzt ORDER BY cnt DESC LIMIT 12
            """).fetchall()

            # 3. 申请人分布 TOP 8
            applicant_rows = conn.execute("""
                SELECT shenqingrxm, COUNT(*) AS cnt FROM patents
                WHERE shenqingrxm IS NOT NULL
                GROUP BY shenqingrxm ORDER BY cnt DESC
            """).fetchall()

            # 4. 近 7 天每日采集量
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d')
            daily_rows = conn.execute("""
                SELECT strftime('%m-%d', timestamp) AS day, COUNT(*) AS cnt
                FROM patents
                WHERE timestamp >= ?
                GROUP BY day ORDER BY day
            """, (cutoff,)).fetchall()

            # 5. 最近 16 条记录
            recent_rows = conn.execute("""
                SELECT application_no, status_code, anjianywzt, zhuanlimc,
                       shenqingrxm, timestamp, response_time_ms
                FROM patents ORDER BY timestamp DESC LIMIT 16
            """).fetchall()

            # 6. 旧发文待补列表与新详情待补列表保持各自兼容语义
            fwxx_pending_rows = conn.execute("""
                SELECT application_no, anjianywzt, timestamp FROM patents
                WHERE anjianywzt=? AND fwxx_list IS NULL
                ORDER BY timestamp DESC LIMIT 20
            """, (rejection_status,)).fetchall()
            detail_pending_rows = conn.execute("""
                SELECT application_no, anjianywzt, timestamp FROM patents
                WHERE anjianywzt=? AND (
                    fwxx_list IS NULL OR payable_fee_records IS NULL
                    OR paid_fee_records IS NULL
                    OR fee_receipt_dispatch_records IS NULL
                )
                ORDER BY timestamp DESC LIMIT 20
            """, (rejection_status,)).fetchall()

            # 7. 驳回企业发明专利数（按企业聚合，仅发明专利）
            rejection_company_rows = conn.execute("""
                SELECT shenqingrxm, COUNT(*) AS cnt FROM patents
                WHERE anjianywzt=? AND shenqingrxm IS NOT NULL AND zhuanlilx='发明'
                GROUP BY shenqingrxm ORDER BY cnt DESC
            """, (rejection_status,)).fetchall()

        applicant_counts = _count_split_applicants(applicant_rows)
        rejection_company_counts = _count_split_applicants(rejection_company_rows)

        # 构建返回结构（与原 build_summary() 输出完全兼容）
        unique = agg['unique_count'] or 0
        success = agg['success'] or 0
        failed = agg['failed'] or 0
        attempted = success + failed

        # 填充缺失的 7 天日期（可能某天没有记录）
        today = datetime.now(timezone.utc)
        daily_map = {r['day']: r['cnt'] for r in daily_rows}
        daily_counts = []
        for i in range(7):
            day = (today - timedelta(days=6 - i)).strftime('%m-%d')
            daily_counts.append({'date': day, 'count': daily_map.get(day, 0)})

        return {
            'unique_count': unique,
            'success': success,
            'failed': failed,
            'success_rate': round(success / attempted * 100, 2) if attempted else 0,
            'pending': agg['pending'] or 0,
            'rejection': agg['rejection'] or 0,
            'fwxx_collected': agg['fwxx_collected'] or 0,
            'fwxx_pending': agg['fwxx_pending'] or 0,
            'detail_enrichment_completed': agg['detail_enrichment_completed'] or 0,
            'detail_enrichment_pending': agg['detail_enrichment_pending'] or 0,
            'status_counts': [[r['anjianywzt'], r['cnt']] for r in status_rows],
            'applicant_counts': [
                [name, count]
                for name, count in sorted(applicant_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
            ],
            'daily_counts': daily_counts,
            'recent': [dict(r) for r in recent_rows],
            'fwxx_pending_list': [dict(r) for r in fwxx_pending_rows],
            'detail_enrichment_pending_list': [dict(r) for r in detail_pending_rows],
            # 驳回企业列表：[{"name": str, "invention_count": int}, ...]
            'rejection_companies': [
                {'name': name, 'invention_count': count}
                for name, count in sorted(rejection_company_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
        }

    # ── 企业元数据查询 ────────────────────────────────────────────────────

    def get_company_meta_rows(self, tracked_statuses: list[str]) -> list[dict]:
        """
        返回「跟踪企业」与「驳回企业」的合并列表（按企业名去重），每项含：
          name            企业名
          tracked_count   当前处于跟踪状态的发明专利件数（0 表示该企业只在驳回列表）
          total_count     该企业在库中的发明专利总数（含所有状态）
        结果按 total_count 倒序排列。
        """
        if not tracked_statuses:
            tracked_statuses = []
        placeholders = ",".join("?" * len(tracked_statuses)) if tracked_statuses else "''"
        with self._connect() as conn:
            # 跟踪企业：当前处于跟踪状态的发明专利件数
            tracked_rows = conn.execute(f"""
                SELECT shenqingrxm, COUNT(*) AS cnt FROM patents
                WHERE anjianywzt IN ({placeholders}) AND shenqingrxm IS NOT NULL
                  AND zhuanlilx='发明'
                GROUP BY shenqingrxm
            """, tracked_statuses).fetchall() if tracked_statuses else []

            # 驳回企业：处于「驳回等复审请求」的发明专利件数（用于合并）
            rejection_rows = conn.execute("""
                SELECT shenqingrxm, COUNT(*) AS cnt FROM patents
                WHERE anjianywzt='驳回等复审请求' AND shenqingrxm IS NOT NULL
                  AND zhuanlilx='发明'
                GROUP BY shenqingrxm
            """).fetchall()

            # 所有出现过的企业的库内发明专利总数
            total_rows = conn.execute("""
                SELECT shenqingrxm, COUNT(*) AS cnt FROM patents
                WHERE shenqingrxm IS NOT NULL AND zhuanlilx='发明'
                GROUP BY shenqingrxm
            """).fetchall()

        total_map = _count_split_applicants(total_rows)
        tracked_map = _count_split_applicants(tracked_rows)
        rejection_map = _count_split_applicants(rejection_rows)

        # 合并去重：取跟踪企业 ∪ 驳回企业
        all_names = set(tracked_map) | set(rejection_map)
        rows = [
            {
                'name': name,
                'tracked_count': tracked_map.get(name, 0),
                'rejection_count': rejection_map.get(name, 0),
                'total_count': total_map.get(name, 0),
            }
            for name in all_names
        ]
        rows.sort(key=lambda r: -r['total_count'])
        return rows

    # ── 导入 / 导出 ───────────────────────────────────────────────────────

    def import_from_jsonl(self, path: Path) -> int:
        """从 JSONL 文件批量导入（一次性迁移用）"""
        records = []
        bad = 0
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    bad += 1
        if bad:
            print(f"[!] 跳过 {bad} 行损坏记录")
        return self.upsert_batch(records)

    def export_to_jsonl(self, path: Path) -> int:
        """将 DB 全量导出为 JSONL（用于备份 / git 共享）"""
        records = self.get_all_records()
        tmp = Path(str(path) + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        tmp.replace(path)
        return len(records)


# ── CLI ───────────────────────────────────────────────────────────────────

def _cmd_migrate() -> None:
    from settings import PATENTS_DB_FILE, DETECTION_LOG_JSONL_FILE
    from machine_identity import require_database_rebuild_authorization
    require_database_rebuild_authorization(sys.argv[2:])
    print(f"从 {DETECTION_LOG_JSONL_FILE} 导入数据...")
    db = PatentsDB(PATENTS_DB_FILE)
    n = db.import_from_jsonl(DETECTION_LOG_JSONL_FILE)
    print(f"[✓] 导入完成: {n} 条记录 → {PATENTS_DB_FILE}")
    print(f"    数据库现有记录: {db.count()} 条")


def _cmd_stats() -> None:
    from settings import PATENTS_DB_FILE
    db = PatentsDB(PATENTS_DB_FILE)
    stats = db.get_stats()
    print(f"总记录: {stats['total']}  成功: {stats['success']}  失败: {stats['failed']}")
    print(f"平均响应时间: {stats['average_response_time_ms']} ms")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'stats'
    if cmd == 'migrate':
        _cmd_migrate()
    elif cmd == 'stats':
        _cmd_stats()
    else:
        print(f"未知命令: {cmd}")
        print("用法: python db_manager.py [migrate|stats]")

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

from cache_utils import is_supported_cn_application_no, normalize_app_no

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
_NOTICE_DATE_RE = re.compile(
    r"^(?P<year>\d{4})-?(?P<month>\d{2})-?(?P<day>\d{2})(?:[T\s].*)?$"
)

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

_REQUIRED_FEE_DETAILS_MISSING_SQL = (
    "payable_fee_records IS NULL OR paid_fee_records IS NULL "
    "OR fee_receipt_dispatch_records IS NULL"
)
_REQUIRED_FEE_DETAILS_PRESENT_SQL = (
    "payable_fee_records IS NOT NULL AND paid_fee_records IS NOT NULL "
    "AND fee_receipt_dispatch_records IS NOT NULL"
)
# 费用采集数据集：由用户导入的申请号名单决定哪些专利需要采费用（与驳回状态解耦）
_CREATE_FEE_TARGETS_TABLE = """
CREATE TABLE IF NOT EXISTS fee_targets (
    application_no TEXT PRIMARY KEY,
    imported_at    TEXT NOT NULL
)
"""

_COLLECTION_KINDS = ('fees', 'agency')
_CREATE_COLLECTION_FAILURES_TABLE = """
CREATE TABLE IF NOT EXISTS collection_failures (
    collection_kind TEXT NOT NULL CHECK(collection_kind IN ('fees', 'agency')),
    application_no  TEXT NOT NULL,
    reason          TEXT NOT NULL,
    attempt_count   INTEGER NOT NULL CHECK(attempt_count > 0),
    last_failed_at  TEXT NOT NULL,
    PRIMARY KEY (collection_kind, application_no)
)
"""


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


def _normalize_notice_date(value) -> str | None:
    """Return a validated YYYYMMDD key for a notice issuance date."""
    if value is None:
        return None
    match = _NOTICE_DATE_RE.fullmatch(str(value).strip())
    if not match:
        return None
    date_key = "".join(match.group(name) for name in ("year", "month", "day"))
    try:
        datetime.strptime(date_key, "%Y%m%d")
    except ValueError:
        return None
    return date_key


def _require_supported_application_no(value: object) -> str:
    normalized_app_no = normalize_app_no(value)
    if not normalized_app_no or not is_supported_cn_application_no(value):
        raise ValueError(f"不支持的申请号格式: {value!r}")
    return normalized_app_no


def _require_collection_kind(value: object) -> str:
    collection_kind = str(value or '').strip()
    if collection_kind not in _COLLECTION_KINDS:
        raise ValueError(f"不支持的采集类型: {value!r}")
    return collection_kind


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
            conn.execute(_CREATE_FEE_TARGETS_TABLE)
            conn.execute(_CREATE_COLLECTION_FAILURES_TABLE)
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
            # detail_enrichment 综合口径已随费用解耦移除，该索引不再有查询使用
            conn.execute("DROP INDEX IF EXISTS idx_detail_enrichment_pending")
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

    def update_fields(self, app_no: str, fields: dict) -> int:
        """
        对已有记录做部分字段更新（只更新传入的字段，不影响其他列）。

        返回受影响行数：0 表示 patents 无该申请号，调用方据此决定兜底；本方法
        绝不新建行（patents 行只由主采集流程建档）。因 updated_at 每次都刷新，
        行数即等价于"记录是否存在"，无需调用方另做一次存在性查询。

        注意：不经过 _encode()，避免把未传入的列填为 NULL。
        JSON 字段会在此处按需序列化。
        """
        if not fields:
            return 0
        # 只取合法列名，过滤掉不在 schema 中的键，并附加 updated_at
        valid = {k: v for k, v in fields.items() if k in _COLUMNS and k != 'updated_at'}
        if not valid:
            return 0
        for field in _JSON_FIELDS:
            if field in valid and valid[field] is not None and not isinstance(valid[field], str):
                valid[field] = json.dumps(valid[field], ensure_ascii=False)
        valid['updated_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        set_clause = ', '.join(f'{col}=?' for col in valid)
        sql = f"UPDATE patents SET {set_clause} WHERE application_no=?"
        with self._lock, self._connect() as conn:
            cursor = conn.execute(sql, [*valid.values(), app_no])
            conn.commit()
            return cursor.rowcount

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

    def records_with_payable_fees(self) -> list[dict]:
        """返回已采到应缴费用快照的记录（含代理机构），供代理机构欠费排行使用。

        只取排行所需列，避免 get_all_records() 全表全字段解码。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT application_no, zhuanlimc, anjianywzt, daili_jg, "
                "payable_fee_records, fee_snapshot_at FROM patents "
                "WHERE payable_fee_records IS NOT NULL"
            ).fetchall()
        return [self._decode(r) for r in rows]

    def query_filtered(
        self,
        applicants: list[str] | None = None,
        ts_from: str | None = None,
        ts_to: str | None = None,
        rejection_from: str | None = None,
        rejection_to: str | None = None,
        notice_name_contains: str | None = None,
        notice_from: str | None = None,
        notice_to: str | None = None,
    ) -> list[dict]:
        """按条件筛选专利记录，条件之间为 OR 关系。

        各筛选维度：
          applicants:      申请人列表（精确匹配，多值 IN）
          ts_from/ts_to:   采集时间范围（ISO 字符串，闭区间）
          rejection_from/rejection_to: 驳回决定下载日期范围（YYYY-MM-DD，闭区间）
          notice_name_contains/notice_from/notice_to:
                           同一条发文记录的通知书名称和发文日条件

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
        notice_name_fragment = str(notice_name_contains or "").strip()
        notice_from_key = _normalize_notice_date(notice_from)
        notice_to_key = _normalize_notice_date(notice_to)
        notice_filter_active = bool(notice_name_fragment or notice_from or notice_to)
        notice_date_range_valid = not (
            (notice_from and notice_from_key is None)
            or (notice_to and notice_to_key is None)
        )

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

        # 维度 3：驳回决定下载日期范围
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

        # 维度 4：发文名称与实际发文日。逐条匹配在解码后的列表中完成。
        if notice_filter_active:
            clauses.append("fwxx_list IS NOT NULL")

        if not clauses:
            return self.get_all_records()

        where = " OR ".join(clauses)
        sql = f"SELECT * FROM patents WHERE {where} ORDER BY timestamp ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        records = [self._decode(r) for r in rows]

        if not applicant_set and not notice_filter_active:
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

        def matches_notice(record: dict) -> bool:
            if not notice_filter_active or not notice_date_range_valid:
                return False
            notices = record.get('fwxx_list')
            if not isinstance(notices, list):
                return False
            for notice in notices:
                if not isinstance(notice, dict):
                    continue
                notice_names = (
                    str(notice.get(field) or '')
                    for field in ('tongzhismc', 'fawenmc')
                )
                if notice_name_fragment and not any(
                    notice_name_fragment in name for name in notice_names
                ):
                    continue
                if notice_from or notice_to:
                    notice_date_key = _normalize_notice_date(notice.get('fawenr'))
                    if notice_date_key is None:
                        continue
                    if notice_from_key and notice_date_key < notice_from_key:
                        continue
                    if notice_to_key and notice_date_key > notice_to_key:
                        continue
                return True
            return False

        return [
            record for record in records
            if (
                matches_applicant(record)
                or matches_timestamp(record)
                or matches_rejection_date(record)
                or matches_notice(record)
            )
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

    def replace_fee_targets(self, app_nos: list[str]) -> dict:
        """\u6574\u8868\u66ff\u6362\u8d39\u7528\u91c7\u96c6\u6570\u636e\u96c6\uff08\u5bfc\u5165\u5373\u66ff\u6362\uff0c\u5355\u4e8b\u52a1\uff09\u3002

        Returns:
            {'previous_count': \u66ff\u6362\u524d\u6570\u91cf, 'imported_count': \u672c\u6b21\u5bfc\u5165\u6570\u91cf}
        """
        imported_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        with self._lock, self._connect() as conn:
            previous_count = conn.execute(
                "SELECT COUNT(*) AS n FROM fee_targets"
            ).fetchone()['n']
            conn.execute("DELETE FROM fee_targets")
            conn.executemany(
                "INSERT OR IGNORE INTO fee_targets(application_no, imported_at) VALUES (?, ?)",
                [(app_no, imported_at) for app_no in app_nos],
            )
            imported_count = conn.execute(
                "SELECT COUNT(*) AS n FROM fee_targets"
            ).fetchone()['n']
            conn.commit()
        return {'previous_count': previous_count, 'imported_count': imported_count}

    def fee_dataset_app_nos(self) -> list[str]:
        """\u6570\u636e\u96c6\u5168\u90e8\u7533\u8bf7\u53f7\uff08--force \u5168\u91cf\u91cd\u91c7\u7528\uff09\u3002"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT application_no FROM fee_targets ORDER BY application_no"
            ).fetchall()
        return [row['application_no'] for row in rows]

    def fee_dataset_pending_app_nos(self) -> list[str]:
        """\u6570\u636e\u96c6\u5185\u5df2\u5efa\u6863\u4e14\u5fc5\u9700\u8d39\u7528\u680f\u76ee\u7f3a\u5931\u7684\u7533\u8bf7\u53f7\u3002

        \u6545\u610f\u6392\u9664 patents \u65e0\u884c\u7684\u300c\u672a\u5efa\u6863\u300d\u7533\u8bf7\u53f7\uff1apersist_fee_fields \u5bf9\u65e0\u884c\u8bb0\u5f55
        \u6c38\u8fdc\u5199\u4e0d\u8fdb\u4e3b\u5e93\uff08\u8fdb fee_unmatched \u5907\u4efd\uff09\uff0c\u653e\u8fdb\u961f\u5217\u53ea\u4f1a\u53cd\u590d\u5931\u8d25\u5e76\u89e6\u53d1\u7194\u65ad\u3002
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.application_no FROM fee_targets t "
                "JOIN patents p ON p.application_no = t.application_no "
                f"WHERE {_REQUIRED_FEE_DETAILS_MISSING_SQL} "
                "ORDER BY t.application_no"
            ).fetchall()
        return [row['application_no'] for row in rows]

    def fee_dataset_unregistered_app_nos(self) -> list[str]:
        """数据集内主库无记录的申请号（需先主采集建档才会进入费用待采）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.application_no FROM fee_targets t "
                "LEFT JOIN patents p ON p.application_no = t.application_no "
                "WHERE p.application_no IS NULL ORDER BY t.application_no"
            ).fetchall()
        return [row['application_no'] for row in rows]

    def fee_dataset_progress(self) -> dict:
        """\u6570\u636e\u96c6\u53e3\u5f84\u7684\u8d39\u7528\u91c7\u96c6\u8fdb\u5ea6\uff0csummary \u4e0e\u91c7\u96c6\u542f\u52a8\u7edf\u8ba1\u5171\u7528\u8fd9\u4e00\u5904\u5b9a\u4e49\u3002

        Returns:
            {'total': \u6570\u636e\u96c6\u5927\u5c0f, 'collected': \u5df2\u91c7\u9f50, 'pending': \u5f85\u91c7,
             'unregistered': \u672a\u5efa\u6863\uff08patents \u65e0\u884c\uff0c\u9700\u5148\u8dd1\u4e3b\u91c7\u96c6\uff09}
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN p.application_no IS NULL THEN 1 ELSE 0 END) AS unregistered, "
                f"SUM(CASE WHEN p.application_no IS NOT NULL AND ({_REQUIRED_FEE_DETAILS_PRESENT_SQL}) "
                "THEN 1 ELSE 0 END) AS collected, "
                f"SUM(CASE WHEN p.application_no IS NOT NULL AND ({_REQUIRED_FEE_DETAILS_MISSING_SQL}) "
                "THEN 1 ELSE 0 END) AS pending "
                "FROM fee_targets t LEFT JOIN patents p ON p.application_no = t.application_no"
            ).fetchone()
        return {
            'total': row['total'] or 0,
            'collected': row['collected'] or 0,
            'pending': row['pending'] or 0,
            'unregistered': row['unregistered'] or 0,
        }

    def fee_details_completed_app_nos(self) -> set[str]:
        """Return cases whose required fee payloads are all non-NULL."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT application_no FROM patents WHERE "
                f"{_REQUIRED_FEE_DETAILS_PRESENT_SQL}"
            ).fetchall()
        return {row['application_no'] for row in rows}

    # ── 独立采集失败记录 ──────────────────────────────────────────────────

    def record_collection_failure(
        self,
        collection_kind: str,
        app_no: str,
        reason: str,
    ) -> dict:
        """记录可重试失败；同一采集类型和申请号重复失败时递增尝试次数。"""
        validated_kind = _require_collection_kind(collection_kind)
        normalized_app_no = _require_supported_application_no(app_no)
        failure_reason = str(reason or '').strip()
        if not failure_reason:
            raise ValueError("采集失败原因不能为空")
        failed_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO collection_failures("
                "collection_kind, application_no, reason, attempt_count, last_failed_at"
                ") VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(collection_kind, application_no) DO UPDATE SET "
                "reason=excluded.reason, "
                "attempt_count=collection_failures.attempt_count + 1, "
                "last_failed_at=excluded.last_failed_at",
                (validated_kind, normalized_app_no, failure_reason, failed_at),
            )
            row = conn.execute(
                "SELECT collection_kind, application_no, reason, attempt_count, last_failed_at "
                "FROM collection_failures WHERE collection_kind=? AND application_no=?",
                (validated_kind, normalized_app_no),
            ).fetchone()
            conn.commit()
        return dict(row)

    def clear_collection_failure(self, collection_kind: str, app_no: str) -> bool:
        """采集成功后清除该类型、该申请号的失败状态。"""
        validated_kind = _require_collection_kind(collection_kind)
        normalized_app_no = _require_supported_application_no(app_no)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM collection_failures "
                "WHERE collection_kind=? AND application_no=?",
                (validated_kind, normalized_app_no),
            )
            conn.commit()
        return cursor.rowcount > 0

    def failed_collection_targets(self, collection_kind: str) -> list[dict]:
        """返回指定采集类型的失败目标，最近失败的目标排在前面。"""
        validated_kind = _require_collection_kind(collection_kind)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT collection_kind, application_no, reason, attempt_count, last_failed_at "
                "FROM collection_failures WHERE collection_kind=? "
                "ORDER BY last_failed_at DESC, application_no",
                (validated_kind,),
            ).fetchall()
        return [dict(row) for row in rows]

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
            agg = conn.execute(f"""
                SELECT
                    COUNT(*) AS unique_count,
                    SUM(CASE WHEN status_code=200 THEN 1 ELSE 0 END) AS success,
                    SUM(CASE WHEN status_code IS NOT NULL AND status_code NOT IN (200, ?) THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status_code IS NULL OR status_code=? THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN anjianywzt=? THEN 1 ELSE 0 END) AS rejection,
                    SUM(CASE WHEN fwxx_list IS NOT NULL THEN 1 ELSE 0 END) AS fwxx_collected,
                    SUM(CASE WHEN anjianywzt=? AND fwxx_list IS NOT NULL THEN 1 ELSE 0 END) AS rejection_fwxx_collected,
                    SUM(CASE WHEN anjianywzt=? AND fwxx_list IS NULL THEN 1 ELSE 0 END) AS fwxx_pending
                FROM patents
            """, (
                PENDING_STATUS_CODE,
                PENDING_STATUS_CODE,
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

            # 6. 发文待补列表（驳回口径）与费用待补列表（数据集口径）
            fwxx_pending_rows = conn.execute("""
                SELECT application_no, anjianywzt, timestamp FROM patents
                WHERE anjianywzt=? AND fwxx_list IS NULL
                ORDER BY timestamp DESC LIMIT 20
            """, (rejection_status,)).fetchall()
            fee_pending_rows = conn.execute(f"""
                SELECT t.application_no, p.anjianywzt, p.timestamp
                FROM fee_targets t JOIN patents p ON p.application_no = t.application_no
                WHERE {_REQUIRED_FEE_DETAILS_MISSING_SQL}
                ORDER BY p.timestamp DESC LIMIT 20
            """).fetchall()

            collection_failure_rows = conn.execute("""
                SELECT collection_kind, application_no, reason,
                       attempt_count, last_failed_at
                FROM collection_failures
                ORDER BY last_failed_at DESC, collection_kind, application_no
            """).fetchall()
            collection_failure_count_row = conn.execute("""
                SELECT
                    SUM(CASE WHEN collection_kind='fees' THEN 1 ELSE 0 END) AS fees,
                    SUM(CASE WHEN collection_kind='agency' THEN 1 ELSE 0 END) AS agency
                FROM collection_failures
            """).fetchone()


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
            'rejection_fwxx_collected': agg['rejection_fwxx_collected'] or 0,
            'fwxx_pending': agg['fwxx_pending'] or 0,
            **{f'fee_dataset_{key}': value for key, value in self.fee_dataset_progress().items()},
            'status_counts': [[r['anjianywzt'], r['cnt']] for r in status_rows],
            'applicant_counts': [
                [name, count]
                for name, count in sorted(applicant_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
            ],
            'daily_counts': daily_counts,
            'recent': [dict(r) for r in recent_rows],
            'fwxx_pending_list': [dict(r) for r in fwxx_pending_rows],
            'fee_dataset_pending_list': [dict(r) for r in fee_pending_rows],
            'collection_failures': [dict(row) for row in collection_failure_rows],
            'collection_failure_counts': {
                collection_kind: collection_failure_count_row[collection_kind] or 0
                for collection_kind in _COLLECTION_KINDS
            },
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

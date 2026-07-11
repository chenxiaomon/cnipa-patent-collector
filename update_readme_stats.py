#!/usr/bin/env python3
"""Refresh the generated database statistics block in README.md."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from db_manager import PatentsDB
from settings import BASE_DIR, PATENTS_DB_FILE

README_PATH = BASE_DIR / 'README.md'
START_MARKER = '<!-- AUTO_STATS_START -->'
END_MARKER = '<!-- AUTO_STATS_END -->'


def render_statistics_block(summary: dict) -> str:
    generated_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    return '\n'.join([
        START_MARKER,
        '## 当前数据快照',
        '',
        f'> 由 `update_readme_stats.py` 从 SQLite 自动生成，更新时间：{generated_at}。',
        '',
        '| 指标 | 数值 |',
        '|------|-----:|',
        f"| 唯一申请号 | {summary['unique_count']:,} |",
        f"| 成功采集 | {summary['success']:,} |",
        f"| 采集失败 | {summary['failed']:,} |",
        f"| 待采记录 | {summary['pending']:,} |",
        f"| 已尝试成功率 | {summary['success_rate']:.2f}% |",
        f"| 驳回等复审 | {summary['rejection']:,} |",
        f"| 待补发文 | {summary['fwxx_pending']:,} |",
        END_MARKER,
    ])


def update_readme_statistics(
    readme_path: Path = README_PATH,
    database_path: Path = PATENTS_DB_FILE,
) -> None:
    readme_text = readme_path.read_text(encoding='utf-8')
    if START_MARKER not in readme_text or END_MARKER not in readme_text:
        raise RuntimeError('README 缺少自动统计标记区块。')
    before, remainder = readme_text.split(START_MARKER, 1)
    _, after = remainder.split(END_MARKER, 1)
    block = render_statistics_block(PatentsDB(database_path).get_summary())
    temporary_path = readme_path.with_suffix('.tmp')
    temporary_path.write_text(before.rstrip() + '\n\n' + block + after, encoding='utf-8')
    temporary_path.replace(readme_path)


if __name__ == '__main__':
    update_readme_statistics()
    print(f'[✓] 已刷新 {README_PATH} 数据快照。')

"""
Git 备份文件 detection_log.jsonl.gz 的格式：gzip 包裹的 JSONL，一行一条记录。

本模块是该格式的唯一所有者。PatentsDB 的全量导出/导入、DetectionLogger 的
追加和 sync.py 的计数都经由这里，调用方只处理不含换行符的文本行。

约束：
- 文件提交到 Git，GitHub 拒收超过 100MB 的单个文件；纯文本在 2026-11 已达
  94.6MB，gzip 后约 7.5MB。
- gzip 头的修改时间固定为 0、不写原文件名，换行固定为 LF：同样的数据在任何
  机器上导出都得到同样的字节，数据没变时重新导出不会产生新提交。
- 追加写入新的 gzip 成员。gzip 允许多个成员串联，读取时是一条连续的流；
  下一次全量导出会重写为单个成员。
"""

import gzip
import zlib
from pathlib import Path
from typing import Iterable, Iterator


def write_backup_lines(path: Path, lines: Iterable[str]) -> None:
    """用 lines 原子替换整份备份：先写同目录 .tmp，再替换目标文件。"""
    path = Path(path)
    temporary_path = Path(str(path) + '.tmp')
    try:
        with open(temporary_path, 'wb') as raw_stream, gzip.GzipFile(
            filename='', mode='wb', fileobj=raw_stream, mtime=0,
        ) as backup_stream:
            for line in lines:
                backup_stream.write(line.encode('utf-8') + b'\n')
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def append_backup_lines(path: Path, lines: Iterable[str]) -> None:
    """把 lines 作为一个新的 gzip 成员追加到备份末尾。"""
    with open(path, 'ab') as raw_stream, gzip.GzipFile(
        filename='', mode='ab', fileobj=raw_stream, mtime=0,
    ) as backup_stream:
        for line in lines:
            backup_stream.write(line.encode('utf-8') + b'\n')


def read_backup_lines(path: Path) -> Iterator[str]:
    """逐行读出备份（不含换行符）；文件不是完整的 gzip 时抛 ValueError。"""
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as backup_stream:
            for line in backup_stream:
                yield line.rstrip('\n')
    except (gzip.BadGzipFile, EOFError, zlib.error) as error:
        raise ValueError(
            f"{path} 不是完整的 gzip 备份；旧的纯文本 JSONL 需先压缩，见 docs/runbook.md"
        ) from error

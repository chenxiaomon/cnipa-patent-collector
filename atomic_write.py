"""
原子化 JSON 写入：先写同目录 .tmp 再 os.replace() 到目标路径。

进程在写入途中崩溃时目标文件保持旧内容，不会留下半截 JSON
（项目文件管理规则要求所有落盘写入走此模式）。
"""
import json
import os
import tempfile
import time
from pathlib import Path


def write_json_atomic(path, obj, *, indent=2) -> None:
    """将 obj 序列化为 JSON 并原子替换到 path（str 或 Path）。"""
    snapshot = json.dumps(obj, ensure_ascii=False, indent=indent)
    destination = Path(path)
    # 独立临时文件防止不同进程在替换前覆盖彼此尚未发布的内容。
    snapshot_stream = tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', dir=destination.parent,
        prefix=destination.name + '.', suffix='.tmp', delete=False,
    )
    temporary_path = Path(snapshot_stream.name)
    try:
        with snapshot_stream:
            snapshot_stream.write(snapshot)
        deadline = time.monotonic() + 1.0
        while True:
            try:
                os.replace(temporary_path, destination)
                return
            except PermissionError as error:
                # Windows 读句柄/扫描器可短暂阻止替换；永久权限错误仍须上报。
                if getattr(error, 'winerror', None) not in {5, 32, 33} or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    finally:
        temporary_path.unlink(missing_ok=True)

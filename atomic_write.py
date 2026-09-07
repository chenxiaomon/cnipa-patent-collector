"""
原子化 JSON 写入：先写同目录 .tmp 再 os.replace() 到目标路径。

进程在写入途中崩溃时目标文件保持旧内容，不会留下半截 JSON
（项目文件管理规则要求所有落盘写入走此模式）。
"""
import json
import os
import tempfile


def write_json_atomic(path, obj, *, indent=2) -> None:
    """将 obj 序列化为 JSON 并原子替换到 path（str 或 Path）。"""
    destination = os.fspath(path)
    destination_dir = os.path.dirname(os.path.abspath(destination))
    temporary_fd, temporary_path = tempfile.mkstemp(
        dir=destination_dir,
        prefix=f'.{os.path.basename(destination)}.',
        suffix='.tmp',
        text=True,
    )
    try:
        with os.fdopen(temporary_fd, 'w', encoding='utf-8') as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=indent)
        os.replace(temporary_path, destination)
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass

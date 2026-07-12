"""
原子化 JSON 写入：先写同目录 .tmp 再 os.replace() 到目标路径。

进程在写入途中崩溃时目标文件保持旧内容，不会留下半截 JSON
（项目文件管理规则要求所有落盘写入走此模式）。
"""
import json
import os


def write_json_atomic(path, obj, *, indent=2) -> None:
    """将 obj 序列化为 JSON 并原子替换到 path（str 或 Path）。"""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp_path, path)

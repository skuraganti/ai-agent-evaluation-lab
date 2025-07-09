"""Bounded regular-file IO. Independent adaptation of portfolio host IPC utilities."""

import json
import math
import os
import stat
import tempfile
from pathlib import Path


def read_regular(path: Path, cap: int):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            raise ValueError("Invalid bounded regular file")
        content = stream.read(cap + 1)
    if len(content) > cap:
        raise ValueError("File grew beyond bound")
    return content


def read_json(path: Path, cap: int):
    content = read_regular(path, cap)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def integer(value):
        if len(value.lstrip("-")) > 16:
            raise ValueError("JSON integer outside bound")
        return int(value)

    def floating(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Nonfinite JSON number")
        return result

    try:
        value = json.loads(
            content,
            object_pairs_hook=pairs,
            parse_int=integer,
            parse_float=floating,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("Invalid bounded JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("IPC JSON must be an object")
    return value


def atomic_json(path: Path, value, *, pad_to=None):
    if path.is_symlink():
        raise ValueError("Queue symlink refused")
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if pad_to is not None:
        size = len(encoded.encode("utf-8"))
        if type(pad_to) is not int or not size <= pad_to <= 4096:
            raise ValueError("Invalid fixed JSON frame size")
        encoded += " " * (pad_to - size)
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)

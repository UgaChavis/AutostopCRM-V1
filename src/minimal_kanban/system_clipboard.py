from __future__ import annotations

import os
from pathlib import Path

CF_HDROP = 15
DRAGQUERY_FILE_COUNT = 0xFFFFFFFF


class ClipboardUnavailableError(RuntimeError):
    pass


def list_clipboard_file_paths() -> list[Path]:
    if os.name != "nt":
        return []
    import ctypes

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    if not user32.IsClipboardFormatAvailable(CF_HDROP):
        return []
    if not user32.OpenClipboard(None):
        raise ClipboardUnavailableError("Не удалось открыть буфер обмена Windows.")
    try:
        handle = user32.GetClipboardData(CF_HDROP)
        if not handle:
            return []
        count = shell32.DragQueryFileW(handle, DRAGQUERY_FILE_COUNT, None, 0)
        paths: list[Path] = []
        for index in range(count):
            length = shell32.DragQueryFileW(handle, index, None, 0) + 1
            buffer = ctypes.create_unicode_buffer(length)
            shell32.DragQueryFileW(handle, index, buffer, length)
            if buffer.value:
                paths.append(Path(buffer.value))
        return paths
    finally:
        user32.CloseClipboard()

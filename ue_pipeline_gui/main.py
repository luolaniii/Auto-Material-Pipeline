#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UE Pipeline GUI - 美术批量模型处理工具
入口模块，启动 PySide6 主窗口。
"""

import sys
from pathlib import Path

# 必须最早把项目根目录加进 sys.path（打包后也成立）
_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_DEV_ROOT = Path(__file__).resolve().parent
if str(_DEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_DEV_ROOT))

# 在 import 任何重型库之前安装崩溃钩子
try:
    from core import crash_log
    _LOG = crash_log.install()
except Exception:
    _LOG = None

from PySide6.QtWidgets import QApplication, QMessageBox

from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("UE Pipeline GUI")
    app.setOrganizationName("ArtPipeline")

    try:
        window = MainWindow()
        if _LOG is not None:
            from gui.log_widget import emit_log
            emit_log(f"崩溃日志: {_LOG}", "DEBUG")
        window.show()
    except Exception as e:
        import traceback
        detail = traceback.format_exc()
        if _LOG is not None:
            try:
                with open(_LOG, "a", encoding="utf-8") as f:
                    f.write("\n!!! 启动失败 !!!\n" + detail)
            except Exception:
                pass
        QMessageBox.critical(None, "启动失败", f"{e}\n\n日志: {_LOG}\n\n{detail}")
        raise

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""测试共享夹具：无头 Qt 平台 + 隔离的数据目录（避免写用户 APPDATA）。"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MODU_DATA_DIR", tempfile.mkdtemp(prefix="modu-test-"))

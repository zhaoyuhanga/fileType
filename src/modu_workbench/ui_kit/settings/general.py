"""通用设置页：环境信息、数据目录、全局合规开关。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from modu_workbench.core.platform.media import find_ffmpeg, find_ffprobe
from modu_workbench.core.convert.office_io import find_soffice
from modu_workbench.core.platform import paths as config

from .base import SettingsPage, app_settings

#: 超过这个长度的"连续不可断片段"就强制插一个零宽断点
_MAX_UNBROKEN_RUN = 24
#: 这些字符后面天然是好的断行位置
_BREAK_AFTER = "\\/_-."


def _breakable(text: str) -> str:
    """在长路径里插入零宽断点，让 `QLabel` 能真正折行。

    ## 为什么必须这么做

    `QLabel` 即使开了 `setWordWrap(True)`，`minimumSizeHint()` 仍按
    "最长不可断行"计算。Windows 路径用 `\\` 分隔、没有空格，Qt 找不到断点，
    于是一条长路径就能把整页最小宽度顶到 1500px 以上 —— 默认窗口宽度下
    直接出现横向滚动条。

    ## 这两个坑都是真实踩到的

    1. 只对"数据库路径"做了处理，**忘了外部工具（ffmpeg/ffprobe/LibreOffice）
       那一段** —— 本机装了 WinGet 版 ffmpeg 后，路径长这样：

           C:\\Users\\me\\AppData\\Local\\Microsoft\\WinGet\\Packages\\
           Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\\...\\ffmpeg.exe

       实测单个标签 **1452px**，`general` 页在 980px 宽的窗口里横向滚动 768px。
    2. 光在分隔符后插断点还不够：`Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe`
       这种五十多字符、只有 `.`/`_` 的片段依然很长，所以再加一层
       "每 24 个字符强制一个断点"兜底。

    断点是 U+200B（零宽空格），不影响显示，但**会被复制进去** —— 这是既有
    取舍（路径要可选中复制、又要能折行）；完整原文在 tooltip 里。
    """
    out: list[str] = []
    run = 0
    for char in text or "":
        out.append(char)
        if char in _BREAK_AFTER:
            out.append("\u200b")
            run = 0
        elif char.isspace():
            run = 0
        else:
            run += 1
            if run >= _MAX_UNBROKEN_RUN:
                out.append("\u200b")
                run = 0
    return "".join(out)


class GeneralSettingsPage(SettingsPage):
    title = "通用"
    icon = "🧩"

    def __init__(self, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._settings = app_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)

        self._compliance = QCheckBox("在线书库下载需勾选「个人学习用途」声明")
        form.addRow("在线书库", self._compliance)

        form.addRow("数据目录", self._pickable(str(config.app_data_dir())))
        form.addRow("书库数据库", self._pickable(str(config.library_db_path())))
        form.addRow("乐库数据库", self._pickable(str(config.music_db_path())))
        form.addRow("影视数据库", self._pickable(str(config.video_db_path())))
        form.addRow("图库数据库", self._pickable(str(config.gallery_db_path())))

        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
        soffice = find_soffice()
        # 这三条同样是**长路径**，必须和数据库路径一样插断点 ——
        # 本机 WinGet 版 ffmpeg 的路径曾把这一页顶出 1452px 宽（见 _breakable）
        engine = QLabel(
            f"ffmpeg：{_breakable(ffmpeg) or '未找到（音视频转换 / HLS 合流不可用）'}\n"
            f"ffprobe：{_breakable(ffprobe) or '未找到（时长探测不可用）'}\n"
            f"LibreOffice：{_breakable(soffice) or '未找到（Word/Excel→PDF 使用内置兜底）'}"
        )
        engine.setWordWrap(True)
        engine.setToolTip(
            f"ffmpeg：{ffmpeg or '未找到'}\n"
            f"ffprobe：{ffprobe or '未找到'}\n"
            f"LibreOffice：{soffice or '未找到'}"
        )
        form.addRow("外部工具", engine)
        layout.addLayout(form)
        layout.addStretch(1)

        hint = QLabel(
            "可用环境变量覆盖：MODU_DATA_DIR / MODU_FFMPEG / MODU_FFPROBE / MODU_SOFFICE / "
            "MODU_MUSIC_DIR / MODU_VIDEO_DIR / MODU_GALLERY_DIR / MODU_DEEPSEEK_KEY。"
        )
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    @staticmethod
    def _pickable(text: str) -> QLabel:
        """可选中复制的路径标签。

        **必须主动插入换行**：`QLabel` 即使开了 `setWordWrap`，它的
        `minimumSizeHint` 仍然按"最长不可断行"计算；而 Windows 路径用 `\\` 分隔，
        中文/空格又少，Qt 很难找到断点 —— 于是一条长路径就能把整页最小宽度顶到
        750px 以上，默认窗口宽度下直接出现横向滚动条（实测过：
        `%TEMP%\\pytest-of-xxx\\...` 这种长路径必现）。
        在分隔符后插入零宽断点，既保持"可复制"又让它可以换行。
        """
        label = QLabel(_breakable(text))
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        label.setToolTip(text)
        return label

    def load(self) -> None:
        self._compliance.setChecked(
            self._settings.value("online/compliance_required", True, type=bool)
        )

    def save(self) -> bool:
        before = self._settings.value("online/compliance_required", True, type=bool)
        after = self._compliance.isChecked()
        self._settings.setValue("online/compliance_required", after)
        return before != after


__all__ = ["GeneralSettingsPage"]

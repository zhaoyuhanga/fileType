"""墨读转换引擎（自 fileType 转换能力迁移）。

- registry.py  动作注册与能力判定
- formats.py   格式集合 / 扩展名映射
- text_io.py   文本读写 / 编码识别 / JSON 美化
- pdf_out.py   Qt 文本 → PDF（中文友好）
- image_io.py  Pillow 图片互转
- archive_io.py ZIP / TAR / RAR（含路径穿越防护）
- engine.py    转换分发（含输出防覆盖）
"""

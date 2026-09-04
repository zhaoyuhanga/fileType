"""解析器测试:覆盖各种章节格式 + JUNK 过滤 + fallback。"""
from __future__ import annotations

import os
import tempfile
import unittest

from modu_workbench.core.reader.parser import parse_txt, parse_epub, _split_txt_into_chapters


# ---------- TXT 解析测试 ----------

class TxtBasicTest(unittest.TestCase):
    """基础分章测试。"""

    def test_zhengwen_zh_chap(self):
        """正文 第N章 xxx 格式(重生之财源滚滚类型)。"""
        text = "正文 第一章 重回2004\n\n内容1。\n\n正文 第二章 风云再起\n\n内容2。\n\n正文 第三章 再次相遇\n\n内容3。"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            self.assertEqual(len(book.chapters), 3)
            self.assertIn("正文 第一章", book.chapters[0].title)
            self.assertIn("重回2004", book.chapters[0].title)
            self.assertIn("内容1", book.chapters[0].content)
        finally:
            os.unlink(p)

    def test_bare_zh_chap(self):
        """裸 第N章 xxx 格式(全职高手类型)。"""
        text = "　　第一章 被驱逐的高手\n\n内容A。\n\n　　第二章 C区47号\n\n内容B。"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            self.assertEqual(len(book.chapters), 2)
            self.assertIn("第一章", book.chapters[0].title)
            self.assertIn("被驱逐", book.chapters[0].title)
        finally:
            os.unlink(p)

    def test_eq_wrapped(self):
        """===第N章 xxx=== 包裹式(完美世界类型)。"""
        text = "===序章 大荒===\n\n内容1。\n\n===第一章 朝气蓬勃===\n\n内容2。\n\n===第二章 崛起===\n\n内容3。"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            self.assertEqual(len(book.chapters), 3)
            self.assertIn("序章", book.chapters[0].title)
            self.assertIn("大荒", book.chapters[0].title)
            self.assertIn("第一章", book.chapters[1].title)
        finally:
            os.unlink(p)

    def test_bare_zh_hui(self):
        """裸 第N回/节/卷/篇 格式。"""
        text = "第1回 楔子\n\n内容。\n\n第2回 大闹天宫\n\n内容。"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            self.assertEqual(len(book.chapters), 2)
            self.assertIn("第1回", book.chapters[0].title)
        finally:
            os.unlink(p)

    def test_chinese_numbers(self):
        """中文数字章节。"""
        text = "第十九章 第十九章 标题\n\nx\n\n第二十章 标题二\n\ny"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            self.assertEqual(len(book.chapters), 2)
        finally:
            os.unlink(p)

    def test_arabic_numbers(self):
        """阿拉伯数字章节。"""
        text = "第685章 万卡遇\n\nx\n\n第702章 临行夜\n\ny"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            self.assertEqual(len(book.chapters), 2)
        finally:
            os.unlink(p)

    def test_junk_filter(self):
        """JUNK 关键字不应被识别为标题。"""
        text = (
            "正文 第一章 标题一\n\n"
            "内容。\n\n"
            "求推荐求订阅求收藏!\n\n"          # 纯 JUNK
            "更多内容。\n\n"
            "正文 第二章 标题二\n\n"
            "内容。\n\n"
            "(本章完)\n\n"                     # 章节结束标记
            "PS:感谢打赏!\n\n"                  # 留言
            "正文 第三章 标题三\n\n"
            "内容。"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            self.assertEqual(len(book.chapters), 3, f"应识别 3 章,实际 {len(book.chapters)}: "
                                                  f"{[c.title for c in book.chapters]}")
        finally:
            os.unlink(p)

    def test_junk_zhengwen(self):
        """'正文 第N回' 这种不应该被识别的格式(避免误判)。"""
        # 这个其实不冲突,但要确保不出现就 OK
        text = "正文 第一章 标题一\n\n内容。\n\n正文 第二章 标题二\n\n内容。"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            self.assertEqual(len(book.chapters), 2)
        finally:
            os.unlink(p)

    def test_no_chapter_returns_full(self):
        """没有任何章节标题,整本当一章。"""
        text = "这是一些正文内容,没有章节标题。\n\n继续写。\n\n继续。"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            self.assertEqual(len(book.chapters), 1)
            self.assertEqual(book.chapters[0].title, "全文")
        finally:
            os.unlink(p)

    def test_encoding_gbk(self):
        """GBK 编码正确识别。"""
        import tempfile as tf
        text = "正文 第一章 重回2004\n\n内容。\n\n正文 第二章 风云\n\n内容。"
        with tf.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="gbk") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            self.assertEqual(len(book.chapters), 2)
            self.assertIn("第一章", book.chapters[0].title)
        finally:
            os.unlink(p)


class TxtFallbackTest(unittest.TestCase):
    """Fallback 策略测试。"""

    def test_fallback_separator(self):
        """无章节标题,但有"=========="分隔符。"""
        text = "第一段标题\n==========\n内容1\n==========\n第二段标题\n==========\n内容2"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            # 主正则识别不到(没"第N章"),fallback 到分隔符
            self.assertGreaterEqual(len(book.chapters), 2)
        finally:
            os.unlink(p)

    def test_fallback_blank_lines(self):
        """无章节标题,但有连续空行。"""
        text = "标题一\n内容1\n\n\n标题二\n内容2\n\n\n标题三\n内容3"
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            p = f.name
        try:
            book = parse_txt(p)
            # fallback 到连续空行切分
            self.assertGreaterEqual(len(book.chapters), 2)
        finally:
            os.unlink(p)


# ---------- 用户真实 4 本书测试 ----------


class EpubParserTest(unittest.TestCase):
    def test_round_trip(self):
        from ebooklib import epub
        b = epub.EpubBook()
        b.set_identifier("id-1")
        b.set_title("测试EPUB书")
        b.set_language("zh")
        b.add_author("测试作者")
        c1 = epub.EpubHtml(title="第一章 开端", file_name="c1.xhtml", lang="zh")
        c1.content = "<h1>第一章 开端</h1><p>这是第一章内容。</p>"
        c2 = epub.EpubHtml(title="第二章 发展", file_name="c2.xhtml", lang="zh")
        c2.content = "<h1>第二章 发展</h1><p>第二章的正文段落。</p>"
        b.add_item(c1)
        b.add_item(c2)
        b.toc = (c1, c2)
        b.add_item(epub.EpubNcx())
        b.add_item(epub.EpubNav())
        b.spine = ["nav", c1, c2]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "test.epub")
            epub.write_epub(p, b)
            book = parse_epub(p)
            self.assertTrue(book.ok, msg=book.error)
            self.assertEqual(book.title, "测试EPUB书")
            self.assertEqual(book.author, "测试作者")
            self.assertEqual(len(book.chapters), 2)
            self.assertIn("第一章", book.chapters[0].title)
            self.assertIn("这是第一章内容", book.chapters[0].content)



"""数据族转换：json / xml / ini / yaml 互转，另可输出 txt / markdown / html / pdf / csv。

设计取舍（为什么这么写）：

1. **先解析成普通 Python 对象，再序列化**。四个格式看起来是「6 组两两转换」，
   实际上是「4 个解析器 + 9 个序列化器」。中间层用 dict / list / 标量，
   新增格式只需要加一对函数，不用写 n² 条分支（registry 里已经登记了全部组合）。

2. **XML 的对象模型**（本模块自己定的「唯一事实来源」，其它模块若要读 XML 也按这个来）::

       <book id="7">前言<item>a</item><item>b</item></book>
       # 属性 → "@属性名"，文本 → "#text"，重复子标签 → 列表，同名单标签 → 标量
       {"book": {"@id": "7", "#text": "前言", "item": ["a", "b"]}}

   - 叶子元素（没有属性也没有子元素）直接给**字符串**，例如 ``<title>书</title>`` → ``"书"``，
     这样 ``{"book": {"title": "书"}}`` 这类常见文档读起来最自然；
   - 元素只要带属性或子元素就一定是 dict，属性带 ``@`` 前缀可以和子标签名区分开
     （XML 里不存在以 ``@`` 开头的合法标签名），``#text`` 同理用 ``#`` 前缀避让；
   - **代价（必须知道）**：XML 的文本没有类型信息，所以解析出来一律是字符串
     （``<n>1</n>`` → ``"1"``，不是整数）；混合内容里子元素之后的文本（ElementTree 的
     ``tail``）会被丢弃。想保住类型/混合内容，请用 JSON / YAML 而不是 XML 做中转。

3. **输出根标签名**：对象是「单键 dict」时直接用那个键当根名，这样
   ``{"book": {...}}`` ↔ ``<book>…</book>`` 能原样往返；否则用稳定的默认根名
   ``root``。顶层是列表时，每个元素写成 ``<item>``。

4. **INI 只能表达两层**（段 → 键 = 值），这是格式本身的限制，不是实现偷懒：
   遇到嵌套结构直接抛中文 ``ValueError``，让用户换 JSON / YAML / XML，而不是
   把 dict 硬转成 ``{'a': 1}`` 这种字符串塞进值里、转回来再也不是原数据。

5. **报错要能定位**：JSON 给行/列，XML 给行/列（并说明常见原因），YAML 用 PyYAML 的
   ``problem_mark`` 给行/列，INI 带上 configparser 的原始信息。所有失败都抛
   ``ValueError``，engine 会把消息原样显示给用户，绝不能漏出英文堆栈。

6. 所有文本产物统一走 ``text_io.write_text``（编码/换行只在一个地方管），
   PDF 走 ``pdf_out.render_text_pdf``。
"""
from __future__ import annotations

import csv
import html as html_mod
import io
import json
import re
import xml.etree.ElementTree as ET
from configparser import ConfigParser, Error as ConfigParserError
from pathlib import Path
from typing import Any

from . import text_io

# 输入格式（与 formats.DATA_FORMATS 保持一致；这里不 import 它，避免和元数据表耦合）
DATA_FORMATS = ("json", "xml", "ini", "yaml")
# 无「单键包裹」时的稳定根标签名
XML_ROOT_NAME = "root"
# 列表统一写成的标签名
XML_LIST_ITEM_NAME = "item"
# CSV 导出时，从这些键里找「真正的行数组」（与 engine 里 json→csv 的约定一致）
CSV_WRAPPER_KEYS = ("items", "data", "list", "records", "rows")
# 顶层是标量数组时使用的列名
CSV_VALUE_COLUMN = "value"
# INI 的 DEFAULT 段在 Python 里是「会被合并进每个段」的特殊段，单独还原这个键名
INI_DEFAULT_SECTION = "DEFAULT"

# XML 标签名合法性：正常名（字母/下划线/中文开头，后接字母数字与 . - _）或
# ElementTree 的命名空间写法 "{uri}local"。数字开头的键（YAML 的 `1: a` 会解析成
# int 键）必须拦住：ET.tostring 不校验名字，会写出 <1> 这种打不开的 XML。
_XML_NAME = r"[^\W\d][\w.\-]*"
_TAG_PATTERN = re.compile(rf"^(?:{_XML_NAME}|\{{[^{{}}]*\}}{_XML_NAME})$")


# ---------------------------------------------------------------- 入口

def convert_data(
    source: str | Path,
    source_format: str,
    target: str,
    output: str | Path,
    title: str = "",
) -> None:
    """数据族统一入口（engine._run_data 调用）。

    :param source: 源文件路径
    :param source_format: ``json`` / ``xml`` / ``ini`` / ``yaml``
    :param target: ``txt`` / ``markdown`` / ``html`` / ``pdf`` / ``json`` / ``xml``
                   / ``yaml`` / ``ini`` / ``csv``
    :param output: 目标文件路径（engine 负责起名与防覆盖，这里直接写这个路径）
    :param title: PDF / HTML 的标题，空则用源文件名
    """
    source_path = Path(source)
    data_format = _normalize_format(source_format)
    document_title = (title or "").strip() or source_path.stem
    data = parse_data(source_path, data_format)

    if target == "pdf":
        pretty = _pretty_json(data)
        from .pdf_out import render_text_pdf

        render_text_pdf(pretty, output, title=document_title)
        return

    text_io.write_text(output, serialize_data(data, target, title=document_title))


def parse_data(source: str | Path, source_format: str) -> Any:
    """把源文件解析成普通 Python 对象（dict / list / 标量）。

    单独暴露出来是为了让「解析」和「序列化」可以分别测试/复用；
    转不出普通对象的格式（比如非法 JSON）一律抛中文 ``ValueError``。
    """
    path = Path(source)
    data_format = _normalize_format(source_format)
    if not path.exists():
        raise ValueError(f"源文件不存在：{path}")

    if data_format == "json":
        return parse_json(text_io.read_text_smart(path))
    if data_format == "yaml":
        return parse_yaml(text_io.read_text_smart(path))
    if data_format == "ini":
        return parse_ini(text_io.read_text_smart(path), source_name=str(path))
    return parse_xml(path)


def serialize_data(data: Any, target: str, title: str = "") -> str:
    """把普通对象序列化成目标格式的文本（PDF 除外，PDF 由调用方渲染）。"""
    if target == "json":
        return _pretty_json(data) + "\n"
    if target == "yaml":
        return data_to_yaml_text(data)
    if target == "xml":
        return data_to_xml_text(data)
    if target == "ini":
        return data_to_ini_text(data)
    if target == "csv":
        return data_to_csv_text(data)
    if target == "markdown":
        return f"```json\n{_pretty_json(data)}\n```\n"
    if target in ("txt", "pdf"):
        # pdf 目标也先要一份好看的文本，再由 convert_data 交给 render_text_pdf
        return _pretty_json(data) + "\n"
    if target == "html":
        return data_to_html_text(data, title)
    raise ValueError(f"不支持的数据目标：{target}（可用：txt / markdown / html / pdf / json / xml / yaml / ini / csv）")


# ---------------------------------------------------------------- JSON

def parse_json(text: str) -> Any:
    """解析 JSON 文本；语法错误给出「第几行第几列」。"""
    trimmed = (text or "").lstrip("\ufeff").strip()
    if not trimmed:
        raise ValueError("JSON 文件是空的，没有可转换的内容")
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError as error:
        line, column = _line_column(trimmed, error.pos)
        raise ValueError(
            f"JSON 语法错误（第 {line} 行，第 {column} 列）：{error.msg}。"
            "常见原因：少了逗号、多了尾随逗号、引号没配对、括号没闭合。"
        ) from error


def _pretty_json(data: Any) -> str:
    # default=str：PyYAML 会把 `2024-01-01` 解析成 datetime.date，还会给出 set / bytes
    # 等类型，json.dumps 默认会抛英文 TypeError；转成字符串至少不丢信息、不炸界面。
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------- YAML

def parse_yaml(text: str) -> Any:
    """解析 YAML 文本；错误定位用 PyYAML 的 problem_mark。"""
    yaml = _yaml_module()
    trimmed = (text or "").lstrip("\ufeff")
    if not trimmed.strip():
        raise ValueError("YAML 文件是空的，没有可转换的内容")
    try:
        return yaml.safe_load(trimmed)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None) or getattr(error, "context_mark", None)
        problem = getattr(error, "problem", None) or str(error)
        if mark is not None:
            raise ValueError(
                f"YAML 语法错误（第 {mark.line + 1} 行，第 {mark.column + 1} 列）：{problem}。"
                "常见原因：缩进用了 Tab、冒号后面少空格、列表项前的 '-' 没对齐、引号没配对。"
            ) from error
        raise ValueError(f"YAML 语法错误：{problem}。请检查缩进与冒号写法。") from error


def data_to_yaml_text(data: Any) -> str:
    yaml = _yaml_module()
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _yaml_module():  # noqa: ANN202 - 返回模块对象，避免顶层 import 在缺依赖时炸掉整个包
    """惰性拿到 PyYAML。

    不在模块顶层 ``import yaml``：PyYAML 不是标准库，某些精简发行版里可能缺失，
    顶层 import 会让整个转换面板连带崩掉；这里改成按需导入并给中文安装提示。
    """
    try:
        import yaml
    except ImportError as error:  # pragma: no cover - 依赖缺失分支
        raise ValueError(
            "YAML 读写需要 PyYAML：请执行 pip install PyYAML（或 pip install -r requirements.txt）后重试"
        ) from error
    return yaml


# ---------------------------------------------------------------- XML

def parse_xml(source: str | Path) -> dict[str, Any]:
    """解析 XML 文件 → ``{根标签: 对象}``（对象模型见模块文档）。

    用 ``ET.parse`` 直接读文件而不是先解码成 str：XML 头部可能写着
    ``encoding="gb2312"``，交给 ElementTree/expat 处理才不会乱码。
    """
    try:
        tree = ET.parse(source)
    except ET.ParseError as error:
        line, column = _xml_error_position(error)
        location = f"（第 {line} 行，第 {column} 列）" if line else ""
        raise ValueError(
            f"XML 语法错误{location}：{error}。"
            "常见原因：标签没有闭合、属性值没加引号、& < > 没有转义、根元素不止一个。"
        ) from error
    except OSError as error:
        raise ValueError(f"XML 文件读取失败：{error}") from error
    root = tree.getroot()
    return {root.tag: element_to_obj(root)}


def parse_xml_text(text: str) -> dict[str, Any]:
    """解析 XML 字符串（测试/复用用），错误同样是中文 ``ValueError``。"""
    try:
        root = ET.fromstring((text or "").lstrip("\ufeff"))
    except ET.ParseError as error:
        line, column = _xml_error_position(error)
        location = f"（第 {line} 行，第 {column} 列）" if line else ""
        raise ValueError(
            f"XML 语法错误{location}：{error}。"
            "常见原因：标签没有闭合、属性值没加引号、& < > 没有转义。"
        ) from error
    return {root.tag: element_to_obj(root)}


def element_to_obj(element: ET.Element) -> Any:
    """ElementTree 元素 → 普通对象（属性 ``@name``、文本 ``#text``、同名子标签 → 列表）。"""
    attributes = {f"@{name}": value for name, value in element.attrib.items()}
    text = (element.text or "").strip()
    children = list(element)

    if not attributes and not children:
        # 叶子：直接给文本（去掉首尾空白），保证 {"a": "1"} 这种最简结构读写都自然
        return text

    result: dict[str, Any] = dict(attributes)
    if text:
        result["#text"] = text
    for child in children:
        value = element_to_obj(child)
        if child.tag in result:
            existing = result[child.tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[child.tag] = [existing, value]
        else:
            result[child.tag] = value
    return result


def data_to_xml_text(data: Any) -> str:
    """普通对象 → XML 文本（带 XML 声明 + 两空格缩进）。"""
    root = obj_to_xml(data)
    ET.indent(root, space="  ")  # Python 3.9+；工程要求 3.10+
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body + "\n"


def obj_to_xml(data: Any) -> ET.Element:
    """普通对象 → ElementTree 根元素。"""
    root_name, payload = _root_for(data)
    return _value_to_element(root_name, payload, "")


def _root_for(data: Any) -> tuple[str, Any]:
    """决定根标签名与「喂给根元素的内容」。

    单键 dict（``{"book": {...}}``）直接用那个键当根名、值当内容，这样
    XML → 对象 → XML 能原样往返；其余情况用稳定根名 ``root``，
    顶层是列表时由 ``_value_to_element`` 写成 ``<root><item>…</item></root>``。
    """
    if isinstance(data, dict) and len(data) == 1:
        key, value = next(iter(data.items()))
        if _is_valid_tag(key):
            return key, value
    return XML_ROOT_NAME, data


def _value_to_element(tag: str, value: Any, path: str) -> ET.Element:
    _check_tag(tag, path)
    element = ET.Element(tag)
    here = f"{path}/{tag}" if path else tag

    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.startswith("@"):
                attribute_name = key[1:]
                if not attribute_name:
                    raise ValueError(f"XML 输出失败：属性名是空的（位置：{here}）")
                element.set(attribute_name, _scalar_text(item))
                continue
            if key == "#text":
                element.text = _scalar_text(item)
                continue
            if isinstance(item, list):
                for entry in item:
                    element.append(_value_to_element(str(key), entry, here))
                continue
            element.append(_value_to_element(str(key), item, here))
        return element

    if isinstance(value, list):
        # 非 dict 值遇到列表：统一写成一串 <item>
        for entry in value:
            element.append(_value_to_element(XML_LIST_ITEM_NAME, entry, here))
        return element

    if value is None:
        return element
    element.text = _scalar_text(value)
    return element


def _is_valid_tag(tag: Any) -> bool:
    return isinstance(tag, str) and bool(_TAG_PATTERN.match(tag)) and tag not in ("#text",)


def _check_tag(tag: Any, path: str) -> None:
    if _is_valid_tag(tag):
        return
    where = path or "根节点"
    raise ValueError(
        f"XML 输出失败：键「{tag}」不能当 XML 标签名（位置：{where}）。"
        "XML 标签不能带空格、& < > \" ' / 等字符，也不能以数字开头；"
        "请改名，或改输出成 JSON / YAML。"
    )


def _scalar_text(value: Any) -> str:
    """标量 → XML/属性文本（bool 写 true/false，None 写空，容器退化成 JSON 字符串）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _xml_error_position(error: ET.ParseError) -> tuple[int, int]:
    """ParseError.position 是 (行, 列)，行从 1 开始、列从 0 开始 → 列 +1 更符合人的习惯。"""
    position = getattr(error, "position", None)
    if not position:
        return 0, 0
    line, column = position
    return int(line), int(column) + 1


# ---------------------------------------------------------------- INI

def parse_ini(text: str, source_name: str = "<INI>") -> dict[str, Any]:
    """解析 INI 文本 → ``{段名: {键: 值}}``（两级映射）。

    - ``interpolation=None``：INI 里的 ``%`` 很常见（进度、格式串），默认的
      BasicInterpolation 会把它当插值直接报错；
    - ``optionxform = str``：configparser 默认把键名转小写，会把 ``UserName``
      静默改成 ``username``，往返一次数据就变了，这里明确保留大小写；
    - ``DEFAULT`` 段的值在 configparser 里会合并进每个段，解析时把它们单独放回
      ``DEFAULT`` 键，并从各段里剔除（否则往返一次默认值就会被复制到每个段里）。
    """
    if not (text or "").strip():
        raise ValueError("INI 文件是空的（至少需要一个 [段名] 和若干 键 = 值）")
    parser = _new_ini_parser()
    try:
        parser.read_string(text.lstrip("\ufeff"), source=source_name)
    except ConfigParserError as error:
        raise ValueError(
            f"INI 语法错误：{error}。"
            "常见原因：段名没有用 [方括号] 包起来、行里缺少 = 或 :、同一段里键名重复。"
        ) from error

    defaults = dict(parser.defaults())
    data: dict[str, Any] = {}
    if defaults:
        data[INI_DEFAULT_SECTION] = defaults
    for section in parser.sections():
        entries: dict[str, Any] = {}
        for key, value in parser.items(section, raw=True):
            if key in defaults and defaults[key] == value:
                continue  # 这是 DEFAULT 段继承来的，已在 DEFAULT 里保留一份
            entries[key] = value
        data[section] = entries
    return data


def data_to_ini_text(data: Any) -> str:
    """普通对象 → INI 文本；只接受「段 → 键 = 值」两层，否则抛中文错误。"""
    if not isinstance(data, dict):
        raise ValueError(
            f"INI 只能表达「段 → 键 = 值」两层结构，当前顶层是{_type_name(data)}，无法写入。"
            "请改用 JSON / YAML / XML 目标。"
        )
    parser = _new_ini_parser()
    for section, entries in data.items():
        section_name = str(section)
        if not isinstance(entries, dict):
            raise ValueError(
                f"INI 只能表达「段 → 键 = 值」两层结构：[{section_name}] 的值是{_type_name(entries)}，"
                "不是一组键值。请改用 JSON / YAML / XML 目标。"
            )
        if not section_name:
            raise ValueError("INI 段名不能为空")
        if section_name != parser.default_section and not parser.has_section(section_name):
            parser.add_section(section_name)
        for key, value in entries.items():
            if isinstance(value, (dict, list)):
                raise ValueError(
                    f"INI 的 [{section_name}] 段里，键「{key}」的值是{_type_name(value)}，"
                    "「键 = 值」表达不了嵌套结构。请改用 JSON / YAML / XML 目标。"
                )
            parser.set(section_name, str(key), _scalar_text(value))

    buffer = io.StringIO()
    parser.write(buffer)
    return buffer.getvalue()


def _new_ini_parser() -> ConfigParser:
    parser = ConfigParser(interpolation=None, delimiters=("=", ":"), inline_comment_prefixes=None)
    parser.optionxform = str  # type: ignore[assignment]  # 保留键名大小写，见 parse_ini 文档
    return parser


def _type_name(value: Any) -> str:
    return {
        dict: "映射（对象）",
        list: "列表",
        str: "字符串",
        bool: "布尔值",
        int: "数字",
        float: "数字",
        type(None): "空值",
    }.get(type(value), f"{type(value).__name__} 类型")


# ---------------------------------------------------------------- CSV

def data_to_csv_text(data: Any) -> str:
    """普通对象 → CSV 文本（对象数组，或「一组键值」→ 一行）。

    与 engine 里 json→csv 的约定保持一致：dict 先尝试从 items/data/list/records/rows
    里取真正的行数组，取不到就把这个 dict 当成单行记录。
    """
    rows = _csv_rows(data)
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    if not columns:
        raise ValueError("数据里没有可导出 CSV 的字段")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_cell(row.get(key, "")) for key in columns})
    return buffer.getvalue()


def _csv_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        if not data:
            raise ValueError("数据里没有可导出 CSV 的内容（列表是空的）")
        if all(isinstance(item, dict) for item in data):
            return [{str(key): value for key, value in item.items()} for item in data]
        if all(not isinstance(item, (dict, list)) for item in data):
            return [{CSV_VALUE_COLUMN: item} for item in data]
        raise ValueError(
            "数据转 CSV 需要「对象数组」（每个元素都是对象），当前列表里混有嵌套的映射/列表。"
            "请改用 JSON / YAML / XML 目标。"
        )
    if isinstance(data, dict):
        for key in CSV_WRAPPER_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                return _csv_rows(value)
        if all(not isinstance(value, (dict, list)) for value in data.values()):
            return [{str(key): value for key, value in data.items()}]
        raise ValueError(
            "数据转 CSV 需要「对象数组」或「一组键值」；当前对象里还有嵌套的映射/列表。"
            "请改用 JSON / YAML / XML 目标。"
        )
    raise ValueError(
        f"数据转 CSV 需要「对象数组」或「一组键值」，当前顶层是{_type_name(data)}。"
        "请改用 JSON / YAML 目标。"
    )


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        # 单元格里塞不下嵌套结构，退化成 JSON 文本，至少不丢信息、也不出现 Python 的 repr
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---------------------------------------------------------------- 文本 / HTML

def data_to_html_text(data: Any, title: str = "") -> str:
    """普通对象 → 简单 HTML（JSON 逐行成段并转义，样式保持与其它家族一致）。"""
    pretty = _pretty_json(data)
    paragraphs = "".join(
        f"<p>{html_mod.escape(line) or '&nbsp;'}</p>" for line in pretty.splitlines()
    )
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{html_mod.escape(title)}</title></head><body>{paragraphs}</body></html>\n"
    )


def _normalize_format(source_format: str) -> str:
    value = (source_format or "").lstrip(".").lower()
    if value not in DATA_FORMATS:
        raise ValueError(
            f"数据转换不支持「{source_format}」源格式（只支持 JSON / XML / INI / YAML）"
        )
    return value


def _line_column(text: str, index: int) -> tuple[int, int]:
    """字符下标 → (行, 列)，均从 1 开始（与 text_io 的定位方式一致）。"""
    before = text[: index + 1]
    line = before.count("\n") + 1
    column = index - before.rfind("\n")
    return line, column

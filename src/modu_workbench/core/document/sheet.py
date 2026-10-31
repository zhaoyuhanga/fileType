"""墨软文档：自动填充与计算（MR-DOC-302）。

四块能力，全部**离线可用**（不依赖 Excel/WPS）：

1. **自动填充**（`detect_series` / `fill_series`）：等差数列、日期、工作日、月份、
   星期、中文序号与自定义列表、文本+序号、公式（相对引用自动平移）；
2. **智能填充**（`infer_pattern` / `smart_fill`）：按"示例输入 → 示例输出"反推规则
   —— 拆分姓名、提取号码/邮箱/金额、加前后缀、大小写、编号补零、按关键词补全分类；
3. **公式计算**（`FormulaEngine`）：Excel 常用函数、跨表引用、数组公式、循环引用检测；
4. **数据统计与检查**（`summarize_table` / `group_by` / `pivot` / `chart_series` /
   `check_formulas` / `convert_units`），以及给 AI 用的自然语言生成/解释/修复公式。

公式引擎是一个**受控子集**：不执行 VBA、不访问网络与文件系统，
只对给定表格求值 —— 这样"打开别人的表格就执行任意代码"的风险不存在。
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

from .models import TableData

# ---------------------------------------------------------------- 列/引用


def column_index(letters: str) -> int:
    """A→0、Z→25、AA→26。"""
    value = 0
    for char in (letters or "").strip().upper():
        if not char.isalpha():
            raise ValueError(f"非法列名：{letters}")
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def column_letters(index: int) -> str:
    """0→A、25→Z、26→AA。"""
    value = int(index) + 1
    letters = ""
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


_REF_RE = re.compile(r"^(?:(?P<sheet>'[^']+'|[^!]+)!)?(?P<col>\$?[A-Za-z]{1,3})(?P<row>\$?\d+)$")


@dataclass(frozen=True)
class CellRef:
    sheet: str = ""
    row: int = 0          # 0 基
    column: int = 0
    absolute_row: bool = False
    absolute_column: bool = False

    def text(self, *, with_sheet: bool = False) -> str:
        column = ("$" if self.absolute_column else "") + column_letters(self.column)
        row = ("$" if self.absolute_row else "") + str(self.row + 1)
        prefix = ""
        if with_sheet and self.sheet:
            prefix = f"'{self.sheet}'!" if (" " in self.sheet or "-" in self.sheet) else f"{self.sheet}!"
        return f"{prefix}{column}{row}"


def parse_cell_ref(text: str, *, default_sheet: str = "") -> Optional[CellRef]:
    """解析 `Sheet1!$B$3` / `B3`；不是单元格引用返回 None。"""
    match = _REF_RE.match((text or "").strip())
    if not match:
        return None
    sheet = match.group("sheet") or default_sheet
    sheet = sheet.strip("'")
    column_text = match.group("col")
    row_text = match.group("row")
    return CellRef(
        sheet=sheet,
        row=int(row_text.lstrip("$")) - 1,
        column=column_index(column_text.lstrip("$")),
        absolute_row=row_text.startswith("$"),
        absolute_column=column_text.startswith("$"),
    )


class FormulaError(Exception):
    """公式错误：`code` 是 Excel 风格错误码（#DIV/0! / #VALUE! …）。"""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}{('：' + detail) if detail else ''}")


# ---------------------------------------------------------------- 值模型


class Array(list):
    """区域/数组值：既可按行遍历（Excel 语义），也保留二维形状。"""

    def __init__(self, values: Iterable[Any], rows: int = 1, cols: int = 0):
        super().__init__(values)
        self.rows = max(1, int(rows))
        self.cols = int(cols) or (len(self) // max(1, self.rows)) or 1

    @property
    def matrix(self) -> list[list[Any]]:
        return [list(self[index * self.cols:(index + 1) * self.cols]) for index in range(self.rows)]

    def single(self) -> Any:
        return self[0] if len(self) == 1 else self


def to_number(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 0.0
    text = str(value).strip().replace(",", "")
    if text in ("", "-"):
        return 0.0
    if text.endswith("%"):
        try:
            return float(text[:-1]) / 100.0
        except ValueError:
            raise FormulaError("#VALUE!", f"无法把「{value}」当成数字")
    try:
        return float(text)
    except ValueError:
        raise FormulaError("#VALUE!", f"无法把「{value}」当成数字")


def to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def cell_value(raw: str) -> Any:
    """单元格文本 → 值：空串给 ""、数字给 float、TRUE/FALSE 给 bool。"""
    text = (raw or "").strip()
    if text == "":
        return ""
    if text.upper() in ("TRUE", "FALSE"):
        return text.upper() == "TRUE"
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return raw


def _truthy(value: Any) -> bool:
    if isinstance(value, Array):
        value = value.single()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    text = str(value).strip().upper()
    return text not in ("", "FALSE", "0", "NO", "否")


# ---------------------------------------------------------------- 公式引擎

_TOKEN_RE = re.compile(
    r"""\s*(?:
      (?P<number>\d+\.?\d*(?:[eE][+-]?\d+)?)
    | (?P<string>"(?:[^"]|"")*")
    | (?P<ref>
          '(?:[^']|'')+'!\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?
        | [A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*!\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?
        | \$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?
      )
    | (?P<func>[A-Za-z_][A-Za-z0-9_.]*\s*(?=\())
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<op><=|>=|<>|[-+*/^&%(),;{}<>=])
    )""",
    re.X,
)


@dataclass
class _Token:
    kind: str
    value: str


def _tokenize(formula: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    while position < len(formula):
        match = _TOKEN_RE.match(formula, position)
        if not match or match.end() == position:
            raise FormulaError("#NAME?", f"无法识别的公式片段：{formula[position:position + 12]!r}")
        position = match.end()
        kind = match.lastgroup or ""
        tokens.append(_Token(kind, match.group(kind)))
    return tokens


class FormulaEngine:
    """在一组工作表（sheet 名 → 二维文本）上求值。"""

    def __init__(self, sheets: dict[str, Sequence[Sequence[str]]] | None = None):
        self.sheets: dict[str, list[list[str]]] = {
            name: [[("" if cell is None else str(cell)) for cell in row] for row in rows]
            for name, rows in (sheets or {}).items()
        }
        self._cache: dict[tuple[str, int, int], Any] = {}
        self._visiting: set[tuple[str, int, int]] = set()
        # 解析状态：每次 evaluate 前保存、结束后恢复 ——
        # 求值单元格时会递归 evaluate（单元格里也是公式），若不保存就会把外层公式的
        # token 流冲掉（表现为"缺少 )"这类莫名其妙的报错）
        self._tokens: list[_Token] = []
        self._position = 0
        self._sheet = ""
        self._row = 0
        self._column = 0
        self._array_mode = False

    # ---------- 表格注册 ----------

    @classmethod
    def from_tables(cls, tables: Sequence[TableData]) -> "FormulaEngine":
        sheets: dict[str, list[list[str]]] = {}
        for index, table in enumerate(tables):
            name = table.name or f"Sheet{index + 1}"
            sheets[name] = table.normalized()
        return cls(sheets)

    def sheet_names(self) -> list[str]:
        return list(self.sheets)

    def cell_text(self, sheet: str, row: int, column: int) -> str:
        rows = self.sheets.get(sheet)
        if rows is None or row < 0 or column < 0 or row >= len(rows):
            return ""
        line = rows[row]
        return line[column] if column < len(line) else ""

    # ---------- 求值 ----------

    def value(self, sheet: str, row: int, column: int) -> Any:
        """单元格的值（公式会递归求值，循环引用报 #CIRC!）。"""
        key = (sheet, row, column)
        if key in self._cache:
            return self._cache[key]
        if key in self._visiting:
            raise FormulaError("#CIRC!", f"{sheet}!{column_letters(column)}{row + 1} 出现循环引用")
        text = self.cell_text(sheet, row, column)
        if not text.startswith("="):
            value = cell_value(text)
            self._cache[key] = value
            return value
        self._visiting.add(key)
        try:
            value = self.evaluate(text, sheet=sheet, row=row, column=column)
        except FormulaError:
            raise
        except Exception as error:  # noqa: BLE001
            raise FormulaError("#VALUE!", str(error)) from error
        finally:
            self._visiting.discard(key)
        self._cache[key] = value
        return value

    def evaluate(self, formula: str, *, sheet: str = "", row: int = 0, column: int = 0) -> Any:
        """求值一个公式（可带前缀 `=`，也接受 `{=…}` 数组公式写法）。"""
        expression = (formula or "").strip()
        array_mode = False
        if expression.startswith("{"):
            expression = expression.strip("{}").strip()
            array_mode = True
        if expression.startswith("="):
            expression = expression[1:].strip()
        if not expression:
            return ""
        saved = (self._tokens, self._position, self._sheet, self._row, self._column,
                 self._array_mode)
        self._tokens = _tokenize(expression)
        self._position = 0
        self._sheet, self._row, self._column = sheet, row, column
        self._array_mode = array_mode
        try:
            value = self._parse_expression()
            if self._position < len(self._tokens):
                token = self._tokens[self._position]
                raise FormulaError("#NAME?", f"公式末尾有多余内容：{token.value!r}")
            return value
        finally:
            (self._tokens, self._position, self._sheet, self._row, self._column,
             self._array_mode) = saved

    # ---------- 递归下降 ----------

    def _peek(self) -> Optional[_Token]:
        return self._tokens[self._position] if self._position < len(self._tokens) else None

    def _next(self) -> _Token:
        token = self._peek()
        if token is None:
            raise FormulaError("#NAME?", "公式意外结束")
        self._position += 1
        return token

    def _accept(self, value: str) -> bool:
        token = self._peek()
        if token is not None and token.value.strip().upper() == value.upper():
            self._position += 1
            return True
        return False

    def _expect(self, value: str) -> None:
        if not self._accept(value):
            token = self._peek()
            raise FormulaError("#NAME?", f"缺少 {value}（实际：{token.value if token else '结束'}）")

    def _parse_expression(self) -> Any:
        return self._parse_comparison()

    def _parse_comparison(self) -> Any:
        left = self._parse_concat()
        while True:
            token = self._peek()
            if token is None or token.value not in ("=", "<>", "<", "<=", ">", ">="):
                return left
            operator = self._next().value
            right = self._parse_concat()
            left = self._compare(operator, left, right)

    def _parse_concat(self) -> Any:
        left = self._parse_additive()
        while self._peek() is not None and self._peek().value == "&":
            self._next()
            right = self._parse_additive()
            left = self._elementwise(lambda a, b: to_text(a) + to_text(b), left, right)
        return left

    def _parse_additive(self) -> Any:
        left = self._parse_multiplicative()
        while self._peek() is not None and self._peek().value in ("+", "-"):
            operator = self._next().value
            right = self._parse_multiplicative()
            if operator == "+":
                left = self._elementwise(lambda a, b: to_number(a) + to_number(b), left, right)
            else:
                left = self._elementwise(lambda a, b: to_number(a) - to_number(b), left, right)
        return left

    def _parse_multiplicative(self) -> Any:
        left = self._parse_power()
        while self._peek() is not None and self._peek().value in ("*", "/"):
            operator = self._next().value
            right = self._parse_power()
            if operator == "*":
                left = self._elementwise(lambda a, b: to_number(a) * to_number(b), left, right)
            else:
                left = self._elementwise(self._divide, left, right)
        return left

    def _parse_power(self) -> Any:
        left = self._parse_unary()
        if self._peek() is not None and self._peek().value == "^":
            self._next()
            right = self._parse_unary()
            return self._elementwise(lambda a, b: to_number(a) ** to_number(b), left, right)
        return left

    def _parse_unary(self) -> Any:
        token = self._peek()
        if token is not None and token.value in ("-", "+"):
            operator = self._next().value
            value = self._parse_unary()
            if operator == "-":
                return self._elementwise(lambda item: -to_number(item), value)
            return value
        return self._parse_postfix()

    def _parse_postfix(self) -> Any:
        value = self._parse_primary()
        while self._peek() is not None and self._peek().value == "%":
            self._next()
            value = self._elementwise(lambda item: to_number(item) / 100.0, value)
        return value

    def _parse_primary(self) -> Any:
        token = self._next()
        if token.kind == "number":
            return float(token.value)
        if token.kind == "string":
            return token.value[1:-1].replace('""', '"')
        if token.kind == "ref":
            return self._resolve_reference(token.value)
        if token.kind == "func":
            return self._call_function(token.value.strip())
        if token.kind == "ident":
            name = token.value.strip().upper()
            if name == "TRUE":
                return True
            if name == "FALSE":
                return False
            raise FormulaError("#NAME?", f"未知名称：{token.value}")
        if token.value == "(":
            value = self._parse_expression()
            self._expect(")")
            return value
        if token.value == "{":
            return self._parse_array_literal()
        raise FormulaError("#NAME?", f"无法解析：{token.value!r}")

    def _parse_array_literal(self) -> Array:
        rows: list[list[Any]] = [[]]
        while True:
            rows[-1].append(self._parse_expression())
            token = self._peek()
            if token is None:
                raise FormulaError("#NAME?", "数组常量没有闭合的 }")
            if token.value == ",":
                self._next()
                continue
            if token.value == ";":
                self._next()
                rows.append([])
                continue
            self._expect("}")
            break
        flat = [item for row in rows for item in row]
        values = [item[0] if isinstance(item, Array) and len(item) == 1 else item for item in flat]
        return Array(values, rows=len(rows), cols=len(rows[0]) if rows else 1)

    # ---------- 运算辅助 ----------

    @staticmethod
    def _divide(left: Any, right: Any) -> float:
        denominator = to_number(right)
        if denominator == 0:
            raise FormulaError("#DIV/0!", "除数为 0")
        return to_number(left) / denominator

    @staticmethod
    def _elementwise(operation: Callable[[Any, Any], Any], left: Any, right: Any) -> Any:
        left_array = isinstance(left, Array)
        right_array = isinstance(right, Array)
        if not left_array and not right_array:
            return operation(left, right)
        left_items = list(left) if left_array else None
        right_items = list(right) if right_array else None
        if left_items is not None and right_items is not None:
            if len(left_items) == 1:
                left_items = left_items * len(right_items)
            if len(right_items) == 1:
                right_items = right_items * len(left_items)
            if len(left_items) != len(right_items):
                raise FormulaError("#VALUE!", "数组长度不一致")
            return _rebuild([operation(a, b) for a, b in zip(left_items, right_items)],
                            left, right)
        if left_items is not None:
            return _rebuild([operation(item, right) for item in left_items], left, right)
        return _rebuild([operation(left, item) for item in right_items or []], left, right)

    def _compare(self, operator: str, left: Any, right: Any) -> Any:
        def compare(a: Any, b: Any) -> bool:
            if isinstance(a, str) or isinstance(b, str):
                first, second = to_text(a), to_text(b)
            else:
                first, second = to_number(a), to_number(b)
            if operator == "=":
                return first == second
            if operator == "<>":
                return first != second
            if operator == "<":
                return first < second
            if operator == "<=":
                return first <= second
            if operator == ">":
                return first > second
            return first >= second

        return self._elementwise(compare, left, right)

    # ---------- 引用 ----------

    def _resolve_reference(self, text: str) -> Any:
        if ":" in text:
            return self._resolve_range(text)
        reference = parse_cell_ref(text, default_sheet=self._sheet)
        if reference is None:
            raise FormulaError("#REF!", f"非法引用：{text}")
        sheet = reference.sheet or self._sheet
        if sheet and sheet not in self.sheets and sheet != self._sheet:
            raise FormulaError("#REF!", f"找不到工作表：{sheet}")
        return self.value(sheet, reference.row, reference.column)

    def _resolve_range(self, text: str) -> Array:
        start_text, _, end_text = text.partition(":")
        start = parse_cell_ref(start_text, default_sheet=self._sheet)
        end = parse_cell_ref(end_text, default_sheet=start.sheet if start else self._sheet)
        if start is None or end is None:
            raise FormulaError("#REF!", f"非法区域：{text}")
        sheet = start.sheet or end.sheet or self._sheet
        if sheet not in self.sheets and sheet != self._sheet:
            raise FormulaError("#REF!", f"找不到工作表：{sheet}")
        top, bottom = sorted((start.row, end.row))
        left, right = sorted((start.column, end.column))
        values: list[Any] = []
        for row in range(top, bottom + 1):
            for column in range(left, right + 1):
                values.append(self.value(sheet, row, column))
        return Array(values, rows=bottom - top + 1, cols=right - left + 1)

    # ---------- 函数 ----------

    def _call_function(self, name: str) -> Any:
        self._expect("(")
        arguments: list[Any] = []
        if not self._accept(")"):
            while True:
                arguments.append(self._parse_expression())
                if self._accept(","):
                    continue
                self._expect(")")
                break
        function = FUNCTIONS.get(name.upper().rstrip("_"))
        if function is None:
            raise FormulaError("#NAME?", f"不支持的函数：{name}（可查看「函数列表」）")
        try:
            return function(arguments, self)
        except FormulaError:
            raise
        except ZeroDivisionError:
            raise FormulaError("#DIV/0!", "除数为 0")
        except (TypeError, ValueError) as error:
            raise FormulaError("#VALUE!", str(error)) from error


def _rebuild(values: list[Any], *sources: Any) -> Array:
    """按第一个数组源的形状重建数组结果（保持二维结构，VLOOKUP/INDEX 才有意义）。"""
    for source in sources:
        if isinstance(source, Array):
            return Array(values, rows=source.rows, cols=source.cols)
    return Array(values, rows=1, cols=len(values))


def _flatten_values(arguments: Sequence[Any]) -> list[Any]:
    values: list[Any] = []
    for argument in arguments:
        if isinstance(argument, Array):
            values.extend(argument)
        elif isinstance(argument, (list, tuple)):
            values.extend(argument)
        else:
            values.append(argument)
    return values


def _numbers(arguments: Sequence[Any]) -> list[float]:
    result: list[float] = []
    for value in _flatten_values(arguments):
        if isinstance(value, str) and value.strip() == "":
            continue
        if isinstance(value, bool):
            result.append(1.0 if value else 0.0)
            continue
        try:
            result.append(to_number(value))
        except FormulaError:
            continue
    return result


def _matcher(criteria: Any) -> Callable[[Any], bool]:
    text = to_text(criteria).strip()
    match = re.match(r"^(<=|>=|<>|=|<|>)\s*(.*)$", text)
    operator, target = (match.group(1), match.group(2)) if match else ("=", text)
    number: Optional[float] = None
    try:
        number = float(target.replace(",", ""))
    except ValueError:
        number = None

    def predicate(value: Any) -> bool:
        if number is not None:
            try:
                current = to_number(value)
            except FormulaError:
                return False
            return {
                "=": current == number, "<>": current != number, "<": current < number,
                "<=": current <= number, ">": current > number, ">=": current >= number,
            }[operator]
        current_text = to_text(value)
        if operator == "=":
            return current_text == target
        if operator == "<>":
            return current_text != target
        return target in current_text

    return predicate


# ---------------------------------------------------------------- 函数表


def _fn_sum(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    return float(sum(_numbers(args)))


def _fn_average(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    values = _numbers(args)
    if not values:
        raise FormulaError("#DIV/0!", "没有可平均的数值")
    return sum(values) / len(values)


def _fn_count(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    return len(_numbers(args))


def _fn_counta(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    return len([item for item in _flatten_values(args) if to_text(item).strip() != ""])


def _fn_min(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    values = _numbers(args)
    return min(values) if values else 0.0


def _fn_max(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    values = _numbers(args)
    return max(values) if values else 0.0


def _fn_product(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    values = _numbers(args) or [0.0]
    result = 1.0
    for value in values:
        result *= value
    return result


def _fn_median(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    values = sorted(_numbers(args))
    if not values:
        raise FormulaError("#VALUE!", "没有可计算的中位数")
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _fn_round(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    value = to_number(args[0])
    digits = int(to_number(args[1])) if len(args) > 1 else 0
    return round(value, digits)


def _fn_roundup(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    digits = int(to_number(args[1])) if len(args) > 1 else 0
    factor = 10 ** digits
    value = to_number(args[0]) * factor
    return (math.ceil(value) if value >= 0 else math.floor(value)) / factor


def _fn_rounddown(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    digits = int(to_number(args[1])) if len(args) > 1 else 0
    factor = 10 ** digits
    value = to_number(args[0]) * factor
    return (math.floor(value) if value >= 0 else math.ceil(value)) / factor


def _fn_if(args: Sequence[Any], engine: FormulaEngine) -> Any:  # noqa: ARG001
    if len(args) < 2:
        raise FormulaError("#VALUE!", "IF 需要至少两个参数")
    condition = args[0]
    if isinstance(condition, Array):
        if len(condition) != 1:
            raise FormulaError("#VALUE!", "IF 的条件不能是区域（请用单个单元格）")
        condition = condition.single()
    return args[1] if _truthy(condition) else (args[2] if len(args) > 2 else False)


def _fn_iferror(args: Sequence[Any], engine: FormulaEngine) -> Any:  # noqa: ARG001
    value = args[0] if args else ""
    if isinstance(value, str) and value.startswith("#"):
        return args[1] if len(args) > 1 else ""
    return value


def _fn_and(args: Sequence[Any], engine: FormulaEngine) -> bool:  # noqa: ARG001
    return all(_truthy(item) for item in _flatten_values(args))


def _fn_or(args: Sequence[Any], engine: FormulaEngine) -> bool:  # noqa: ARG001
    return any(_truthy(item) for item in _flatten_values(args))


def _fn_not(args: Sequence[Any], engine: FormulaEngine) -> bool:  # noqa: ARG001
    return not _truthy(args[0] if args else False)


def _fn_concat(args: Sequence[Any], engine: FormulaEngine) -> str:  # noqa: ARG001
    return "".join(to_text(item) for item in _flatten_values(args))


def _fn_left(args: Sequence[Any], engine: FormulaEngine) -> str:  # noqa: ARG001
    count = int(to_number(args[1])) if len(args) > 1 else 1
    return to_text(args[0])[:count]


def _fn_right(args: Sequence[Any], engine: FormulaEngine) -> str:  # noqa: ARG001
    count = int(to_number(args[1])) if len(args) > 1 else 1
    return to_text(args[0])[-count:] if count else ""


def _fn_mid(args: Sequence[Any], engine: FormulaEngine) -> str:  # noqa: ARG001
    start = int(to_number(args[1]))
    count = int(to_number(args[2]))
    return to_text(args[0])[max(0, start - 1):max(0, start - 1) + count]


def _fn_len(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    return len(to_text(args[0]))


def _fn_trim(args: Sequence[Any], engine: FormulaEngine) -> str:  # noqa: ARG001
    return re.sub(r"\s+", " ", to_text(args[0])).strip()


def _fn_upper(args: Sequence[Any], engine: FormulaEngine) -> str:  # noqa: ARG001
    return to_text(args[0]).upper()


def _fn_lower(args: Sequence[Any], engine: FormulaEngine) -> str:  # noqa: ARG001
    return to_text(args[0]).lower()


def _fn_substitute(args: Sequence[Any], engine: FormulaEngine) -> str:  # noqa: ARG001
    return to_text(args[0]).replace(to_text(args[1]), to_text(args[2]))


def _fn_today(args: Sequence[Any], engine: FormulaEngine) -> str:  # noqa: ARG001, ARG002
    return dt.date.today().isoformat()


def _fn_now(args: Sequence[Any], engine: FormulaEngine) -> str:  # noqa: ARG001, ARG002
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_date(value: Any) -> Optional[dt.date]:
    text = to_text(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y.%m.%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _fn_year(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    date = _parse_date(args[0])
    if date is None:
        raise FormulaError("#VALUE!", "不是日期")
    return date.year


def _fn_month(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    date = _parse_date(args[0])
    if date is None:
        raise FormulaError("#VALUE!", "不是日期")
    return date.month


def _fn_day(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    date = _parse_date(args[0])
    if date is None:
        raise FormulaError("#VALUE!", "不是日期")
    return date.day


def _fn_weekday(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    date = _parse_date(args[0])
    if date is None:
        raise FormulaError("#VALUE!", "不是日期")
    return date.isoweekday()


def _fn_days(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    start, end = _parse_date(args[0]), _parse_date(args[1])
    if start is None or end is None:
        raise FormulaError("#VALUE!", "不是日期")
    return (end - start).days


def _fn_countif(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    values = _flatten_values([args[0]])
    predicate = _matcher(args[1] if len(args) > 1 else "")
    return len([item for item in values if predicate(item)])


def _fn_sumif(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    values = _flatten_values([args[0]])
    predicate = _matcher(args[1] if len(args) > 1 else "")
    targets = _flatten_values([args[2]]) if len(args) > 2 else values
    total = 0.0
    for index, item in enumerate(values):
        if predicate(item):
            try:
                total += to_number(targets[index] if index < len(targets) else 0)
            except FormulaError:
                continue
    return total


def _fn_averageif(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    values = _flatten_values([args[0]])
    predicate = _matcher(args[1] if len(args) > 1 else "")
    targets = _flatten_values([args[2]]) if len(args) > 2 else values
    picked: list[float] = []
    for index, item in enumerate(values):
        if predicate(item):
            try:
                picked.append(to_number(targets[index] if index < len(targets) else 0))
            except FormulaError:
                continue
    if not picked:
        raise FormulaError("#DIV/0!", "没有匹配的数值")
    return sum(picked) / len(picked)


def _fn_vlookup(args: Sequence[Any], engine: FormulaEngine) -> Any:  # noqa: ARG001
    if len(args) < 3:
        raise FormulaError("#VALUE!", "VLOOKUP 需要三个参数")
    needle = args[0].single() if isinstance(args[0], Array) else args[0]
    table = args[1]
    if not isinstance(table, Array):
        raise FormulaError("#VALUE!", "VLOOKUP 的第二个参数必须是区域")
    index = int(to_number(args[2]))
    exact = _truthy(args[3]) if len(args) > 3 else True
    matrix = table.matrix
    if index < 1 or index > (len(matrix[0]) if matrix else 0):
        raise FormulaError("#REF!", "VLOOKUP 的列号超出区域")
    for row in matrix:
        if not row:
            continue
        left = to_text(row[0])
        if exact and left == to_text(needle):
            return row[index - 1]
        if not exact and to_text(needle) in left:
            return row[index - 1]
    raise FormulaError("#N/A", f"找不到：{to_text(needle)}")


def _fn_index(args: Sequence[Any], engine: FormulaEngine) -> Any:  # noqa: ARG001
    table = args[0]
    if not isinstance(table, Array):
        raise FormulaError("#VALUE!", "INDEX 的第一个参数必须是区域")
    row = int(to_number(args[1])) if len(args) > 1 else 1
    column = int(to_number(args[2])) if len(args) > 2 else 1
    matrix = table.matrix
    if row < 1 or row > len(matrix):
        raise FormulaError("#REF!", "INDEX 的行号超出区域")
    line = matrix[row - 1]
    if column < 1 or column > len(line):
        raise FormulaError("#REF!", "INDEX 的列号超出区域")
    return line[column - 1]


def _fn_match(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    needle = args[0].single() if isinstance(args[0], Array) else args[0]
    values = _flatten_values([args[1]])
    mode = int(to_number(args[2])) if len(args) > 2 else 1
    for offset, item in enumerate(values):
        if to_text(item) == to_text(needle):
            return offset + 1
    if mode == 0:
        raise FormulaError("#N/A", f"找不到：{to_text(needle)}")
    raise FormulaError("#N/A", f"找不到近似值：{to_text(needle)}")


def _fn_int(args: Sequence[Any], engine: FormulaEngine) -> int:  # noqa: ARG001
    return math.floor(to_number(args[0]))


def _fn_abs(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    return abs(to_number(args[0]))


def _fn_mod(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    divisor = to_number(args[1])
    if divisor == 0:
        raise FormulaError("#DIV/0!", "除数为 0")
    return math.fmod(to_number(args[0]), divisor)


def _fn_power(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    return to_number(args[0]) ** to_number(args[1])


def _fn_sqrt(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    value = to_number(args[0])
    if value < 0:
        raise FormulaError("#VALUE!", "负数不能开平方")
    return math.sqrt(value)


def _fn_ceiling(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    significance = to_number(args[1]) if len(args) > 1 else 1.0
    if significance == 0:
        return 0.0
    return math.ceil(to_number(args[0]) / significance) * significance


def _fn_floor(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    significance = to_number(args[1]) if len(args) > 1 else 1.0
    if significance == 0:
        return 0.0
    return math.floor(to_number(args[0]) / significance) * significance


def _fn_stdev(args: Sequence[Any], engine: FormulaEngine) -> float:  # noqa: ARG001
    values = _numbers(args)
    if len(values) < 2:
        raise FormulaError("#DIV/0!", "样本太少")
    mean = sum(values) / len(values)
    variance = sum((item - mean) ** 2 for item in values) / (len(values) - 1)
    return math.sqrt(variance)


FUNCTIONS: dict[str, Callable[[Sequence[Any], FormulaEngine], Any]] = {
    "SUM": _fn_sum,
    "AVERAGE": _fn_average,
    "AVG": _fn_average,
    "COUNT": _fn_count,
    "COUNTA": _fn_counta,
    "MIN": _fn_min,
    "MAX": _fn_max,
    "PRODUCT": _fn_product,
    "MEDIAN": _fn_median,
    "ROUND": _fn_round,
    "ROUNDUP": _fn_roundup,
    "ROUNDDOWN": _fn_rounddown,
    "IF": _fn_if,
    "IFERROR": _fn_iferror,
    "AND": _fn_and,
    "OR": _fn_or,
    "NOT": _fn_not,
    "CONCAT": _fn_concat,
    "CONCATENATE": _fn_concat,
    "LEFT": _fn_left,
    "RIGHT": _fn_right,
    "MID": _fn_mid,
    "LEN": _fn_len,
    "TRIM": _fn_trim,
    "UPPER": _fn_upper,
    "LOWER": _fn_lower,
    "SUBSTITUTE": _fn_substitute,
    "TODAY": _fn_today,
    "NOW": _fn_now,
    "YEAR": _fn_year,
    "MONTH": _fn_month,
    "DAY": _fn_day,
    "WEEKDAY": _fn_weekday,
    "DAYS": _fn_days,
    "COUNTIF": _fn_countif,
    "SUMIF": _fn_sumif,
    "AVERAGEIF": _fn_averageif,
    "VLOOKUP": _fn_vlookup,
    "INDEX": _fn_index,
    "MATCH": _fn_match,
    "INT": _fn_int,
    "ABS": _fn_abs,
    "MOD": _fn_mod,
    "POWER": _fn_power,
    "SQRT": _fn_sqrt,
    "CEILING": _fn_ceiling,
    "FLOOR": _fn_floor,
    "STDEV": _fn_stdev,
}

FUNCTION_NAMES: tuple[str, ...] = tuple(sorted(FUNCTIONS))


def evaluate_formula(formula: str, table: TableData | None = None, *,
                     sheet_name: str = "", engine: FormulaEngine | None = None,
                     tables: Sequence[TableData] | None = None) -> Any:
    """求值一个公式（便捷入口；可传多张表用于跨表引用）。"""
    if engine is None:
        pool = list(tables or [])
        if table is not None:
            pool.insert(0, table)
        engine = FormulaEngine.from_tables(pool)
    return engine.evaluate(formula, sheet=sheet_name or (engine.sheet_names() or [""])[0])


# ---------------------------------------------------------------- 循环引用检查


def check_formulas(tables: Sequence[TableData]) -> list[dict]:
    """计算检查：错误值、循环引用、引用越界、非法函数名。"""
    engine = FormulaEngine.from_tables(tables)
    issues: list[dict] = []
    for sheet, rows in engine.sheets.items():
        for row_index, row in enumerate(rows):
            for column_index, text in enumerate(row):
                if not isinstance(text, str) or not text.startswith("="):
                    continue
                address = f"{sheet}!{column_letters(column_index)}{row_index + 1}"
                try:
                    engine.value(sheet, row_index, column_index)
                except FormulaError as error:
                    issues.append({
                        "cell": address,
                        "formula": text,
                        "code": error.code,
                        "detail": error.detail or str(error),
                    })
                except Exception as error:  # noqa: BLE001
                    issues.append({
                        "cell": address, "formula": text,
                        "code": "#VALUE!", "detail": str(error),
                    })
    return issues


# ---------------------------------------------------------------- 自动填充

WEEKDAYS_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
WEEKDAY_ALIASES = {name: index for index, name in enumerate(WEEKDAYS_CN)}
WEEKDAY_ALIASES.update({f"星期{name[-1]}": index for index, name in enumerate(WEEKDAYS_CN)})
WEEKDAY_ALIASES.update({"星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3,
                        "星期五": 4, "星期六": 5, "星期天": 6, "星期日": 6})
MONTHS_CN = tuple(f"{index}月" for index in range(1, 13))
CN_NUMERALS = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
               "十一", "十二", "十三", "十四", "十五")
STEMS = tuple("甲乙丙丁戊己庚辛壬癸")
QUARTERS = ("第一季度", "第二季度", "第三季度", "第四季度")
SEASONS = ("春季", "夏季", "秋季", "冬季")

#: 内置自定义列表（用户可在界面上补充自己的）
CUSTOM_LISTS: dict[str, tuple[str, ...]] = {
    "星期": WEEKDAYS_CN + ("周一",),
    "月份": MONTHS_CN,
    "中文数字": CN_NUMERALS,
    "天干": STEMS,
    "季度": QUARTERS,
    "季节": SEASONS,
}

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m", "%Y/%m", "%Y年%m月", "%m月")
_TEXT_NUMBER_RE = re.compile(r"^(?P<prefix>.*?)(?P<number>\d+)(?P<suffix>\D*)$")


@dataclass
class SeriesSpec:
    """自动填充的规律描述。"""

    kind: str                      # number/date/weekday/month/weekday_name/custom/text_number/formula/copy
    label: str = ""
    step: float = 1.0
    unit: str = "day"              # day / workday / month
    items: tuple[str, ...] = ()
    date_format: str = "%Y-%m-%d"
    start: Optional[dt.date] = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "label": self.label, "step": self.step, "unit": self.unit,
            "items": list(self.items), "date_format": self.date_format,
            "start": self.start.isoformat() if self.start else "",
            "note": self.note,
        }


def parse_date(value: str) -> Optional[tuple[dt.date, str]]:
    """尽量解析日期，返回 (日期, 原始格式)。"""
    text = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%m月":
            date = dt.date(dt.date.today().year, parsed.month, 1)
        elif fmt in ("%Y-%m", "%Y/%m", "%Y年%m月"):
            date = dt.date(parsed.year, parsed.month, 1)
        else:
            date = parsed.date()
        return date, fmt
    return None


def format_date(date: dt.date, fmt: str) -> str:
    if fmt == "%m月":
        return f"{date.month}月"
    if fmt in ("%Y-%m", "%Y/%m", "%Y年%m月"):
        return date.strftime(fmt)
    return date.strftime(fmt)


def detect_series(values: Sequence[str]) -> Optional[SeriesSpec]:
    """识别填充规律（识别不出时返回 None，界面回退成"复制"）。"""
    items = [str(value) for value in values if str(value).strip() != ""]
    if not items:
        return None
    if len(items) == 1:
        single = items[0].strip()
        if single.startswith("="):
            return SeriesSpec("formula", label="公式填充", note="相对引用按行/列自动平移")
        # 单个值也要能认出"星期/月份/自定义列表/日期"，否则往下拉只能得到一片重复值
        if single in WEEKDAY_ALIASES:
            return SeriesSpec("weekday_name", label="星期序列", items=WEEKDAYS_CN, step=1)
        for name, custom in CUSTOM_LISTS.items():
            if single in custom:
                return SeriesSpec("custom", label=f"自定义列表：{name}", items=custom, step=1)
        parsed_single = parse_date(single)
        if parsed_single:
            return SeriesSpec("date", label="按天递增", unit="day", step=1,
                              date_format=parsed_single[1], start=parsed_single[0])
        return SeriesSpec("copy", label="复制填充", note="单个值重复填充")

    # 1) 等差数列
    try:
        numbers = [float(item.replace(",", "")) for item in items]
    except ValueError:
        numbers = []
    if len(numbers) >= 2:
        steps = {round(numbers[index] - numbers[index - 1], 10) for index in range(1, len(numbers))}
        if len(steps) == 1:
            return SeriesSpec("number", label=f"等差数列（步长 {numbers[1] - numbers[0]:g}）",
                              step=numbers[1] - numbers[0])
        ratios = {round(numbers[index] / numbers[index - 1], 10)
                  for index in range(1, len(numbers)) if numbers[index - 1]}
        if len(ratios) == 1 and ratios != {1.0}:
            return SeriesSpec("number", label=f"等比数列（倍数 {ratios.pop():g}）",
                              step=numbers[1] / numbers[0] if numbers[0] else 1.0)

    # 2) 日期 / 月份 / 工作日
    parsed = [parse_date(item) for item in items]
    if all(parsed):
        dates = [item[0] for item in parsed]        # type: ignore[index]
        fmt = parsed[0][1]                          # type: ignore[index]
        if _is_workday_series(dates):
            return SeriesSpec("date", label="工作日（跳过周末）", unit="workday",
                              date_format=fmt, start=dates[-1])
        step = (dates[1] - dates[0]).days if len(dates) > 1 else 1
        if fmt in ("%Y-%m", "%Y/%m", "%Y年%m月", "%m月"):
            return SeriesSpec("date", label="按月递增", unit="month", step=1,
                              date_format=fmt, start=dates[-1])
        return SeriesSpec("date", label=f"日期（每 {step} 天）", unit="day", step=step,
                          date_format=fmt, start=dates[-1])

    # 3) 星期 / 月份名 / 自定义列表
    if all(item in WEEKDAY_ALIASES for item in items):
        return SeriesSpec("weekday_name", label="星期序列", items=WEEKDAYS_CN,
                          step=1, note="滚动填充（周日之后回到周一）")
    for name, custom in CUSTOM_LISTS.items():
        if all(item in custom for item in items):
            return SeriesSpec("custom", label=f"自定义列表：{name}", items=custom, step=1)

    # 4) 文本 + 序号（如"第1项"、"编号-001"）
    matches = [_TEXT_NUMBER_RE.match(item) for item in items]
    if all(matches):
        prefix = matches[0].group("prefix")             # type: ignore[union-attr]
        suffix = matches[0].group("suffix")             # type: ignore[union-attr]
        if all(match.group("prefix") == prefix and match.group("suffix") == suffix  # type: ignore[union-attr]
               for match in matches):
            numbers = [int(match.group("number")) for match in matches]            # type: ignore[union-attr]
            steps = {numbers[index] - numbers[index - 1] for index in range(1, len(numbers))}
            if len(steps) == 1:
                width = max(len(match.group("number")) for match in matches)       # type: ignore[union-attr]
                return SeriesSpec(
                    "text_number", label=f"文本 + 序号（步长 {steps.pop()}）", step=1,
                    items=(prefix, suffix, str(width)),
                )

    # 5) 公式
    if all(item.startswith("=") for item in items):
        return SeriesSpec("formula", label="公式填充", note="相对引用自动平移")
    return None


def fill_series(values: Sequence[str], count: int, *,
                spec: Optional[SeriesSpec] = None) -> list[str]:
    """按规律继续填充 `count` 个值（`spec` 为空时自动识别）。"""
    items = [str(value) for value in values]
    spec = spec or detect_series(items)
    if spec is None:
        spec = SeriesSpec("copy", label="复制填充")
    produced: list[str] = []
    trailing = items[-1] if items else ""

    if spec.kind == "copy":
        return [trailing for _ in range(count)]

    if spec.kind == "formula":
        formulas = [item for item in items if item.startswith("=")]
        base = formulas[-1] if formulas else trailing
        # 公式平移的步长：取最后两行的行差（自动填充是竖着往下拉）
        step = 1
        if len(formulas) >= 2:
            step = max(1, len(formulas) - 1)
        for index in range(1, count + 1):
            produced.append(translate_formula(base, step * index, 0, keep_absolute=True))
        return produced

    if spec.kind == "number":
        try:
            last = float(items[-1].replace(",", ""))
        except ValueError:
            last = 0.0
        for index in range(1, count + 1):
            value = last + spec.step * index
            produced.append(_number_text(value))
        return produced

    if spec.kind == "date":
        start = spec.start or (parse_date(items[-1]) or (dt.date.today(), ""))[0]   # type: ignore[index]
        current = start
        for _ in range(count):
            current = _next_date(current, spec)
            produced.append(format_date(current, spec.date_format))
        return produced

    if spec.kind == "weekday_name":
        items_list = list(spec.items or WEEKDAYS_CN)
        last = items[-1]
        index = items_list.index(last) if last in items_list else -1
        for step in range(1, count + 1):
            produced.append(items_list[(index + int(spec.step) * step) % len(items_list)])
        return produced

    if spec.kind == "custom":
        items_list = list(spec.items)
        last = items[-1]
        index = items_list.index(last) if last in items_list else -1
        for step in range(1, count + 1):
            position = index + int(spec.step) * step
            produced.append(items_list[position] if 0 <= position < len(items_list)
                            else items_list[position % len(items_list)])
        return produced

    if spec.kind == "text_number":
        prefix, suffix, width = (list(spec.items) + ["", "", "1"])[:3]
        try:
            last = int(_TEXT_NUMBER_RE.match(items[-1]).group("number"))     # type: ignore[union-attr]
        except (AttributeError, ValueError):
            last = 0
        for index in range(1, count + 1):
            produced.append(f"{prefix}{str(last + index).zfill(int(width))}{suffix}")
        return produced

    return [trailing for _ in range(count)]


def _is_workday_series(dates: Sequence[dt.date]) -> bool:
    """样本是否体现"跳过周末"（周五 → 周一 这类），据此推断工作日序列。

    只有连续工作日（周四→周五）时**不**判为工作日序列 ——
    否则用户想按自然日填充会被莫名跳过周末。
    """
    if len(dates) < 2:
        return False
    spans_weekend = False
    for previous, current in zip(dates, dates[1:]):
        gap = (current - previous).days
        if gap < 1:
            return False
        if previous.weekday() >= 5 or current.weekday() >= 5:
            return False
        for offset in range(1, gap):
            if (previous + dt.timedelta(days=offset)).weekday() < 5:
                return False
        if gap > 1:
            spans_weekend = True
    return spans_weekend


def _number_text(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:g}"


def _next_date(current: dt.date, spec: SeriesSpec) -> dt.date:
    if spec.unit == "month":
        month = current.month + 1
        year = current.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(current.day, _days_in_month(year, month))
        return dt.date(year, month, day)
    step = max(1, int(spec.step or 1))
    if spec.unit == "workday":
        candidate = current
        added = 0
        while added < step:
            candidate += dt.timedelta(days=1)
            if candidate.weekday() < 5:
                added += 1
        return candidate
    return current + dt.timedelta(days=step)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (dt.date(year, month + 1, 1) - dt.timedelta(days=1)).day


def autofill(values: Sequence[str], count: int, *,
             custom_list: Sequence[str] | None = None) -> tuple[list[str], SeriesSpec]:
    """自动填充入口：返回（填充结果, 识别到的规律）。"""
    spec = detect_series(values)
    if spec is None and custom_list:
        spec = SeriesSpec("custom", label="自定义列表", items=tuple(custom_list), step=1)
    if spec is None:
        spec = SeriesSpec("copy", label="复制填充")
    return fill_series(values, count, spec=spec), spec


# ---------------------------------------------------------------- 公式平移


def translate_formula(formula: str, delta_row: int, delta_column: int, *,
                      keep_absolute: bool = True) -> str:
    """相对引用平移（自动填充/插入行列时用）；`$` 锁定的部分不动。"""
    if not formula or not formula.startswith("="):
        return formula
    body = formula[1:]

    def replace(match: re.Match) -> str:
        sheet = match.group("sheet") or ""
        column = match.group("col")
        row = match.group("row")
        # 字符串常量里的内容不能改
        absolute_column = column.startswith("$")
        absolute_row = row.startswith("$")
        new_column = column
        new_row = row
        if not (keep_absolute and absolute_column):
            new_column = ("$" if absolute_column else "") + column_letters(
                max(0, column_index(column.lstrip("$")) + delta_column))
        if not (keep_absolute and absolute_row):
            new_row = ("$" if absolute_row else "") + str(
                max(1, int(row.lstrip("$")) + delta_row))
        return f"{sheet}{new_column}{new_row}"

    pattern = re.compile(
        r"(?P<sheet>(?:'[^']+'|[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*)!)?"
        r"(?P<col>\$?[A-Za-z]{1,3})(?P<row>\$?\d+)"
    )
    pieces: list[str] = []
    position = 0
    for match in re.finditer(r'"(?:[^"]|"")*"', body):
        segment = body[position:match.start()]
        pieces.append(pattern.sub(replace, segment))
        pieces.append(match.group(0))
        position = match.end()
    pieces.append(pattern.sub(replace, body[position:]))
    return "=" + "".join(pieces)


# ---------------------------------------------------------------- 智能填充


@dataclass
class PatternSpec:
    """智能填充识别出的规则。"""

    kind: str
    label: str
    params: dict = field(default_factory=dict)
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "params": dict(self.params),
                "confidence": self.confidence}


_SPLIT_SEPARATORS = (" ", "-", "_", "/", "|", "·", ",", "，", "、")
_EXTRACT_PATTERNS = {
    "digits": re.compile(r"\d+"),
    "phone": re.compile(r"1[3-9]\d{9}"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "id_card": re.compile(r"\d{17}[\dXx]"),
    "amount": re.compile(r"\d+(?:\.\d+)?"),
    "date": re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?"),
    "url": re.compile(r"https?://[^\s]+"),
}
_EXTRACT_LABELS = {
    "digits": "提取数字",
    "phone": "提取手机号",
    "email": "提取邮箱",
    "id_card": "提取身份证号",
    "amount": "提取金额",
    "date": "提取日期",
    "url": "提取网址",
}


def infer_pattern(inputs: Sequence[str], outputs: Sequence[str]) -> Optional[PatternSpec]:
    """按示例反推规则：`inputs[i]` 应变成 `outputs[i]`。"""
    pairs = [(str(a), str(b)) for a, b in zip(inputs, outputs)]
    if not pairs or len(pairs) != len(outputs):
        return None

    # 1) 原样复制
    if all(a == b for a, b in pairs):
        return PatternSpec("copy", "原样复制")

    # 2) 分隔符切分（拆分姓名/编码）
    for separator in _SPLIT_SEPARATORS:
        for index in (0, 1):
            if all(separator in a and a.split(separator)[index].strip() == b.strip() for a, b in pairs):
                return PatternSpec("split", f"按「{separator}」取第 {index + 1} 段",
                                   {"separator": separator, "index": index})
    # 中文姓名：姓（首字）+ 名（其余）
    if all(len(a) in (2, 3, 4) and a[0] == b and len(b) == 1
           and all("\u4e00" <= char <= "\u9fff" for char in a) for a, b in pairs):
        return PatternSpec("cn_surname", "提取姓氏", {})
    if all(len(a) in (2, 3, 4) and a[1:] == b and all("\u4e00" <= char <= "\u9fff" for char in a)
           for a, b in pairs):
        return PatternSpec("cn_given", "提取名字", {})

    # 3) 提取结构（手机号/邮箱/身份证/数字/日期/网址）
    for kind, pattern in _EXTRACT_PATTERNS.items():
        if all((pattern.search(a).group(0) if pattern.search(a) else None) == b for a, b in pairs):
            return PatternSpec("extract", _EXTRACT_LABELS[kind], {"pattern": kind})

    # 4) 去前缀 / 去后缀
    prefix = _common_affix([a for a, _b in pairs], [b for _a, b in pairs], suffix=False)
    if prefix and all(a.startswith(prefix) and a[len(prefix):] == b for a, b in pairs):
        return PatternSpec("remove_prefix", f"去掉前缀「{prefix}」", {"text": prefix})
    suffix = _common_affix([a for a, _b in pairs], [b for _a, b in pairs], suffix=True)
    if suffix and all(a.endswith(suffix) and a[: -len(suffix)] == b for a, b in pairs):
        return PatternSpec("remove_suffix", f"去掉后缀「{suffix}」", {"text": suffix})

    # 5) 加前后缀
    added_prefix = _common_affix([b for _a, b in pairs], [a for a, _b in pairs], suffix=False)
    if added_prefix and all(b.startswith(added_prefix) and b[len(added_prefix):] == a for a, b in pairs):
        return PatternSpec("add_prefix", f"加上前缀「{added_prefix}」", {"text": added_prefix})
    added_suffix = _common_affix([b for _a, b in pairs], [a for a, _b in pairs], suffix=True)
    if added_suffix and all(b.endswith(added_suffix) and b[: -len(added_suffix)] == a for a, b in pairs):
        return PatternSpec("add_suffix", f"加上后缀「{added_suffix}」", {"text": added_suffix})

    # 6) 大小写
    if all(a.upper() == b for a, b in pairs):
        return PatternSpec("upper", "转成大写")
    if all(a.lower() == b for a, b in pairs):
        return PatternSpec("lower", "转成小写")

    # 7) 编号补零（1 → 0001）
    padded = True
    width = 0
    for a, b in pairs:
        try:
            numeric = int(b)
        except ValueError:
            padded = False
            break
        if a.strip() != str(numeric) or not b.startswith("0"):
            padded = False
            break
        width = max(width, len(b))
    if padded and width:
        return PatternSpec("pad", f"编号补零到 {width} 位", {"width": width})

    # 8) 固定替换
    replacements = {(a, b) for a, b in pairs if a != b}
    if len({a for a, _b in replacements}) == 1 and len({b for _a, b in replacements}) == 1:
        old, new = replacements.pop()
        return PatternSpec("replace", f"把「{old}」替换为「{new}」", {"old": old, "new": new})

    # 9) 按关键词分类（示例：输出是少量标签，输入里含标签关键词）
    classify = _infer_classify_rules(pairs)
    if classify:
        return PatternSpec("classify", f"按关键词归类（{len(classify)} 条规则）",
                           {"rules": classify})
    return None


def _common_affix(long_values: Sequence[str], short_values: Sequence[str], *,
                  suffix: bool) -> str:
    """求"长串相对短串"多出来的公共前缀/后缀。"""
    extras: list[str] = []
    for long_value, short_value in zip(long_values, short_values):
        if suffix:
            if not long_value.endswith(short_value):
                return ""
            extras.append(long_value[: len(long_value) - len(short_value)])
        else:
            if not long_value.startswith(short_value):
                return ""
            extras.append(long_value[len(short_value):])
    if not extras or not extras[0]:
        return ""
    common = extras[0]
    for extra in extras[1:]:
        while common and not (extra.endswith(common) if suffix else extra.startswith(common)):
            common = common[:-1] if suffix else common[1:]
        if not common:
            return ""
    return common


def _infer_classify_rules(pairs: Sequence[tuple[str, str]]) -> list[dict]:
    """从示例里猜"哪个关键词 → 哪个分类"。"""
    labels = {b for _a, b in pairs}
    if len(labels) > max(2, len(pairs) // 2) or len(labels) == len(pairs):
        return []
    rules: list[dict] = []
    for label in labels:
        sources = [a for a, b in pairs if b == label]
        if not sources:
            continue
        # 分类名本身出现在源文本里（"美的空调" → 空调）是最可靠的关键词
        if label and all(label in source for source in sources):
            rules.append({"keyword": label, "label": label})
            continue
        # 否则取所有 source 的最长公共子串（长度 ≥ 2）
        candidates = _common_substrings(sources)
        keyword = next((item for item in candidates if len(item) >= 2), "")
        if not keyword:
            return []
        rules.append({"keyword": keyword, "label": label})
    return rules


def _common_substrings(values: Sequence[str]) -> list[str]:
    if not values:
        return []
    shortest = min(values, key=len)
    found: list[str] = []
    for length in range(len(shortest), 1, -1):
        for start in range(0, len(shortest) - length + 1):
            piece = shortest[start:start + length]
            if all(piece in value for value in values):
                found.append(piece)
        if found:
            break
    return found


def apply_pattern(spec: PatternSpec, value: str) -> str:
    """把识别出的规则作用到新值上。"""
    text = str(value)
    params = spec.params
    if spec.kind == "copy":
        return text
    if spec.kind == "split":
        separator, index = params.get("separator", " "), int(params.get("index", 0))
        parts = text.split(separator)
        return parts[index].strip() if len(parts) > index else ""
    if spec.kind == "cn_surname":
        return text[0] if text else ""
    if spec.kind == "cn_given":
        return text[1:] if len(text) > 1 else ""
    if spec.kind == "extract":
        pattern = _EXTRACT_PATTERNS.get(params.get("pattern", "digits"))
        match = pattern.search(text) if pattern else None
        return match.group(0) if match else ""
    if spec.kind == "remove_prefix":
        prefix = params.get("text", "")
        return text[len(prefix):] if text.startswith(prefix) else text
    if spec.kind == "remove_suffix":
        suffix = params.get("text", "")
        return text[: -len(suffix)] if suffix and text.endswith(suffix) else text
    if spec.kind == "add_prefix":
        return params.get("text", "") + text
    if spec.kind == "add_suffix":
        return text + params.get("text", "")
    if spec.kind == "upper":
        return text.upper()
    if spec.kind == "lower":
        return text.lower()
    if spec.kind == "pad":
        width = int(params.get("width", 4))
        digits = re.search(r"\d+", text)
        if not digits:
            return text
        return text[:digits.start()] + digits.group(0).zfill(width) + text[digits.end():]
    if spec.kind == "replace":
        return text.replace(params.get("old", ""), params.get("new", ""))
    if spec.kind == "classify":
        best = ""
        best_length = 0
        for rule in params.get("rules", []):
            keyword = str(rule.get("keyword") or "")
            if keyword and keyword in text and len(keyword) > best_length:
                best, best_length = str(rule.get("label") or ""), len(keyword)
        return best
    return text


def smart_fill(new_values: Sequence[str], specs: Sequence[PatternSpec | None]) -> list[list[str]]:
    """多列智能填充：返回每列的结果（规则为 None 的列返回空串）。"""
    columns: list[list[str]] = []
    for spec in specs:
        if spec is None:
            columns.append(["" for _ in new_values])
        else:
            columns.append([apply_pattern(spec, value) for value in new_values])
    return columns


def infer_patterns(inputs: Sequence[str], output_columns: Sequence[Sequence[str]]) -> list[PatternSpec | None]:
    """对每一列示例输出分别反推规则（列数 = 需要产出的字段数）。"""
    return [infer_pattern(inputs, column) for column in output_columns]


# ---------------------------------------------------------------- 统计


def sheet_name_of(table: TableData, position: int = 0) -> str:
    return table.name or f"Sheet{position + 1}"


def _resolve_value(engine: FormulaEngine, sheet: str, row: int, column: int) -> Any:
    try:
        return engine.value(sheet, row, column)
    except FormulaError as error:
        return error.code


def resolved_matrix(table: TableData, engine: FormulaEngine | None = None) -> list[list[Any]]:
    """把表格解析成"值矩阵"（公式先求值，出错给错误码）。

    统计/图表必须走这里：表格里最常见的就是 `=B2*C2` 这类公式列，
    直接读原文会得到一堆 0。
    """
    engine = engine or FormulaEngine.from_tables([table])
    sheet = (engine.sheet_names() or [sheet_name_of(table)])[0]
    source = engine.sheets.get(sheet) or table.normalized()
    return [
        [_resolve_value(engine, sheet, row, column) for column in range(len(line))]
        for row, line in enumerate(source)
    ]


def _data_rows(table: TableData, engine: FormulaEngine | None = None) -> tuple[list[str], list[list[Any]]]:
    """(表头, 数据行) —— 数据行里的公式已求值。"""
    matrix = resolved_matrix(table, engine)
    header = table.header_row() if table.header else []
    if not header:
        header = [f"列{index + 1}" for index in range(table.width)]
    start = 1 if (table.header and len(matrix) > 1) else 0
    return header, matrix[start:]


def _column_position(header: Sequence[str], table: TableData, name: str) -> int:
    needle = (name or "").strip().lower()
    for index, cell in enumerate(header):
        if str(cell).strip().lower() == needle:
            return index
    return table.column_index(name)


def summarize_column(values: Sequence[Any]) -> dict:
    """单列统计：计数、求和、平均、最值、去重数。"""
    flattened = _flatten_values([list(values)])
    numbers = _numbers(flattened)
    texts = [to_text(value) for value in flattened if to_text(value).strip() != ""]
    return {
        "count": len(texts),
        "numeric": len(numbers),
        "sum": round(sum(numbers), 6) if numbers else 0.0,
        "average": round(sum(numbers) / len(numbers), 6) if numbers else 0.0,
        "min": min(numbers) if numbers else 0.0,
        "max": max(numbers) if numbers else 0.0,
        "distinct": len(set(texts)),
    }


def summarize_table(table: TableData, engine: FormulaEngine | None = None) -> list[dict]:
    """整表统计：每个表头一列（公式列会自动求值）。"""
    header, rows = _data_rows(table, engine)
    if not rows:
        return []
    width = max(len(row) for row in rows)
    reports: list[dict] = []
    for index in range(width):
        name = (str(header[index]).strip() if index < len(header) else "") or f"列{index + 1}"
        report = summarize_column([row[index] if index < len(row) else "" for row in rows])
        report["column"] = name
        reports.append(report)
    return reports


_AGGREGATES = {
    "sum": lambda items: round(sum(items), 6),
    "count": lambda items: len(items),
    "average": lambda items: round(sum(items) / len(items), 6) if items else 0.0,
    "max": lambda items: max(items) if items else 0.0,
    "min": lambda items: min(items) if items else 0.0,
}


def _aggregate(items: Sequence[float], how: str) -> float:
    function = _AGGREGATES.get(how)
    if function is None:
        raise ValueError(f"不支持的统计方式：{how}（可用：{'、'.join(_AGGREGATES)}）")
    return function(list(items))


def group_by(table: TableData, key_column: str, *, value_column: str = "",
             how: str = "count", engine: FormulaEngine | None = None) -> list[tuple[str, float]]:
    """分组统计：按 `key_column` 分组，对 `value_column` 做 sum/count/average/max/min。"""
    header, rows = _data_rows(table, engine)
    if not rows:
        return []
    key_index = _column_position(header, table, key_column)
    if key_index < 0:
        raise ValueError(f"找不到列：{key_column}")
    value_index = _column_position(header, table, value_column) if value_column else -1
    buckets: dict[str, list[float]] = {}
    for row in rows:
        key = to_text(row[key_index]) if key_index < len(row) else ""
        bucket = buckets.setdefault(key, [])
        if value_index >= 0:
            try:
                bucket.append(to_number(row[value_index] if value_index < len(row) else 0))
            except FormulaError:
                continue
        else:
            bucket.append(1.0)
    return [(key, _aggregate(values, how)) for key, values in buckets.items()]


def pivot(table: TableData, index_column: str, column_column: str, value_column: str, *,
          how: str = "sum", engine: FormulaEngine | None = None) -> TableData:
    """透视表：行 = index_column，列 = column_column 的不同取值。"""
    header, rows = _data_rows(table, engine)
    if not rows:
        return TableData(rows=[], name="透视表")
    index_at = _column_position(header, table, index_column)
    column_at = _column_position(header, table, column_column)
    value_at = _column_position(header, table, value_column)
    for position, name in ((index_at, index_column), (column_at, column_column),
                           (value_at, value_column)):
        if position < 0:
            raise ValueError(f"找不到列：{name}")
    columns: list[str] = []
    buckets: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        row_key = to_text(row[index_at]) if index_at < len(row) else ""
        column_key = to_text(row[column_at]) if column_at < len(row) else ""
        if column_key not in columns:
            columns.append(column_key)
        try:
            number = to_number(row[value_at] if value_at < len(row) else 0)
        except FormulaError:
            continue
        buckets.setdefault(row_key, {}).setdefault(column_key, []).append(number)
    output = [["项目", *columns, "合计"]]
    for row_key, values in buckets.items():
        line = [row_key]
        total = 0.0
        for column_key in columns:
            item = _aggregate(values.get(column_key, []), how) if values.get(column_key) else 0.0
            line.append(_number_text(item))
            total += float(item)
        line.append(_number_text(round(total, 6)))
        output.append(line)
    return TableData(rows=output, name=f"透视表·{value_column}", header=True,
                     meta={"index": index_column, "columns": column_column, "agg": how})


def chart_series(table: TableData, label_column: str, value_column: str, *,
                 kind: str = "bar", engine: FormulaEngine | None = None) -> dict:
    """图表数据（界面按 kind 画柱/线/饼；不依赖 QtCharts）。"""
    header, rows = _data_rows(table, engine)
    label_at = _column_position(header, table, label_column)
    value_at = _column_position(header, table, value_column)
    if label_at < 0 or value_at < 0:
        raise ValueError("找不到图表所需的列")
    labels: list[str] = []
    values: list[float] = []
    for row in rows:
        labels.append(to_text(row[label_at]) if label_at < len(row) else "")
        try:
            values.append(to_number(row[value_at] if value_at < len(row) else 0))
        except FormulaError:
            values.append(0.0)
    return {"kind": kind, "labels": labels, "values": values,
            "title": f"{value_column} 按 {label_column}"}


# ---------------------------------------------------------------- 排序与筛选

FILTER_MODES = (("contains", "包含"), ("equals", "等于"), ("gt", "大于"), ("lt", "小于"),
                ("not_empty", "非空"), ("empty", "为空"))


def _sort_key(value: Any) -> tuple[int, Any]:
    """排序键：数值优先按数值排，文本按字符排（避免 "10" 排在 "9" 前面）。"""
    text = to_text(value).strip()
    if text == "":
        return (2, "")
    try:
        return (0, float(text.replace(",", "").replace("%", "")))
    except ValueError:
        return (1, text)


def sort_table(table: TableData, column: str, *, descending: bool = False,
               engine: FormulaEngine | None = None) -> TableData:
    """按某一列排序（公式列先求值再比较；表头始终留在第一行）。"""
    header, rows = _data_rows(table, engine)
    position = _column_position(header, table, column)
    if position < 0:
        raise ValueError(f"找不到列：{column}")
    body = sorted(rows, key=lambda row: _sort_key(row[position] if position < len(row) else ""),
                  reverse=bool(descending))
    output = TableData(rows=[[str(cell) for cell in header]] + [
        [to_text(cell) for cell in row] for row in body],
        name=table.name, header=True, styles=dict(table.styles), meta=dict(table.meta))
    output.meta["sorted_by"] = f"{column}{'（降序）' if descending else '（升序）'}"
    return output


def filter_table(table: TableData, column: str, criterion: str = "", *,
                 mode: str = "contains", engine: FormulaEngine | None = None) -> TableData:
    """按条件筛选行（返回只含命中行的新表，表头保留）。"""
    header, rows = _data_rows(table, engine)
    position = _column_position(header, table, column)
    if position < 0:
        raise ValueError(f"找不到列：{column}")
    needle = (criterion or "").strip()

    def keep(row: Sequence[Any]) -> bool:
        value = row[position] if position < len(row) else ""
        text = to_text(value).strip()
        if mode == "contains":
            return needle.lower() in text.lower()
        if mode == "equals":
            return text == needle
        if mode == "not_empty":
            return text != ""
        if mode == "empty":
            return text == ""
        if mode in ("gt", "lt"):
            try:
                left = to_number(value)
                right = float(needle.replace(",", ""))
            except (FormulaError, ValueError):
                return False
            return left > right if mode == "gt" else left < right
        raise ValueError(f"不支持的筛选方式：{mode}")

    matched = [row for row in rows if keep(row)]
    output = TableData(rows=[[str(cell) for cell in header]] + [
        [to_text(cell) for cell in row] for row in matched],
        name=table.name, header=True, styles=dict(table.styles), meta=dict(table.meta))
    output.meta["filtered_by"] = f"{column} {mode} {needle}".strip()
    output.meta["filtered_out"] = len(rows) - len(matched)
    return output


def distinct_values(table: TableData, column: str, *, limit: int = 50,
                    engine: FormulaEngine | None = None) -> list[str]:
    """某一列的去重取值（做筛选下拉/数据验证清单用）。"""
    _header, rows = _data_rows(table, engine)
    position = table.column_index(column)
    if position < 0:
        raise ValueError(f"找不到列：{column}")
    seen: list[str] = []
    for row in rows:
        value = to_text(row[position] if position < len(row) else "").strip()
        if value and value not in seen:
            seen.append(value)
        if len(seen) >= limit:
            break
    return seen


# ---------------------------------------------------------------- 单位换算

_UNITS: dict[str, dict[str, float]] = {
    "长度": {"毫米": 0.001, "厘米": 0.01, "米": 1.0, "千米": 1000.0,
             "英寸": 0.0254, "英尺": 0.3048, "英里": 1609.344},
    "重量": {"克": 0.001, "千克": 1.0, "吨": 1000.0, "斤": 0.5, "磅": 0.45359237},
    "面积": {"平方米": 1.0, "平方千米": 1e6, "公顷": 1e4, "亩": 666.6666667,
             "平方英尺": 0.09290304},
    "存储": {"字节": 1.0, "KB": 1024.0, "MB": 1024.0 ** 2, "GB": 1024.0 ** 3, "TB": 1024.0 ** 4},
    "时间": {"秒": 1.0, "分钟": 60.0, "小时": 3600.0, "天": 86400.0},
    "速度": {"米/秒": 1.0, "千米/小时": 1 / 3.6, "英里/小时": 0.44704},
}


def unit_categories() -> list[str]:
    return list(_UNITS) + ["温度"]


def units_of(category: str) -> list[str]:
    if category == "温度":
        return ["摄氏度", "华氏度", "开尔文"]
    return list(_UNITS.get(category, {}))


def convert_units(value: float, from_unit: str, to_unit: str) -> float:
    """单位换算（长度/重量/面积/存储/时间/速度/温度）。"""
    if from_unit == to_unit:
        return float(value)
    if {from_unit, to_unit} <= {"摄氏度", "华氏度", "开尔文"}:
        return _convert_temperature(float(value), from_unit, to_unit)
    for table in _UNITS.values():
        if from_unit in table and to_unit in table:
            return float(value) * table[from_unit] / table[to_unit]
    raise ValueError(f"不支持的单位换算：{from_unit} → {to_unit}（可查看「单位列表」）")


def _convert_temperature(value: float, from_unit: str, to_unit: str) -> float:
    if from_unit == "摄氏度":
        celsius = value
    elif from_unit == "华氏度":
        celsius = (value - 32) * 5 / 9
    else:
        celsius = value - 273.15
    if to_unit == "摄氏度":
        return celsius
    if to_unit == "华氏度":
        return celsius * 9 / 5 + 32
    return celsius + 273.15


# ---------------------------------------------------------------- AI 公式

_AI_FORMULA_SYSTEM = (
    "你是表格公式助手。只输出一条可直接粘贴到单元格的公式，以 = 开头，"
    "不要解释、不要代码块围栏。可用函数：" + "、".join(FUNCTION_NAMES)
)


def formula_prompt(description: str, table: TableData | None = None, *, cell: str = "") -> str:
    """把"自然语言需求 + 表格结构"整理成给模型的提示词。"""
    lines = [f"需求：{description.strip()}"]
    if cell:
        lines.append(f"目标单元格：{cell}")
    if table is not None:
        rows = table.normalized()
        header = table.header_row()
        if header:
            lines.append("表头：" + " | ".join(header))
        lines.append("前几行数据：")
        for row in rows[1:6]:
            lines.append("  " + " | ".join(row))
        lines.append(f"数据区：{column_letters(0)}2:{column_letters(max(0, table.width - 1))}{max(2, len(rows))}")
    return "\n".join(lines)


def parse_formula_reply(reply: str) -> str:
    """从模型回复里取出公式（容忍代码块、解释文字与中文尾巴）。"""
    text = (reply or "").strip()
    fenced = re.search(r"```(?:excel|formula)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    match = re.search(r"=\s*[A-Za-z(].*", text)
    formula = match.group(0).strip() if match else text
    formula = formula.splitlines()[0].strip() if formula else ""
    return _trim_formula_tail(formula)


def _trim_formula_tail(formula: str) -> str:
    """去掉公式后面跟着的中文解释（字符串常量里的中文不动）。"""
    in_string = False
    index = 0
    while index < len(formula):
        char = formula[index]
        if char == '"':
            if in_string and index + 1 < len(formula) and formula[index + 1] == '"':
                index += 2
                continue
            in_string = not in_string
        elif not in_string and ("\u4e00" <= char <= "\u9fff" or char in "，。；：！？"):
            return formula[:index].strip()
        index += 1
    return formula.strip()


def suggest_formula(chat: Callable[[list[dict]], str], description: str,
                    table: TableData | None = None, *, cell: str = "") -> str:
    """自然语言 → 公式（`chat` 是 `lambda messages: text`，便于测试与降级）。"""
    messages = [
        {"role": "system", "content": _AI_FORMULA_SYSTEM},
        {"role": "user", "content": formula_prompt(description, table, cell=cell)},
    ]
    return parse_formula_reply(chat(messages))


def explain_formula(chat: Callable[[list[dict]], str], formula: str) -> str:
    return chat([
        {"role": "system", "content": "用中文简要解释这条表格公式的作用与参数（不超过 120 字）。"},
        {"role": "user", "content": formula},
    ]).strip()


def repair_formula(chat: Callable[[list[dict]], str], formula: str, error: str) -> str:
    reply = chat([
        {"role": "system", "content": _AI_FORMULA_SYSTEM + " 这一次请修复报错的公式。"},
        {"role": "user", "content": f"公式：{formula}\n报错：{error}"},
    ])
    return parse_formula_reply(reply)


__all__ = [
    "CUSTOM_LISTS",
    "FILTER_MODES",
    "FUNCTIONS",
    "FUNCTION_NAMES",
    "Array",
    "CellRef",
    "FormulaEngine",
    "FormulaError",
    "PatternSpec",
    "SeriesSpec",
    "apply_pattern",
    "autofill",
    "cell_value",
    "chart_series",
    "check_formulas",
    "column_index",
    "column_letters",
    "convert_units",
    "detect_series",
    "distinct_values",
    "evaluate_formula",
    "explain_formula",
    "fill_series",
    "filter_table",
    "formula_prompt",
    "group_by",
    "infer_pattern",
    "infer_patterns",
    "parse_cell_ref",
    "parse_date",
    "pivot",
    "repair_formula",
    "resolved_matrix",
    "sheet_name_of",
    "smart_fill",
    "sort_table",
    "suggest_formula",
    "summarize_column",
    "summarize_table",
    "to_number",
    "to_text",
    "translate_formula",
    "unit_categories",
    "units_of",
]

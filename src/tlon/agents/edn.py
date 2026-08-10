"""EDN data literal parser (L0 of the agent behavior DSL).

Parses a minimal EDN subset into plain Python data — no evaluation:

- ``nil`` / ``true`` / ``false``
- integers and floats
- strings (with escapes)
- keywords (``:foo-bar``) -> :class:`Keyword`
- symbols (``react``) -> :class:`Symbol`
- vectors ``[...]`` -> list
- lists ``(...)`` -> :class:`Form` (code, not data — evaluated by ``agents/eval.py``)
- maps ``{...}`` -> dict
- ``;`` line comments; commas are treated as whitespace

Keyword and Symbol are distinct str subclasses so that ``:mode :react``
and ``:mode "react"`` stay semantically distinguishable downstream.
Likewise Form (a list subclass) keeps ``(...)`` (an expression) distinct
from ``[...]`` (a data vector) for the restricted evaluator.

Parse failures raise :class:`EDNParseError` with line/column information.
``dumps`` serializes back to EDN (test roundtrips, future agent-written
spec files).
"""

from typing import Any

_SYMBOL_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789*+!-_?./<>="
)
_SYMBOL_START_EXTRA = set("*+!_?./<>=")
_WHITESPACE = set(" \t\n\r\f,")
_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
}


class Keyword(str):
    """An EDN keyword (``:foo-bar``), stored without the leading colon."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Keyword({str.__repr__(self)})"


class Symbol(str):
    """An EDN symbol (``react``)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Symbol({str.__repr__(self)})"


class Form(list[Any]):
    """An EDN list form (``(...)``) — an expression, distinct from a vector.

    Vectors (``[...]``) parse to plain lists (data); lists (``(...)``) parse
    to Form (code for the restricted evaluator in ``agents/eval.py``).
    """

    def __repr__(self) -> str:
        return f"Form({list.__repr__(self)})"


class EDNParseError(ValueError):
    """EDN parse failure with source position (1-based line/column)."""

    def __init__(self, message: str, line: int, column: int):
        self.message = message
        self.line = line
        self.column = column
        super().__init__(f"{message} (line {line}, column {column})")


def loads(text: str) -> Any:
    """Parse a single EDN form from ``text``.

    Raises:
        EDNParseError: on malformed input or trailing content.
    """
    parser = _Parser(text)
    value = parser.parse_value()
    parser.skip_whitespace()
    if not parser.at_end:
        raise parser.error(f"unexpected trailing content: {parser.peek()!r}")
    return value


def dumps(value: Any) -> str:
    """Serialize Python data back to an EDN literal.

    Supports None/bool/int/float/str/Keyword/Symbol/Form/list/tuple/dict.
    """
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Keyword):
        return ":" + value
    if isinstance(value, Symbol):
        return str(value)
    if isinstance(value, str):
        return _dump_string(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Form):
        return "(" + " ".join(dumps(item) for item in value) + ")"
    if isinstance(value, (list, tuple)):
        return "[" + " ".join(dumps(item) for item in value) + "]"
    if isinstance(value, dict):
        pairs = " ".join(f"{dumps(k)} {dumps(v)}" for k, v in value.items())
        return "{" + pairs + "}"
    raise TypeError(f"cannot dump {type(value).__name__} as EDN")


def _dump_string(value: str) -> str:
    out = []
    for ch in value:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


class _Parser:
    """Recursive-descent cursor over the input text."""

    def __init__(self, text: str):
        self._text = text
        self._pos = 0
        self._line = 1
        self._column = 1

    # ── Cursor ──

    @property
    def at_end(self) -> bool:
        return self._pos >= len(self._text)

    def peek(self) -> str:
        return self._text[self._pos]

    def advance(self) -> str:
        ch = self._text[self._pos]
        self._pos += 1
        if ch == "\n":
            self._line += 1
            self._column = 1
        else:
            self._column += 1
        return ch

    def error(self, message: str) -> EDNParseError:
        return EDNParseError(message, self._line, self._column)

    def skip_whitespace(self) -> None:
        while not self.at_end:
            ch = self.peek()
            if ch in _WHITESPACE:
                self.advance()
            elif ch == ";":
                while not self.at_end and self.peek() != "\n":
                    self.advance()
            else:
                return

    # ── Forms ──

    def parse_value(self) -> Any:
        self.skip_whitespace()
        if self.at_end:
            raise self.error("unexpected end of input")
        ch = self.peek()
        if ch == "{":
            return self.parse_map()
        if ch == "[":
            return self.parse_vector()
        if ch == "(":
            return self.parse_form()
        if ch == '"':
            return self.parse_string()
        if ch == ":":
            return self.parse_keyword()
        if ch in ")]}":
            raise self.error(f"unmatched delimiter: {ch!r}")
        if ch.isdigit() or (ch in "+-" and self._peek_next_is_digit()):
            return self.parse_number()
        if ch.isalpha() or ch in _SYMBOL_START_EXTRA or ch in "+-":
            return self.parse_symbol()
        raise self.error(f"unexpected character: {ch!r}")

    def _peek_next_is_digit(self) -> bool:
        return self._pos + 1 < len(self._text) and self._text[self._pos + 1].isdigit()

    def parse_vector(self) -> list[Any]:
        self.advance()  # consume '['
        items: list[Any] = []
        while True:
            self.skip_whitespace()
            if self.at_end:
                raise self.error("unterminated vector: expected ']'")
            if self.peek() == "]":
                self.advance()
                return items
            items.append(self.parse_value())

    def parse_form(self) -> Form:
        self.advance()  # consume '('
        items = Form()
        while True:
            self.skip_whitespace()
            if self.at_end:
                raise self.error("unterminated list: expected ')'")
            if self.peek() == ")":
                self.advance()
                return items
            items.append(self.parse_value())

    def parse_map(self) -> dict[Any, Any]:
        self.advance()  # consume '{'
        items: list[Any] = []
        while True:
            self.skip_whitespace()
            if self.at_end:
                raise self.error("unterminated map: expected '}'")
            if self.peek() == "}":
                self.advance()
                break
            items.append(self.parse_value())
        if len(items) % 2 != 0:
            raise self.error("map literal must contain an even number of forms")
        result: dict[Any, Any] = {}
        for i in range(0, len(items), 2):
            result[items[i]] = items[i + 1]
        return result

    def parse_string(self) -> str:
        self.advance()  # consume opening quote
        out: list[str] = []
        while True:
            if self.at_end:
                raise self.error("unterminated string")
            ch = self.advance()
            if ch == '"':
                return "".join(out)
            if ch == "\\":
                out.append(self._parse_escape())
            else:
                out.append(ch)

    def _parse_escape(self) -> str:
        if self.at_end:
            raise self.error("unterminated string escape")
        ch = self.advance()
        if ch in _ESCAPES:
            return _ESCAPES[ch]
        if ch == "u":
            digits = ""
            for _ in range(4):
                if self.at_end or self.peek() not in "0123456789abcdefABCDEF":
                    raise self.error("invalid unicode escape: expected 4 hex digits")
                digits += self.advance()
            return chr(int(digits, 16))
        raise self.error(f"invalid escape sequence: \\{ch}")

    def parse_keyword(self) -> Keyword:
        self.advance()  # consume ':'
        name = self._read_symbol_chars()
        if not name:
            raise self.error("keyword must have a name after ':'")
        return Keyword(name)

    def parse_symbol(self) -> None | bool | Symbol:
        name = self._read_symbol_chars()
        if name == "nil":
            return None
        if name == "true":
            return True
        if name == "false":
            return False
        if not name:
            raise self.error("expected a symbol")
        return Symbol(name)

    def _read_symbol_chars(self) -> str:
        start = self._pos
        while not self.at_end and self.peek() in _SYMBOL_CHARS:
            self.advance()
        return self._text[start : self._pos]

    def parse_number(self) -> int | float:
        start = self._pos
        if self.peek() in "+-":
            self.advance()
        while not self.at_end and self.peek().isdigit():
            self.advance()
        is_float = False
        if not self.at_end and self.peek() == "." and self._peek_next_is_digit():
            is_float = True
            self.advance()
            while not self.at_end and self.peek().isdigit():
                self.advance()
        if not self.at_end and self.peek() in "eE":
            is_float = True
            self.advance()
            if not self.at_end and self.peek() in "+-":
                self.advance()
            if self.at_end or not self.peek().isdigit():
                raise self.error("invalid number: exponent has no digits")
            while not self.at_end and self.peek().isdigit():
                self.advance()
        token = self._text[start : self._pos]
        if not token or token in "+-":
            raise self.error(f"invalid number: {token!r}")
        try:
            return float(token) if is_float else int(token)
        except ValueError:
            raise self.error(f"invalid number: {token!r}") from None

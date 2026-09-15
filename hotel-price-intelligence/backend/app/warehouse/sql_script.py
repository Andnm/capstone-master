"""Lexer SQL streaming cho 2 viec: tach statement top-level + phat hien statement bi cam.

LICH SU SUA (quan trong, dung xoa): ban dau module nay dung "pre-filter regex 2 tang" voi lap luan
"tang A khong the bo sot". **Lap luan do SAI**, GPT chung minh bang 3 phan vi du va kiem chung tren
MySQL 8.0.45 that (discuss/warehouse-build-implementation/06):

    CREATE\\nDATABASE evil;              -> pre-filter bo sot (regex chi cho [ \\t] giua 2 keyword)
    /*!40101 CREATE\\nDATABASE evil */;  -> pre-filter bo sot
    CREATE/**/DATABASE evil;            -> CA pre-filter LAN full scan deu bo sot

Ca thu 3 nghiem trong nhat: `classify_forbidden()` cu dung `^CREATE\\s+DATABASE`, ma `\\s` khong
khop comment. MySQL thi chap nhan `CREATE/**/DATABASE x` va tao database that. Tuc scan an toan
truoc restore co lo hong that su, khong phai ly thuyet.

THIET KE HIEN TAI - lexer state machine THUC SU, khong con pre-filter:

- Nap theo TUNG DONG (khong phai chunk co dinh). Ly do: `--`, `#`, `/*`, `*/`, `/*!` deu la cap ky
  tu LIEN KE nen khong the bi cat qua ranh gioi dong; mot word token cung khong the chua newline.
  Chi 2 thu co the vat qua dong: chuoi co newline that, va block comment nhieu dong - ca hai deu do
  state machine mang qua giua cac dong. Nho vay KHONG can overlap tuy tien (kieu `_OVERLAP=64` cu,
  von khong chung minh duoc an toan khi khoang trang/comment o bien dai hon 64 byte).
- Khoang trang VA regular comment deu la SEPARATOR, khong phai token. Nho vay `CREATE/**/DATABASE`
  duoc nhin thay dung la 2 token `CREATE`, `DATABASE`.
- Noi dung `/*!...*/` (executable version comment) duoc quet NHU CODE - MySQL that su thuc thi no.
  Nhung `;` ben trong KHONG ket thuc statement (comment phai dong truoc).
- Chi giu 2 token dau cua moi statement -> bo nho O(1), khong load ca file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .errors import SqlScriptError

# Bo tu khoa bi cam. CREATE/DROP SCHEMA la dong nghia cua CREATE/DROP DATABASE trong MySQL - phai
# chan ca hai, neu khong `CREATE SCHEMA x` lot qua trong khi no lam dung viec bi cam.
_FIRST_TOKEN_FORBIDDEN = {"USE": "USE"}
_TWO_TOKEN_FORBIDDEN = {
    ("CREATE", "DATABASE"): "CREATE DATABASE",
    ("CREATE", "SCHEMA"): "CREATE DATABASE",
    ("DROP", "DATABASE"): "DROP DATABASE",
    ("DROP", "SCHEMA"): "DROP DATABASE",
}

_WORD_RE = re.compile(r"[A-Za-z0-9_$]+")
_SPACE_RE = re.compile(r"\s+")
_EXEC_COMMENT_OPEN_RE = re.compile(r"/\*!(\d{5})?")

# Nhay toi ky tu "dang quan tam" ke tiep BEN TRONG chuoi/identifier, thay vi duyet tung ky tu.
# Thuan tuy toi uu: ngu nghia khong doi, nhung phan than chuoi (phan lon dump) duoc bo qua trong C
# thay vi trong vong lap Python. Do duoc tren dump that: 195s -> xem ket qua o discuss file 07.
_QUOTED_JUMP_RE = {
    "'": re.compile(r"['\\]"),
    '"': re.compile(r"[\"\\]"),
    "`": re.compile(r"`"),
}

# States. `_CODE` la trang thai "dang o code", con viec co dang nam TRONG executable comment hay
# khong duoc giu rieng o co `_exec` - KHONG gop vao state.
#
# Vi sao tach: neu coi "trong exec comment" la 1 state rieng thi khi gap chuoi hoac comment long
# ben trong no, luc dong lai ta khong biet phai quay ve _NORMAL hay ve exec comment. mysqldump
# THAT SU phat `/*!40103 SET TIME_ZONE='+00:00' */;` - co chuoi ben trong exec comment - nen day
# khong phai ca gia dinh. Voi co rieng, chuoi/comment luon quay ve `_CODE` va `_exec` giu nguyen.
_CODE, _SQUOTE, _DQUOTE, _BACKTICK, _LINE_COMMENT, _BLOCK_COMMENT = range(6)
_QUOTE_CHAR = {_SQUOTE: "'", _DQUOTE: '"', _BACKTICK: "`"}

_MAX_TOKENS = 2  # chi can 2 token dau de phan loai


@dataclass(frozen=True)
class Statement:
    text: str
    offset: int  # vi tri ky tu dau tien cua statement trong van ban goc - de bao loi co toa do


class SqlLexer:
    """State machine nap theo dong. Tra ve (offset, tokens, text?) cho tung statement top-level."""

    def __init__(self, *, capture_text: bool, max_capture: int | None = None) -> None:
        self.capture_text = capture_text
        # `max_capture` chan mot INSERT extended 100 MB bien thanh 1 chuoi trong RAM khi ta chi can
        # vai chuc ky tu dau lam preview bao loi. None = khong gioi han (dung cho setup.sql, phai
        # lay nguyen van statement de chay).
        self.max_capture = max_capture
        self._state = _CODE
        self._exec = False                 # dang o trong `/*! ... */` (noi dung LA code)
        self._pending_escape = False       # chuoi ket thuc dong bang '\' -> escape ky tu dau dong sau
        self._offset = 0                   # offset toan cuc cua ky tu dang xet
        self._statement_offset = 0
        self._tokens: list[str] = []
        self._chunks: list[str] = []
        self._captured = 0
        self._has_code = False             # da gap it nhat 1 token co nghia (khong phai comment thuan)
        self._seen_content = False
        self._saw_delimiter_keyword = False

    # ---------------------------------------------------------------- public
    def feed_line(self, line: str) -> Iterator[tuple[int, tuple[str, ...], str | None]]:
        index = 0
        length = len(line)
        while index < length:
            if self._state == _CODE:
                index, statement = self._step_code(line, index, length)
                if statement is not None:
                    yield statement
            elif self._state in (_SQUOTE, _DQUOTE, _BACKTICK):
                index = self._step_quoted(line, index, length)
            elif self._state == _LINE_COMMENT:
                index = self._step_line_comment(line, index, length)
            else:  # _BLOCK_COMMENT
                index = self._step_block_comment(line, index, length)

    def finalize(self) -> Iterator[tuple[int, tuple[str, ...], str | None]]:
        """Statement cuoi khong co `;`. Trang thai con dang do = file hong -> raise."""
        if self._state in (_SQUOTE, _DQUOTE, _BACKTICK):
            raise SqlScriptError(
                f"trich dan {_QUOTE_CHAR[self._state]!r} khong bao gio duoc dong (het file)."
            )
        if self._state == _BLOCK_COMMENT or self._exec:
            raise SqlScriptError("comment /* khong bao gio duoc dong (het file).")
        if self._has_code:
            yield self._emit()

    # ---------------------------------------------------------------- states
    def _step_code(self, line: str, index: int, length: int) -> tuple[int, tuple | None]:
        char = line[index]
        pair = line[index:index + 2]

        if self._exec and pair == "*/":
            self._append(pair)
            self._exec = False
            return index + 2, None

        if char in ("'", '"', "`"):
            self._append(char)
            self._state = {"'": _SQUOTE, '"': _DQUOTE, "`": _BACKTICK}[char]
            self._push_token("")  # element co trich dan chiem 1 slot token nhung KHONG phai keyword
            return index + 1, None

        if pair == "--" and (index + 2 >= length or line[index + 2] in " \t\r\n"):
            self._append(pair)
            self._state = _LINE_COMMENT
            return index + 2, None

        if char == "#":
            self._append(char)
            self._state = _LINE_COMMENT
            return index + 1, None

        if pair == "/*":
            match = _EXEC_COMMENT_OPEN_RE.match(line, index)
            if match and not self._exec:
                self._append(match.group())
                self._exec = True
                return match.end(), None
            self._append(pair)
            self._state = _BLOCK_COMMENT
            return index + 2, None

        if char == ";" and not self._exec:
            # Statement chi gom comment/khoang trang (`_has_code=False`) bi bo qua - khong emit,
            # vi chay no tren MySQL se loi cu phap.
            statement = self._emit() if self._has_code else None
            self._append(char)
            self._reset_statement()
            return index + 1, statement

        if char.isspace():
            space = _SPACE_RE.match(line, index)
            self._append(space.group())
            return space.end(), None

        match = _WORD_RE.match(line, index)
        if match:
            word = match.group()
            self._append(word)
            self._push_token(word.upper())
            return match.end(), None

        self._append(char)
        self._push_token("")  # toan tu/dau ngoac... chiem slot nhung khong phai keyword
        return index + 1, None

    def _step_quoted(self, line: str, index: int, length: int) -> int:
        quote = _QUOTE_CHAR[self._state]
        allow_backslash = self._state != _BACKTICK
        if self._pending_escape:
            self._pending_escape = False
            self._append(line[index])
            index += 1
        jump = _QUOTED_JUMP_RE[quote]
        while index < length:
            match = jump.search(line, index)
            if match is None:
                self._append(line[index:])  # ca phan con lai la than chuoi
                return length
            if match.start() > index:
                self._append(line[index:match.start()])
                index = match.start()
            char = line[index]
            if allow_backslash and char == "\\":
                if index + 1 >= length:
                    # '\' o cuoi dong escape ky tu dau tien cua dong ke tiep.
                    self._append(char)
                    self._pending_escape = True
                    return length
                self._append(line[index:index + 2])
                index += 2
                continue
            if index + 1 < length and line[index + 1] == quote:
                self._append(line[index:index + 2])  # doubling: '' hoac ``
                index += 2
                continue
            self._append(char)
            self._state = _CODE  # `_exec` giu nguyen: chuoi trong exec comment khong thoat khoi no
            return index + 1
        return length

    def _step_line_comment(self, line: str, index: int, length: int) -> int:
        newline = line.find("\n", index)
        if newline == -1:
            self._append(line[index:])
            return length
        self._append(line[index:newline + 1])
        self._state = _CODE
        return newline + 1

    def _step_block_comment(self, line: str, index: int, length: int) -> int:
        close = line.find("*/", index)
        if close == -1:
            self._append(line[index:])
            return length
        self._append(line[index:close + 2])
        self._state = _CODE
        return close + 2

    # ---------------------------------------------------------------- helpers
    def _append(self, text: str) -> None:
        if self.capture_text and (self.max_capture is None or self._captured < self.max_capture):
            self._chunks.append(text)
            self._captured += len(text)
        if not self._seen_content and text.strip():
            self._seen_content = True
            self._statement_offset = self._offset
        self._offset += len(text)

    def _push_token(self, token: str) -> None:
        self._has_code = True
        if len(self._tokens) < _MAX_TOKENS:
            self._tokens.append(token)
            if len(self._tokens) == 1 and token == "DELIMITER":
                self._saw_delimiter_keyword = True

    def _emit(self) -> tuple[int, tuple[str, ...], str | None]:
        text = "".join(self._chunks).strip() if self.capture_text else None
        return self._statement_offset, tuple(self._tokens), text

    def _reset_statement(self) -> None:
        self._tokens = []
        self._chunks = []
        self._captured = 0
        self._has_code = False
        self._seen_content = False

    @property
    def saw_delimiter_keyword(self) -> bool:
        return self._saw_delimiter_keyword


def classify_tokens(tokens: tuple[str, ...]) -> str | None:
    """Phan loai statement chi tu 2 token dau da chuan hoa."""
    if not tokens:
        return None
    label = _FIRST_TOKEN_FORBIDDEN.get(tokens[0])
    if label:
        return label
    if len(tokens) >= 2:
        return _TWO_TOKEN_FORBIDDEN.get((tokens[0], tokens[1]))
    return None


def _iter_lines(text: str) -> Iterator[str]:
    return iter(text.splitlines(keepends=True))


def _strip_bom(first_line: str) -> str:
    """UTF-8 BOM dau file bi bo tuong minh - khong de no dinh vao token dau tien."""
    return first_line.lstrip("﻿")


def _scan(lines: Iterator[str], *, capture_text: bool, max_capture: int | None = None):
    lexer = SqlLexer(capture_text=capture_text, max_capture=max_capture)
    first = True
    for line in lines:
        if first:
            line = _strip_bom(line)
            first = False
        yield from lexer.feed_line(line)
    yield from lexer.finalize()
    if lexer.saw_delimiter_keyword:
        raise SqlScriptError(
            "gap lenh DELIMITER - day la lenh cua mysql client, lexer nay khong ho tro. "
            "Dung lai thay vi parse sai."
        )


def iter_statements(sql_text: str) -> Iterator[Statement]:
    """Tach `sql_text` thanh statement top-level, giu nguyen van ban goc cua tung statement."""
    for offset, _tokens, text in _scan(_iter_lines(sql_text), capture_text=True):
        if text:
            yield Statement(text=text, offset=offset)


def classify_forbidden(statement_text: str) -> str | None:
    """Tra nhan statement bi cam neu co, nguoc lai None. Nhan DUNG 1 statement."""
    for _offset, tokens, _text in _scan(_iter_lines(statement_text), capture_text=False):
        label = classify_tokens(tokens)
        if label:
            return label
    return None


def scan_text_for_forbidden(sql_text: str) -> list[tuple[str, int, str]]:
    """Tra list (nhan, offset, 80 ky tu dau cua statement) cho moi statement bi cam."""
    found: list[tuple[str, int, str]] = []
    for offset, tokens, text in _scan(_iter_lines(sql_text), capture_text=True):
        label = classify_tokens(tokens)
        if label:
            found.append((label, offset, (text or "")[:80].replace("\n", " ")))
    return found


def scan_file_for_forbidden(path: str | Path) -> list[tuple[str, int, str]]:
    """Scan 1 file .sql (co the hang tram MB) - muc 3a buoc 3. Streaming, bo nho O(1).

    KHONG con pre-filter/fast path: mot duong duy nhat, de khong ton tai nhanh "nhanh nhung sai".
    Van ban cua statement bi cam duoc capture theo yeu cau bao loi, nhung chi giu 200 ky tu dau de
    mot INSERT khong lo bien thanh chuoi 100 MB trong bo nho.
    """
    found: list[tuple[str, int, str]] = []
    lexer = SqlLexer(capture_text=True, max_capture=200)
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        first = True
        for line in handle:
            if first:
                line = _strip_bom(line)
                first = False
            for offset, tokens, text in lexer.feed_line(line):
                label = classify_tokens(tokens)
                if label:
                    found.append((label, offset, (text or "")[:80].replace("\n", " ")))
        for offset, tokens, text in lexer.finalize():
            label = classify_tokens(tokens)
            if label:
                found.append((label, offset, (text or "")[:80].replace("\n", " ")))
    if lexer.saw_delimiter_keyword:
        raise SqlScriptError(
            f"file {path} chua lenh DELIMITER - lexer nay khong ho tro, dung lai thay vi parse sai."
        )
    return found


def sanitize_setup_sql(sql_text: str) -> list[str]:
    """Bo `CREATE DATABASE`/`USE` khoi setup.sql, giu nguyen thu tu cac statement con lai.

    Muc 3a buoc 6. `DROP DATABASE` thi KHONG sanitize ma raise - no khong bao gio duoc phep xuat
    hien trong setup.sql va neu co thi la dau hieu file bi sua.
    """
    statements: list[str] = []
    for offset, tokens, text in _scan(_iter_lines(sql_text), capture_text=True):
        if not text:
            continue
        label = classify_tokens(tokens)
        if label == "DROP DATABASE":
            raise SqlScriptError(
                f"setup.sql chua DROP DATABASE tai offset {offset} - tu choi chay. "
                f"File nay chi duoc phep tao schema, khong duoc xoa database."
            )
        if label in ("CREATE DATABASE", "USE"):
            continue
        statements.append(text)
    if not statements:
        raise SqlScriptError("setup.sql khong con statement nao sau khi sanitize - file rong hay sai?")
    return statements

"""Lexer SQL streaming + preflight dump (WAREHOUSE_EDA_ML_SPEC.md muc 3a buoc 3).

VAI TRO - DOC TRUOC KHI SUA: lexer nay la PREFLIGHT theo spec, **khong phai ranh gioi an toan**.
Ranh gioi an toan that nam o `staging.py`: dump duoc restore bang mot user MySQL tam CHI co quyen
tren dung 1 staging database. (`mysql --binary-mode` chi giam be mat tan cong: do that chan duoc
`tee`/system nhung KHONG chan `delimiter` - GPT review 10 N1 - nen khong phai ranh gioi.)
Server tu kiem tra quyen bang chinh parser cua no, nen khong phu thuoc viec lexer nay hieu SQL giong
MySQL hay khong.

Ly do phai tach vai tro: 3 vong review lien tiep (discuss/warehouse-build-implementation/06, 08) da
chung minh gia dinh "lexer = MySQL" sai, moi lan mot kieu, va ca 2 vong cuoi GPT deu tao duoc
database that tren MySQL 8.0.45 qua ban lexer cu:
- `CREATE/**/DATABASE`, `CREATE\\nDATABASE` (regex/khoang trang);
- `--\\f comment` (MySQL coi `--` + ky tu dieu khien la comment, lexer cu khong);
- `CREATE /*!99999 x */ DATABASE` (server BO QUA version comment cao hon ban than no).
Loai statement phan loai bang "vai token dau" co mot diem yeu cau truc: bat ky cho nao lexer hieu
comment/khoang trang khac server, theo BAT KY chieu nao, deu lam token lech vi tri va giau statement
nguy hiem. Nen ngoai viec bam sat server, preflight con dung ALLOWLIST: moi statement cua dump phai
dung mot trong cac dang mysqldump that su phat ra; token lech -> khong khop allowlist -> FAIL.

QUY TAC BAM THEO SERVER - do that tren MySQL 8.0.45 (khong suy tu tai lieu):
- `--` la comment khi ky tu ke tiep co ma <= 0x20 hoac = 0x7F (space, \\t, \\v, \\f, \\r, 0x01, 0x1F,
  DEL deu la comment; NBSP, U+3000, chu cai, `-` thi KHONG).
- `/*!NNNNN` voi dung 5 chu so: noi dung la code neu NNNNN <= version server, bi BO QUA neu lon hon
  (80046 bi bo qua tren 8.0.45). `/*!` khong chu so: luon la code. 1-4 hoac >= 6 chu so: server bao
  loi cu phap -> FAIL closed.
- Khoang trang giua token: chi ASCII `\\t \\n \\v \\f \\r` va space. Ky tu khac o vung code thanh token
  "op" -> khong khop allowlist -> FAIL closed.

Nap theo dong, CHI tach o `\\n` (khong dung `str.splitlines()`, vi no tach ca `\\v`/`\\f`/`\\x1c`...
trong khi MySQL chi ket thuc line comment o `\\n`). File doc o che do nhi phan, decode UTF-8 strict:
byte khong hop le -> FAIL (mysqldump voi `--set-charset` luon ra UTF-8 hop le).

Bo nho: O(dong dai nhat + max_capture), khong phai O(1) - vong `for line in file` van nap nguyen
mot dong. Dump that co dong dai nhat ~1,05 MB, chap nhan duoc.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .errors import SqlScriptError

WORD, IDENT, STRING, OP = "word", "ident", "string", "op"

_FORBIDDEN_TWO = {
    ("CREATE", "DATABASE"): "CREATE DATABASE",
    ("CREATE", "SCHEMA"): "CREATE DATABASE",   # SCHEMA la dong nghia DATABASE trong MySQL
    ("DROP", "DATABASE"): "DROP DATABASE",
    ("DROP", "SCHEMA"): "DROP DATABASE",
}

_WORD_RE = re.compile(r"[A-Za-z0-9_$]+")
_SPACE_RE = re.compile(r"[\t\n\x0b\x0c\r ]+")
# ASCII tuong minh: `\d` cua Python khop ca chu so Unicode (vd '٤٠١٠١'), con MySQL chi nhan 0-9.
_DIGITS_RE = re.compile(r"[0-9]*")
_QUOTED_JUMP_RE = {"'": re.compile(r"['\\]"), '"': re.compile(r"[\"\\]"), "`": re.compile(r"`")}

# `_CODE` = dang o code. Viec co dang nam TRONG executable comment duoc giu o co `_exec` rieng: chuoi
# ben trong exec comment (mysqldump phat that: `/*!40103 SET TIME_ZONE='+00:00' */`) dong lai phai
# quay ve dung "code trong exec comment", khong phai ve code thuong.
_CODE, _SQUOTE, _DQUOTE, _BACKTICK, _LINE_COMMENT, _BLOCK_COMMENT = range(6)
_QUOTE_STATE = {"'": _SQUOTE, '"': _DQUOTE, "`": _BACKTICK}
_QUOTE_CHAR = {_SQUOTE: "'", _DQUOTE: '"', _BACKTICK: "`"}

_MAX_TOKENS = 6  # du cho `DROP TABLE IF EXISTS `x`` + token ke tiep (kiem tra ten co tien to db)


def _dash_comment_terminator(char: str) -> bool:
    code = ord(char)
    return code <= 0x20 or code == 0x7F


def mysql_version_id(version: str) -> int:
    """'8.0.45' / '8.0.46-log' -> 80045 / 80046 (dang so cua MySQL cho version comment)."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version or "")
    if not match:
        raise SqlScriptError(f"khong doc duoc version MySQL tu {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return major * 10000 + minor * 100 + patch


@dataclass(frozen=True)
class ScannedStatement:
    offset: int
    tokens: tuple[tuple[str, str | None], ...]
    text: str | None       # tu token code dau tien; comment dung truoc KHONG duoc capture
    truncated: bool        # text bi cat boi max_capture


class SqlLexer:
    """State machine nap theo dong. Yield `ScannedStatement` cho moi statement top-level co code."""

    def __init__(self, *, capture_text: bool, max_capture: int | None = None,
                 server_version: int | None = None) -> None:
        self.capture_text = capture_text
        self.max_capture = max_capture
        # None = khong biet version server -> gap BAT KY versioned comment nao cung FAIL closed.
        self.server_version = server_version
        self._state = _CODE
        self._exec = False
        self._pending_escape = False
        self._offset = 0
        self._saw_delimiter = False
        self._reset_statement()

    # ---------------------------------------------------------------- public
    def feed_line(self, line: str) -> Iterator[ScannedStatement]:
        index, length = 0, len(line)
        while index < length:
            if self._state == _CODE:
                index, statement = self._step_code(line, index, length)
                if statement is not None:
                    yield statement
            elif self._state in _QUOTE_CHAR:
                index = self._step_quoted(line, index, length)
            elif self._state == _LINE_COMMENT:
                index = self._step_line_comment(line, index)
            else:
                index = self._step_block_comment(line, index)

    def finalize(self) -> Iterator[ScannedStatement]:
        if self._state in _QUOTE_CHAR:
            raise SqlScriptError(f"trich dan {_QUOTE_CHAR[self._state]!r} khong bao gio duoc dong (het file).")
        if self._state == _BLOCK_COMMENT or self._exec:
            raise SqlScriptError("comment /* khong bao gio duoc dong (het file).")
        if self._has_code:
            yield self._emit()

    @property
    def saw_delimiter(self) -> bool:
        return self._saw_delimiter

    # ---------------------------------------------------------------- states
    def _step_code(self, line: str, index: int, length: int):
        char = line[index]
        pair = line[index:index + 2]

        if self._exec and pair == "*/":
            self._append(pair)
            self._exec = False
            return index + 2, None
        if char in _QUOTE_STATE:
            self._mark_code()
            self._append(char)
            self._state = _QUOTE_STATE[char]
            self._push_token(IDENT if char == "`" else STRING, None)
            return index + 1, None
        if pair == "--" and (index + 2 >= length or _dash_comment_terminator(line[index + 2])):
            self._reject_comment_in_exec("--")
            self._append(pair)
            self._state = _LINE_COMMENT
            return index + 2, None
        if char == "#":
            self._reject_comment_in_exec("#")
            self._append(char)
            self._state = _LINE_COMMENT
            return index + 1, None
        if pair == "/*":
            if line[index + 2:index + 3] == "!":
                return self._open_versioned(line, index), None
            self._reject_comment_in_exec("/*")
            self._append(pair)
            self._state = _BLOCK_COMMENT
            return index + 2, None
        if char == ";" and not self._exec:
            statement = self._emit() if self._has_code else None
            self._append(char)
            self._reset_statement()
            return index + 1, statement
        space = _SPACE_RE.match(line, index)
        if space:
            self._append(space.group())
            return space.end(), None
        self._mark_code()
        word = _WORD_RE.match(line, index)
        if word:
            self._append(word.group())
            self._push_token(WORD, word.group().upper())
            return word.end(), None
        self._append(char)
        self._push_token(OP, char)
        return index + 1, None

    def _open_versioned(self, line: str, index: int) -> int:
        if self._exec:
            raise SqlScriptError(
                f"offset {self._offset}: versioned comment long trong executable comment - hanh vi server "
                f"chua kiem chung, FAIL closed."
            )
        digits = _DIGITS_RE.match(line, index + 3).group()
        end = index + 3 + len(digits)
        if digits and len(digits) != 5:
            raise SqlScriptError(
                f"offset {self._offset}: `/*!` + {len(digits)} chu so - MySQL 8.0.45 chi doc dung 5 chu so "
                f"(do that: 4 va 6 chu so deu ra loi cu phap). mysqldump khong phat dang nay -> FAIL."
            )
        if digits and self.server_version is None:
            raise SqlScriptError(
                f"offset {self._offset}: gap /*!{digits} nhung khong biet version server dich - khong "
                f"quyet dinh duoc noi dung la code hay bi bo qua, FAIL closed."
            )
        if not digits or int(digits) <= self.server_version:
            self._mark_code()
            self._append(line[index:end])
            self._exec = True
        else:
            # Server BO QUA noi dung (vd /*!99999 hoac /*!80046 tren 8.0.45) -> day la comment thuong.
            self._append(line[index:end])
            self._state = _BLOCK_COMMENT
        return end

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
                self._append(line[index:])
                return length
            if match.start() > index:
                self._append(line[index:match.start()])
                index = match.start()
            if allow_backslash and line[index] == "\\":
                if index + 1 >= length:
                    self._append("\\")
                    self._pending_escape = True
                    return length
                self._append(line[index:index + 2])
                index += 2
                continue
            if index + 1 < length and line[index + 1] == quote:
                self._append(line[index:index + 2])  # doubling '' / ``
                index += 2
                continue
            self._append(quote)
            self._state = _CODE  # `_exec` giu nguyen
            return index + 1
        return length

    def _step_line_comment(self, line: str, index: int) -> int:
        newline = line.find("\n", index)
        if newline == -1:
            self._append(line[index:])
            return len(line)
        self._append(line[index:newline + 1])
        self._state = _CODE
        return newline + 1

    def _step_block_comment(self, line: str, index: int) -> int:
        close = line.find("*/", index)
        if close == -1:
            self._append(line[index:])
            return len(line)
        self._append(line[index:close + 2])
        self._state = _CODE
        return close + 2

    # ---------------------------------------------------------------- helpers
    def _reject_comment_in_exec(self, opener: str) -> None:
        if self._exec:
            raise SqlScriptError(
                f"offset {self._offset}: comment `{opener}` long trong executable comment - mysqldump khong "
                f"phat dang nay va hanh vi server chua kiem chung, FAIL closed."
            )

    def _mark_code(self) -> None:
        if not self._has_code:
            self._has_code = True
            self._statement_offset = self._offset

    def _append(self, text: str) -> None:
        if self.capture_text and self._has_code:
            if self.max_capture is None:
                self._chunks.append(text)
                self._captured += len(text)
            else:
                room = self.max_capture - self._captured
                if room > 0:
                    self._chunks.append(text[:room])
                    self._captured += min(room, len(text))
                if len(text) > max(room, 0):
                    self._truncated = True
        self._offset += len(text)

    def _push_token(self, kind: str, value: str | None) -> None:
        if len(self._tokens) < _MAX_TOKENS:
            self._tokens.append((kind, value))
            if len(self._tokens) == 1 and (kind, value) == (WORD, "DELIMITER"):
                self._saw_delimiter = True

    def _emit(self) -> ScannedStatement:
        text = "".join(self._chunks).strip() if self.capture_text else None
        return ScannedStatement(self._statement_offset, tuple(self._tokens), text, self._truncated)

    def _reset_statement(self) -> None:
        self._tokens: list[tuple[str, str | None]] = []
        self._chunks: list[str] = []
        self._captured = 0
        self._truncated = False
        self._has_code = False
        self._statement_offset = self._offset if hasattr(self, "_offset") else 0


# ==================================================================================== nguon dong
def _iter_text_lines(text: str) -> Iterator[str]:
    """Tach CHI o `\\n`, giu ky tu xuong dong - khop cach doc file nhi phan ben duoi."""
    if text.startswith("﻿"):
        text = text[1:]
    start = 0
    while start < len(text):
        newline = text.find("\n", start)
        if newline == -1:
            yield text[start:]
            return
        yield text[start:newline + 1]
        start = newline + 1


def _iter_file_lines(path: str | Path) -> Iterator[str]:
    """Doc nhi phan (chi tach o b'\\n' - byte 0x0A khong bao gio nam trong ky tu UTF-8 nhieu byte)."""
    with open(path, "rb") as handle:
        position = 0
        for number, raw in enumerate(handle):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SqlScriptError(
                    f"{path}: byte khong phai UTF-8 hop le o dong {number + 1} (byte offset "
                    f"{position + exc.start}) - FAIL closed."
                ) from exc
            if number == 0 and line.startswith("﻿"):
                line = line[1:]
            position += len(raw)
            yield line


def _scan(lines: Iterable[str], lexer: SqlLexer) -> Iterator[ScannedStatement]:
    for line in lines:
        yield from lexer.feed_line(line)
    yield from lexer.finalize()
    if lexer.saw_delimiter:
        raise SqlScriptError("gap lenh DELIMITER (lenh cua mysql client) - lexer khong ho tro, FAIL closed.")


# ==================================================================================== phan loai
def classify_tokens(tokens) -> str | None:
    """Spec buoc 3: USE / CREATE DATABASE / DROP DATABASE (ke ca dang SCHEMA)."""
    if not tokens or tokens[0][0] != WORD:
        return None
    if tokens[0][1] == "USE":
        return "USE"
    if len(tokens) >= 2 and tokens[1][0] == WORD:
        return _FORBIDDEN_TWO.get((tokens[0][1], tokens[1][1]))
    return None


# Dung 21 dang SET ma mysqldump 8.0.45/8.0.46 phat cho dump 4 bang core (do that tren ca 2 dump,
# giong het nhau). So sanh sau khi gom khoang trang + viet hoa. Bat ky SET nao khac - dac biet
# SQL_MODE (NO_BACKSLASH_ESCAPES/ANSI_QUOTES doi ranh gioi chuoi), charset (GBK/Big5 doi y nghia
# byte '\\'), GLOBAL/PERSIST - deu FAIL closed.
MYSQLDUMP_SET_WHITELIST = frozenset({
    "/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */",
    "/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */",
    "/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */",
    "/*!50503 SET NAMES UTF8MB4 */",
    "/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */",
    "/*!40103 SET TIME_ZONE='+00:00' */",
    "/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */",
    "/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */",
    "/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */",
    "/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */",
    "/*!40101 SET @SAVED_CS_CLIENT = @@CHARACTER_SET_CLIENT */",
    "/*!50503 SET CHARACTER_SET_CLIENT = UTF8MB4 */",
    "/*!40101 SET CHARACTER_SET_CLIENT = @SAVED_CS_CLIENT */",
    "/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */",
    "/*!40101 SET SQL_MODE=@OLD_SQL_MODE */",
    "/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */",
    "/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */",
    "/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */",
    "/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */",
    "/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */",
    "/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */",
})
_SET_CAPTURE_LIMIT = 512

# (tu khoa 1, tu khoa 2) -> vi tri token phai la `ident`. DROP xu ly rieng vi co IF EXISTS.
_TABLE_STATEMENTS = {("INSERT", "INTO"): 2, ("CREATE", "TABLE"): 2, ("ALTER", "TABLE"): 2, ("LOCK", "TABLES"): 2}


def dump_statement_problem(statement: ScannedStatement) -> str | None:
    """None neu statement dung dang mysqldump phat ra; nguoc lai tra ly do FAIL."""
    tokens = statement.tokens
    if not tokens or tokens[0][0] != WORD:
        return "statement khong bat dau bang tu khoa"
    head = tokens[0][1]
    if head == "SET":
        if statement.truncated or not statement.text:
            return "SET dai bat thuong (vuot gioi han capture)"
        normalized = re.sub(r"\s+", " ", statement.text).upper()
        return None if normalized in MYSQLDUMP_SET_WHITELIST else f"SET ngoai whitelist mysqldump: {normalized[:90]}"
    if head == "UNLOCK":
        return None if tokens == ((WORD, "UNLOCK"), (WORD, "TABLES")) else "UNLOCK sai dang"
    if head == "DROP":
        expected = ((WORD, "DROP"), (WORD, "TABLE"), (WORD, "IF"), (WORD, "EXISTS"))
        if tokens[:4] != expected or len(tokens) < 5 or tokens[4][0] != IDENT:
            return "DROP ngoai dang `DROP TABLE IF EXISTS `x``"
        return _qualified_problem(tokens, 5)
    if len(tokens) >= 2 and tokens[1][0] == WORD:
        position = _TABLE_STATEMENTS.get((head, tokens[1][1]))
        if position is not None:
            if len(tokens) <= position or tokens[position][0] != IDENT:
                return f"{head} {tokens[1][1]} khong theo sau boi ten bang `...`"
            return _qualified_problem(tokens, position + 1)
    return f"loai statement ngoai allowlist mysqldump: {head}"


def _qualified_problem(tokens, next_position: int) -> str | None:
    if len(tokens) > next_position and tokens[next_position] == (OP, "."):
        return "ten bang co tien to database (`db`.`bang`) - dump chi duoc tac dong staging cua chinh no"
    return None


# ==================================================================================== API
def iter_statements(sql_text: str, *, server_version: int | None = None) -> Iterator[ScannedStatement]:
    """Statement top-level co code (comment dung truoc bi bo khoi `text`). Dung cho setup.sql."""
    lexer = SqlLexer(capture_text=True, server_version=server_version)
    yield from _scan(_iter_text_lines(sql_text), lexer)


def classify_forbidden(statement_text: str, *, server_version: int | None = None) -> str | None:
    for statement in iter_statements(statement_text, server_version=server_version):
        label = classify_tokens(statement.tokens)
        if label:
            return label
    return None


def scan_text_for_forbidden(sql_text: str, *, server_version: int | None = None) -> list[tuple[str, int, str]]:
    return [
        (label, statement.offset, (statement.text or "")[:80].replace("\n", " "))
        for statement in iter_statements(sql_text, server_version=server_version)
        if (label := classify_tokens(statement.tokens))
    ]


def scan_file_for_forbidden(path: str | Path, *, server_version: int | None = None) -> list[tuple[str, int, str]]:
    """Spec buoc 3 thuan tuy: chi USE / CREATE DATABASE / DROP DATABASE."""
    lexer = SqlLexer(capture_text=True, max_capture=_SET_CAPTURE_LIMIT, server_version=server_version)
    return [
        (label, statement.offset, (statement.text or "")[:80].replace("\n", " "))
        for statement in _scan(_iter_file_lines(path), lexer)
        if (label := classify_tokens(statement.tokens))
    ]


_FIRST_IDENT_RE = re.compile(r"`((?:[^`]|``)*)`")


def statement_table(statement: ScannedStatement) -> str | None:
    """Ten bang o vi tri bang cua 1 statement DA QUA allowlist (INSERT/CREATE/DROP/ALTER/LOCK).

    Cac dang do deu co ten bang la identifier backtick DAU TIEN trong text (allowlist da ep ca hinh
    dang lan viec khong co tien to database), nen lay identifier dau tien la du.
    """
    if not statement.tokens or statement.tokens[0][1] in ("SET", "UNLOCK") or not statement.text:
        return None
    match = _FIRST_IDENT_RE.search(statement.text)
    return match.group(1).replace("``", "`") if match else None


def preflight_dump(path: str | Path, *, server_version: int,
                   expected_tables: Iterable[str] | None = None) -> dict:
    """Spec buoc 3 + allowlist mysqldump (+ tap bang du kien). `findings` rong = qua preflight.

    `expected_tables` (GPT review 10, N2): source contract noi dump CHI gom 4 bang core. Bang nao bi
    tac dong ngoai tap nay -> UNEXPECTED_TABLE; bang du kien ma dump khong co DDL -> MISSING_TABLE_DDL
    (neu khong, mot dump thieu han 1 bang core se import 0 dong ma khong ai bao loi).
    Loi lexer (comment khong dong, versioned comment la, byte khong phai UTF-8, DELIMITER...) -> raise.
    """
    expected = set(expected_tables) if expected_tables is not None else None
    lexer = SqlLexer(capture_text=True, max_capture=_SET_CAPTURE_LIMIT, server_version=server_version)
    findings: list[tuple[str, int, str]] = []
    touched: set[str] = set()
    created: set[str] = set()
    statements = 0
    for statement in _scan(_iter_file_lines(path), lexer):
        statements += 1
        preview = (statement.text or "")[:80].replace("\n", " ")
        label = classify_tokens(statement.tokens)
        if label:
            findings.append((label, statement.offset, preview))
            continue
        problem = dump_statement_problem(statement)
        if problem:
            findings.append((f"NOT_ALLOWLISTED: {problem}", statement.offset, preview))
            continue
        table = statement_table(statement)
        if table is None:
            continue
        touched.add(table)
        if statement.tokens[0][1] == "CREATE":
            created.add(table)
        if expected is not None and table not in expected:
            findings.append((f"UNEXPECTED_TABLE: {table}", statement.offset, preview))
    if expected is not None:
        for table in sorted(expected - created):
            findings.append((f"MISSING_TABLE_DDL: {table}", -1, "dump khong co CREATE TABLE cho bang core nay"))
    return {
        "statements": statements, "findings": findings, "server_version": server_version,
        "tables_touched": sorted(touched), "tables_created": sorted(created),
        "expected_tables": sorted(expected) if expected is not None else None,
    }


def sanitize_setup_sql(sql_text: str, *, server_version: int | None = None) -> list[str]:
    """Bo `CREATE DATABASE`/`USE` khoi setup.sql (muc 3a buoc 6); `DROP DATABASE` thi raise."""
    statements: list[str] = []
    for statement in iter_statements(sql_text, server_version=server_version):
        label = classify_tokens(statement.tokens)
        if label == "DROP DATABASE":
            raise SqlScriptError(
                f"setup.sql chua DROP DATABASE tai offset {statement.offset} - tu choi chay."
            )
        if label in ("CREATE DATABASE", "USE"):
            continue
        statements.append(statement.text)
    if not statements:
        raise SqlScriptError("setup.sql khong con statement nao sau khi sanitize - file rong hay sai?")
    return statements

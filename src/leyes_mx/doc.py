"""Plain-text extraction from Word 97-2003 binary documents (.doc).

The Cámara de Diputados publishes consolidated laws as ``.doc`` files in the
Word 97 binary format ([MS-DOC]). Instead of shelling out to LibreOffice we
read the format directly:

1. The OLE container is opened with :mod:`olefile`.
2. The File Information Block (FIB) gives the length of the main document
   text (``ccpText``), the location of the piece table (``Clx``) and of the
   paragraph property index (``PlcBtePapx``).
3. The piece table maps character positions (CP) to byte offsets (FC) in the
   ``WordDocument`` stream, where text lives either as cp1252 ("compressed")
   or UTF-16LE.
4. Paragraph properties are read only to tell table cells apart from table
   rows (``sprmPFTtp``), so tables survive as rows of cells.

Only the main document story is returned: headers, footers and footnotes are
separate stories in the format and are ignored on purpose (the page header of
every LeyesBiblio file repeats "CÁMARA DE DIPUTADOS ..." on every page).
"""

from __future__ import annotations

import io
import struct
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

import olefile

# Field offsets inside the FIB (see [MS-DOC] 2.5.1).
_FIB_FLAGS = 0x0A
_FIB_CCP_TEXT = 0x4C
_FIB_RGFCLCB = 0x9A
_IDX_PLCF_BTE_PAPX = 13
_IDX_CLX = 33

_SPRM_PF_IN_TABLE = 0x2416
_SPRM_PF_TTP = 0x2417
_SPRM_PF_INNER_TTP = 0x244C
_SPRM_PT_DEF_TABLE = 0xD608
_SPRM_PCHG_TABS = 0xC615

PARAGRAPH = "\r"
CELL = "\x07"


class DocFormatError(ValueError):
    """Raised when a file is not a Word 97 binary document we can read."""


@dataclass
class Piece:
    cp_start: int
    cp_end: int
    fc: int  # byte offset in the WordDocument stream
    compressed: bool

    def fc_of(self, cp: int) -> int:
        step = 1 if self.compressed else 2
        return self.fc + (cp - self.cp_start) * step


@dataclass
class Block:
    """A paragraph of text, or a table (a list of rows, each a list of cells)."""

    text: str = ""
    rows: list[list[str]] | None = None

    @property
    def is_table(self) -> bool:
        return self.rows is not None


@dataclass
class _PapxIndex:
    """Maps FC ranges of paragraph marks to (in_table, row_end) flags."""

    starts: list[int] = field(default_factory=list)
    ends: list[int] = field(default_factory=list)
    flags: list[tuple[bool, bool]] = field(default_factory=list)

    def lookup(self, fc: int) -> tuple[bool, bool] | None:
        i = bisect_right(self.starts, fc) - 1
        if i < 0 or fc >= self.ends[i]:
            return None
        return self.flags[i]


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _read_pieces(table: bytes, fc_clx: int, lcb_clx: int) -> list[Piece]:
    clx = table[fc_clx : fc_clx + lcb_clx]
    pos = 0
    # Skip any Prc (property modifiers) that precede the Pcdt.
    while pos < len(clx) and clx[pos] == 0x01:
        cb = struct.unpack_from("<h", clx, pos + 1)[0]
        pos += 3 + cb
    if pos >= len(clx) or clx[pos] != 0x02:
        raise DocFormatError("piece table (Pcdt) not found")
    lcb = _u32(clx, pos + 1)
    plc = clx[pos + 5 : pos + 5 + lcb]
    n = (lcb - 4) // 12
    cps = [_u32(plc, 4 * i) for i in range(n + 1)]
    pieces = []
    for i in range(n):
        raw_fc = _u32(plc, 4 * (n + 1) + 8 * i + 2)
        compressed = bool(raw_fc & 0x40000000)
        fc = raw_fc & 0x3FFFFFFF
        if compressed:
            fc //= 2
        pieces.append(Piece(cps[i], cps[i + 1], fc, compressed))
    return pieces


def _sprm_operand_size(sprm: int, grpprl: bytes, pos: int) -> int:
    spra = sprm >> 13
    if spra in (0, 1):
        return 1
    if spra in (2, 4, 5):
        return 2
    if spra == 3:
        return 4
    if spra == 7:
        return 3
    # spra == 6: variable length operand.
    if sprm == _SPRM_PT_DEF_TABLE:
        return 2 + _u16(grpprl, pos) - 1
    if sprm == _SPRM_PCHG_TABS and grpprl[pos] == 255:
        # PChgTabsDelClose + PChgTabsAdd, rare in practice.
        n_del = grpprl[pos + 1]
        n_add = grpprl[pos + 2 + 4 * n_del]
        return 1 + 1 + 4 * n_del + 1 + 3 * n_add
    return 1 + grpprl[pos]


def _grpprl_table_flags(grpprl: bytes) -> tuple[bool, bool]:
    """Return (in_table, table_terminating_paragraph) for a paragraph."""
    pos = 0
    in_table = False
    ttp = False
    while pos + 2 <= len(grpprl):
        sprm = _u16(grpprl, pos)
        pos += 2
        if pos >= len(grpprl):
            break
        size = _sprm_operand_size(sprm, grpprl, pos)
        if sprm == _SPRM_PF_IN_TABLE:
            in_table = bool(grpprl[pos])
        elif sprm in (_SPRM_PF_TTP, _SPRM_PF_INNER_TTP):
            ttp = ttp or bool(grpprl[pos])
        pos += size
    return in_table or ttp, ttp


def _read_papx(word: bytes, table: bytes, fc: int, lcb: int) -> _PapxIndex:
    index = _PapxIndex()
    if lcb < 8:
        return index
    plc = table[fc : fc + lcb]
    n = (lcb - 4) // 8
    for i in range(n):
        pn = _u32(plc, 4 * (n + 1) + 4 * i) & 0x003FFFFF
        page = word[pn * 512 : pn * 512 + 512]
        if len(page) < 512:
            continue
        crun = page[511]
        fcs = [_u32(page, 4 * j) for j in range(crun + 1)]
        for j in range(crun):
            b_offset = page[4 * (crun + 1) + 13 * j] * 2
            flags = (False, False)
            if b_offset:
                cb = page[b_offset]
                if cb:
                    start, size = b_offset + 1, 2 * cb - 1
                else:
                    start, size = b_offset + 2, 2 * page[b_offset + 1]
                grpprl_and_istd = page[start : start + size]
                try:
                    flags = _grpprl_table_flags(grpprl_and_istd[2:])
                except (IndexError, struct.error):
                    flags = (False, False)
            index.starts.append(fcs[j])
            index.ends.append(fcs[j + 1])
            index.flags.append(flags)
    order = sorted(range(len(index.starts)), key=index.starts.__getitem__)
    index.starts = [index.starts[k] for k in order]
    index.ends = [index.ends[k] for k in order]
    index.flags = [index.flags[k] for k in order]
    return index


def _clean_char(ch: str) -> str:
    if ch == "\x0b":
        return "\n"  # manual line break inside a paragraph
    if ch in "\x1e":
        return "-"  # non-breaking hyphen
    if ch in "\x1f\x01\x02\x05\x08\x00":
        return ""  # optional hyphen, embedded objects, note references
    if ch in "\t\xa0":
        return " "
    return ch


class _Reader:
    def __init__(self, data: bytes):
        if not olefile.isOleFile(io.BytesIO(data)):
            raise DocFormatError("not an OLE2 compound file (is it a real .doc?)")
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            if not ole.exists("WordDocument"):
                raise DocFormatError("no WordDocument stream")
            self.word = ole.openstream("WordDocument").read()
            if _u16(self.word, 0) != 0xA5EC:
                raise DocFormatError("bad FIB signature")
            flags = _u16(self.word, _FIB_FLAGS)
            if flags & 0x0100:
                raise DocFormatError("document is encrypted")
            table_name = "1Table" if flags & 0x0200 else "0Table"
            if not ole.exists(table_name):
                raise DocFormatError(f"missing {table_name} stream")
            self.table = ole.openstream(table_name).read()
        self.ccp_text = _u32(self.word, _FIB_CCP_TEXT)
        fc_clx = _u32(self.word, _FIB_RGFCLCB + 8 * _IDX_CLX)
        lcb_clx = _u32(self.word, _FIB_RGFCLCB + 8 * _IDX_CLX + 4)
        self.pieces = _read_pieces(self.table, fc_clx, lcb_clx)
        fc_papx = _u32(self.word, _FIB_RGFCLCB + 8 * _IDX_PLCF_BTE_PAPX)
        lcb_papx = _u32(self.word, _FIB_RGFCLCB + 8 * _IDX_PLCF_BTE_PAPX + 4)
        try:
            self.papx = _read_papx(self.word, self.table, fc_papx, lcb_papx)
        except (IndexError, struct.error):
            self.papx = _PapxIndex()

    def chars(self):
        """Yield (character, fc) for the main document story."""
        for piece in self.pieces:
            if piece.cp_start >= self.ccp_text:
                break
            end = min(piece.cp_end, self.ccp_text)
            count = end - piece.cp_start
            if piece.compressed:
                raw = self.word[piece.fc : piece.fc + count]
                text = raw.decode("cp1252", errors="replace")
                for k, ch in enumerate(text):
                    yield ch, piece.fc + k
            else:
                raw = self.word[piece.fc : piece.fc + 2 * count]
                text = raw.decode("utf-16-le", errors="replace")
                for k, ch in enumerate(text):
                    yield ch, piece.fc + 2 * k


def read_blocks(data: bytes) -> list[Block]:
    """Return the main text of a .doc as paragraphs and tables, in order."""
    reader = _Reader(data)
    blocks: list[Block] = []
    buf: list[str] = []
    row: list[str] = []
    rows: list[list[str]] = []
    field_stack: list[bool] = []  # True while inside a field's code part

    def flush_table() -> None:
        nonlocal rows
        if row:
            rows.append(row.copy())
            row.clear()
        if rows:
            blocks.append(Block(rows=rows))
            rows = []

    for ch, fc in reader.chars():
        if ch == "\x13":
            field_stack.append(True)
            continue
        if ch == "\x14":
            if field_stack:
                field_stack[-1] = False
            continue
        if ch == "\x15":
            if field_stack:
                field_stack.pop()
            continue
        if field_stack and field_stack[-1]:
            continue  # field code, e.g. HYPERLINK "..."
        if ch == CELL:
            text = "".join(buf).strip()
            buf.clear()
            props = reader.papx.lookup(fc)
            if props is None:
                # No paragraph properties: guess, an empty paragraph ends a row.
                is_row_end = not text and bool(row)
            else:
                is_row_end = props[1]
            if is_row_end:
                if row:
                    rows.append(row.copy())
                    row.clear()
            else:
                row.append(text)
            continue
        if ch in (PARAGRAPH, "\x0c"):
            props = reader.papx.lookup(fc)
            in_table = props[0] if props is not None else bool(row)
            if in_table and ch == PARAGRAPH:
                buf.append("\n")  # paragraph break inside a table cell
                continue
            if rows or row:
                flush_table()
            blocks.append(Block(text="".join(buf)))
            buf.clear()
            continue
        buf.append(_clean_char(ch))
    if rows or row:
        flush_table()
    if buf:
        blocks.append(Block(text="".join(buf)))
    return blocks


def read_blocks_from_path(path: str | Path) -> list[Block]:
    return read_blocks(Path(path).read_bytes())

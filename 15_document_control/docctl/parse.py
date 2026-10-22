"""Parsers (s5): every format to the same structure: a header block, numbered sections with
their lines, tables kept whole, and where each line came from (page, and for scans the
OCR confidence and region).

    native PDF      text read with pypdf, page by page
    scan            an OCR layout result (the shape Document Intelligence returns): lines with
                    a confidence each, tables as cells. The stand-in for OCR itself.
    HTML            headings, paragraphs and tables; navigation and footer boilerplate dropped
    email           the message without its quoted history; attachments returned to intake
                    as documents of their own
    spreadsheet     per sheet: label and value rows as the header, other sheets as tables

A heading is recognised in the three styles the sources use: "1. PURPOSE", "1 Purpose"
and a bare "Purpose" (short, title case, no colon, no full stop).
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser


@dataclass(frozen=True)
class Line:
    text: str
    page: int = 1
    confidence: float | None = None          # OCR confidence; None for born-digital text
    region: tuple[float, ...] | None = None  # OCR polygon, inches


@dataclass
class Table:
    caption: str
    header: list[str]
    rows: list[list[str]]
    page: int = 1


@dataclass
class Section:
    number: int
    heading: str
    lines: list[Line] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    @property
    def path(self) -> str:
        return f"{self.number} {self.heading}"


@dataclass
class Attachment:
    name: str
    mime: str
    data: bytes


@dataclass
class Parsed:
    fmt: str
    kind_line: str                           # "STANDARD OPERATING PROCEDURE", when the document says
    title: str
    header: list[Line]
    sections: list[Section]
    attachments: list[Attachment] = field(default_factory=list)

    def text(self) -> str:
        """All of the document's text, in reading order: what the content hash is taken over."""
        out = [self.kind_line, self.title] + [x.text for x in self.header]
        for s in self.sections:
            out.append(s.heading)
            out += [x.text for x in s.lines]
            for t in s.tables:
                out += [" | ".join(t.header)] + [" | ".join(r) for r in t.rows]
        return "\n".join(x for x in out if x)

    def all_lines(self) -> list[Line]:
        return self.header + [x for s in self.sections for x in s.lines]


class ParseError(ValueError):
    pass


KIND_LINES = {"STANDARD OPERATING PROCEDURE", "MATERIAL SPECIFICATION", "DEVIATION REPORT", "CHANGE CONTROL",
              "COMPLAINT SUMMARY"}
H_NUMBERED = re.compile(r"^(\d{1,2})\.\s+([A-Z][A-Z &/-]+)$")
H_DECIMAL = re.compile(r"^(\d{1,2})\s+([A-Z][a-z][A-Za-z &/-]*)$")
H_PLAIN = re.compile(r"^[A-Z][a-z]+(?: [a-zA-Z]+){0,3}$")
COLUMNS = re.compile(r"\S(?:.*?\S)?(?=\s{2,}|$)")


def _heading(text: str) -> str | None:
    t = text.strip()
    if m := H_NUMBERED.match(t):
        return m.group(2).capitalize()      # "REVISION HISTORY" reads as the other styles do
    if m := H_DECIMAL.match(t):
        return m.group(2)
    if H_PLAIN.match(t) and ":" not in t and not t.endswith("."):
        return t
    return None


def _cells(text: str) -> list[str] | None:
    if "  " not in text.strip():
        return None
    parts = [p.strip() for p in re.split(r"\s{2,}", text.strip()) if p.strip()]
    return parts if len(parts) >= 2 else None


def _from_lines(fmt: str, lines: list[Line], ocr_tables: list[dict] | None = None) -> Parsed:
    """Text lines (from a PDF or OCR) to header block and sections."""
    i = 0
    kind = ""
    if lines and lines[0].text.strip().upper() in KIND_LINES:
        kind, i = lines[0].text.strip(), 1
    if i >= len(lines):
        raise ParseError("no text")
    title = lines[i].text.strip()
    i += 1
    header = []
    while i < len(lines) and ":" in lines[i].text and _heading(lines[i].text) is None:
        header.append(lines[i])
        i += 1
    sections: list[Section] = []
    current: Section | None = None
    pending_table: list[tuple[Line, list[str]]] = []

    def flush_table():
        nonlocal pending_table
        if current is not None and len(pending_table) >= 2:
            head, *rows = pending_table
            current.tables.append(Table(current.heading, head[1], [r for _, r in rows], head[0].page))
        elif current is not None:
            current.lines += [ln for ln, _ in pending_table]
        pending_table = []

    for ln in lines[i:]:
        if not ln.text.strip():
            flush_table()
            continue
        h = _heading(ln.text)
        if h is not None:
            flush_table()
            current = Section(len(sections) + 1, h)
            sections.append(current)
            continue
        if current is None:                    # text before any heading belongs to the header
            header.append(ln)
            continue
        cells = _cells(ln.text)
        if cells is not None:
            pending_table.append((ln, cells))
            continue
        flush_table()
        current.lines.append(ln)
    flush_table()
    if ocr_tables:                             # the layout service's tables replace the text guess
        for t in ocr_tables:
            sec = next((s for s in sections if s.heading == t["caption"]), None)
            if sec is None:
                continue
            grid = [[""] * t["columnCount"] for _ in range(t["rowCount"])]
            for c in t["cells"]:
                grid[c["rowIndex"]][c["columnIndex"]] = c["content"]
            sec.tables = [Table(t["caption"], grid[0], grid[1:], t["boundingRegions"][0]["pageNumber"])]
    return Parsed(fmt, kind, title, header, sections)


def parse_pdf(data: bytes) -> Parsed:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        lines = [Line(t, n) for n, page in enumerate(reader.pages, 1)
                 for t in (page.extract_text() or "").split("\n")]
    except Exception as e:  # a corrupt file is a parse failure, not a crash
        raise ParseError(f"pdf: {type(e).__name__}: {e}") from None
    while lines and not lines[-1].text.strip():
        lines.pop()
    return _from_lines("pdf", lines)


def parse_scan(data: bytes) -> Parsed:
    try:
        d = json.loads(data)
        lines = []
        for p in d["pages"]:
            prev_y = None
            for ln in p["lines"]:
                y = ln["polygon"][1]
                if prev_y is not None and y - prev_y > 0.3:     # a gap on the page is a blank line
                    lines.append(Line("", p["pageNumber"]))
                lines.append(Line(ln["content"], p["pageNumber"], ln["confidence"], tuple(ln["polygon"])))
                prev_y = y
    except (KeyError, ValueError, TypeError) as e:
        raise ParseError(f"scan: not a layout result: {e}") from None
    return _from_lines("scan", lines, d.get("tables"))


class _Html(HTMLParser):
    SKIP = {"nav", "footer", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.skip = [], 0
        self.kind, self.title, self.dt = "", "", None
        self.header: list[Line] = []
        self.sections: list[Section] = []
        self.row: list[str] | None = None
        self.table: list[list[str]] | None = None
        self.buf = ""

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        self.stack.append(tag)
        if tag == "table":
            self.table = []
        elif tag == "tr":
            self.row = []
        self.buf = ""

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        if tag in self.SKIP:
            self.skip -= 1
            return
        text, self.buf = self.buf.strip(), ""
        if self.skip:
            return
        if tag == "h1":
            self.title = text
        elif tag == "h2":
            self.sections.append(Section(len(self.sections) + 1, text))
        elif tag == "dt":
            self.dt = text
        elif tag == "dd" and self.dt is not None:
            self.header.append(Line(f"{self.dt}: {text}"))
            self.dt = None
        elif tag == "p" and text:
            if "kind" in self.stack or not self.title and text.upper() in KIND_LINES:
                self.kind = text
            elif self.sections:
                self.sections[-1].lines.append(Line(text))
            elif text.upper() in KIND_LINES:
                self.kind = text
        elif tag in ("td", "th") and self.row is not None:
            self.row.append(text)
        elif tag == "tr" and self.table is not None and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table and self.sections:
            self.sections[-1].tables.append(Table(self.sections[-1].heading, self.table[0], self.table[1:]))
            self.table = None

    def handle_data(self, data):
        self.buf += data


def parse_html(data: bytes) -> Parsed:
    p = _Html()
    try:
        p.feed(data.decode("utf-8"))
    except UnicodeDecodeError as e:
        raise ParseError(f"html: {e}") from None
    if not p.title:
        raise ParseError("html: no title")
    return Parsed("html", p.kind, p.title, p.header, p.sections)


QUOTE_INTRO = re.compile(r"^On .+ wrote:$")


def parse_eml(data: bytes) -> Parsed:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    if msg["From"] is None:
        raise ParseError("email: no From header")
    body = msg.get_body(preferencelist=("plain",))
    text = body.get_content() if body else ""
    kept = []
    for line in text.splitlines():
        if QUOTE_INTRO.match(line.strip()):    # quoted history starts here: drop the rest
            break
        if line.startswith(">"):
            continue
        kept.append(line)
    atts = [Attachment(part.get_filename(), part.get_content_type(), part.get_content())
            for part in msg.iter_attachments()]
    sec = Section(1, "Message", [Line(x) for x in kept if x.strip()])
    return Parsed("eml", "", str(msg["Subject"]), [Line(f"From: {msg['From']}"), Line(f"Subject: {msg['Subject']}")],
                  [sec], atts)


def parse_xlsx(data: bytes) -> Parsed:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ParseError(f"xlsx: {type(e).__name__}: {e}") from None
    title, header, sections = "", [], []
    first = wb.worksheets[0]
    rows = [[("" if c is None else str(c)) for c in r] for r in first.iter_rows(values_only=True)]
    i = 0
    for r in rows:                            # label and value rows until the first blank row
        i += 1
        if not any(r):
            break
        if r[0] == "Title":
            title = r[1]
        else:
            header.append(Line(f"{r[0]}: {r[1]}", 1))
    for r in rows[i:]:
        if any(r):
            sections.append(Section(len(sections) + 1, r[0], [Line(r[1], 1)] if len(r) > 1 and r[1] else []))
    for ws in wb.worksheets[1:]:
        grid = [[("" if c is None else str(c)) for c in r] for r in ws.iter_rows(values_only=True)]
        grid = [r for r in grid if any(r)]
        if grid:
            sections.append(Section(len(sections) + 1, ws.title, [], [Table(ws.title, grid[0], grid[1:], 1)]))
    if not title:
        raise ParseError("xlsx: no title row")
    return Parsed("xlsx", "", title, header, sections)


PARSERS = {"application/pdf": parse_pdf, "application/vnd.ocr-layout+json": parse_scan, "text/html": parse_html,
           "message/rfc822": parse_eml,
           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": parse_xlsx}


def parse(mime: str, data: bytes) -> Parsed:
    if mime not in PARSERS:
        raise ParseError(f"no parser for {mime}")
    return PARSERS[mime](data)

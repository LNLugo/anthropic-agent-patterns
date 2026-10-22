"""A small spreadsheet writer for the fixtures: byte-for-byte reproducible.

openpyxl writes its XML through lxml when lxml is installed and through the standard
library when it is not, so the same workbook comes out as different bytes on different
machines. This writes the minimum a reader needs (workbook, sheets, inline strings) by
hand, with fixed timestamps. openpyxl still reads the files back.
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet(rows: list[list]) -> str:
    out = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{NS}"><sheetData>']
    for i, row in enumerate(rows, 1):
        cells = "".join(f'<c r="{_col(j)}{i}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'
                        for j, v in enumerate(row) if v not in (None, ""))
        out.append(f'<row r="{i}">{cells}</row>')
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write_xlsx(sheets: list[tuple[str, list[list]]]) -> bytes:
    """sheets: (name, rows) in order; an empty row is written as an empty row."""
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                      'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                      for i in range(1, len(sheets) + 1)) + "</Types>",
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>'
            + "".join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
                      for i, (name, _) in enumerate(sheets, 1)) + "</sheets></workbook>",
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                      for i in range(1, len(sheets) + 1)) + "</Relationships>",
    }
    for i, (_, rows) in enumerate(sheets, 1):
        files[f"xl/worksheets/sheet{i}.xml"] = _sheet(rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), body.encode("utf-8"),
                       compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()

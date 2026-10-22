"""A small PDF writer for the fixtures: text pages in Courier, byte-for-byte reproducible.

Real PDF libraries stamp a creation date and a random document id, so the same input
writes different bytes and a fixture test cannot compare them. This writes PDF 1.4 by
hand: one font, one text object per page, nothing that varies between runs. Courier is
monospaced, so table columns written with spaces come back out of pypdf aligned.
"""

from __future__ import annotations

LINES_PER_PAGE = 48


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(lines: list[str]) -> bytes:
    """lines: the text, one string per line; a line "\f" starts a new page."""
    pages, current = [], []
    for line in lines:
        if line == "\f" or len(current) == LINES_PER_PAGE:
            pages.append(current)
            current = []
            if line == "\f":
                continue
        current.append(line)
    pages.append(current)

    objects: list[bytes] = []                       # object n is objects[n - 1]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")                             # the page tree, filled in below
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    kids = []
    for page in pages:
        text = ["BT", "/F1 10 Tf", "12 TL", "50 760 Td"]
        for line in page:
            text.append(f"({_escape(line)}) Tj T*")
        text.append("ET")
        stream = "\n".join(text).encode("cp1252", errors="replace")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_id = len(objects)
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                       b"/Contents %d 0 R >>" % content_id)
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)

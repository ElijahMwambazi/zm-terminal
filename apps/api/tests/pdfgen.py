"""Tiny offline PDF writer for tests: positions each 2-space-separated field of a line at fixed x
offsets so the real text-layer extraction (pdfplumber) is exercised without committing binaries."""

import re


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(lines: list[str], *, text: bool = True) -> bytes:
    ops = []
    y = 1100
    for line in lines:
        fields = [f for f in re.split(r"\s{2,}", line.strip()) if f] if line.strip() else []
        x = 30.0
        for field in fields:
            ops.append(f"BT /F1 9 Tf {x:.1f} {y} Td ({_esc(field)}) Tj ET")
            x += 5.2 * len(field) + 24  # generous width estimate: fields never touch (gap > 8pt)
        y -= 14
    stream = "\n".join(ops).encode("latin-1") if text else b"0 0 m 10 10 l S"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 2000 1200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)

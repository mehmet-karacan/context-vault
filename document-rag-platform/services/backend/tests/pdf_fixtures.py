"""Shared PDF fixtures for parser tests.

``build_pdf`` writes a minimal, structurally valid PDF (correct xref table)
with no third-party generator dependency (reportlab is not installed in the
test venv). Each page's text is controllable; an empty string yields a blank
page. This lets tests exercise digital / scanned / mixed PDFs and assert
accurate per-page locators.
"""


def build_pdf(pages):
    """Builds a minimal valid multi-page PDF byte string.

    ``pages`` is a list of str; empty string means a blank page.
    """
    n = len(pages)
    first_page = 3
    first_content = 3 + n
    font_obj = 3 + 2 * n
    last_obj = font_obj

    def esc(text):
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content_bodies = []
    for text in pages:
        if text:
            stream = "BT /F1 12 Tf 72 720 Td (" + esc(text) + ") Tj ET"
        else:
            stream = ""
        content_bodies.append(
            "<< /Length %d >>\nstream\n%s\nendstream"
            % (len(stream.encode("latin-1")), stream)
        )

    out = bytearray()
    out.extend(b"%PDF-1.4\n")
    offsets = {}

    def add(obj_num, body):
        offsets[obj_num] = len(out)
        out.extend(("%d 0 obj\n" % obj_num).encode("latin-1"))
        out.extend(body.encode("latin-1"))
        out.extend(b"\nendobj\n")

    add(1, "<< /Type /Catalog /Pages 2 0 R >>")
    page_refs = " ".join("%d 0 R" % (first_page + i) for i in range(n))
    add(2, "<< /Type /Pages /Kids [%s] /Count %d >>" % (page_refs, n))
    for i in range(n):
        body = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>"
            % (first_content + i, font_obj)
        )
        add(first_page + i, body)
    for i in range(n):
        add(first_content + i, content_bodies[i])
    add(font_obj, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    xref = len(out)
    out.extend(("xref\n0 %d\n" % (last_obj + 1)).encode("latin-1"))
    out.extend(b"0000000000 65535 f \n")
    for num in range(1, last_obj + 1):
        out.extend(("%010d 00000 n \n" % offsets[num]).encode("latin-1"))
    out.extend(
        ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n" % (last_obj + 1, xref)).encode(
            "latin-1"
        )
    )
    out.extend(b"%%EOF\n")
    return bytes(out)

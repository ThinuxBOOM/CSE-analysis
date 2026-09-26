"""
Test support: a minimal, standard-library PDF writer for F4 trust-gate tests.

Builds one-page PDFs with Helvetica text (visible, or invisible: text render mode
3, which is how OCR engines lay their text over a scan) and Flate-compressed
image XObjects (opaque, split into strips, or carrying a soft mask), so the REAL
Poppler tools (pdftotext -bbox-layout, pdfimages -list, pdftocairo) see real
geometry. Files are written only to pytest's tmp_path.
"""
import zlib


def _esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def statement_text(x0=60, top=80):
    """A small financial statement (heading, scale, dated columns, value rows) as
    (x, y_top, size, text) runs in top-left page coordinates."""
    rows = [
        (x0, top, 12, "STATEMENT OF PROFIT OR LOSS"),
        (x0, top + 18, 9, "Rs.'000"),
        (x0 + 180, top + 36, 9, "For the year ended"),
        (x0 + 250, top + 50, 9, "31.03.2026"),
        (x0 + 350, top + 50, 9, "31.03.2025"),
    ]
    body = [("Revenue", "62,085", "58,001"), ("Cost of sales", "(40,000)", "(38,000)"),
            ("Gross profit", "22,085", "20,001"), ("Profit before tax", "14,616", "13,000"),
            ("Profit for the year", "10,616", "10,000")]
    for n, (label, a, b) in enumerate(body):
        y = top + 70 + 16 * n
        rows += [(x0, y, 9, label), (x0 + 300 - 5 * len(a), y, 9, a), (x0 + 400 - 5 * len(b), y, 9, b)]
    return rows


def make_pdf(path, *, width=612.0, height=792.0, texts=(), text_mode=0, images=(), text_first=False):
    """images: dicts with x, y (top-left, points), w, h (points), px (pixel width),
    py (pixel height), value (0-255 grey), rgb (bool), smask (None | 'transparent' |
    'opaque' | 'partial'). Paint order: images then text (OCR layout), or text then
    images when text_first (an overlay drawn on top of visible text)."""
    objs = {}
    n_font = 3
    objs[n_font] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    xobjects, next_id = [], 4
    for k, im in enumerate(images):
        comps = 3 if im.get("rgb") else 1
        data = zlib.compress(bytes([im.get("value", 225)]) * (im["px"] * im["py"] * comps))
        smask_ref = b""
        if im.get("smask"):
            level = {"transparent": 0, "opaque": 255, "partial": 128}[im["smask"]]
            mdata = zlib.compress(bytes([level]) * (im["px"] * im["py"]))
            objs[next_id] = (b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
                             b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n" % (im["px"], im["py"], len(mdata))
                             + mdata + b"\nendstream")
            smask_ref = b" /SMask %d 0 R" % next_id
            next_id += 1
        cs = b"/DeviceRGB" if comps == 3 else b"/DeviceGray"
        objs[next_id] = (b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s /BitsPerComponent 8"
                         b" /Filter /FlateDecode%s /Length %d >>\nstream\n" % (im["px"], im["py"], cs, smask_ref, len(data))
                         + data + b"\nendstream")
        xobjects.append((f"Im{k}", next_id, im))
        next_id += 1
    ops_img = []
    for name, _, im in xobjects:
        y_pdf = height - im["y"] - im["h"]
        ops_img.append(f"q {im['w']:.2f} 0 0 {im['h']:.2f} {im['x']:.2f} {y_pdf:.2f} cm /{name} Do Q")
    ops_txt = []
    for run in texts:                               # (x, y_top, size, text[, render_mode])
        x, y_top, size, s = run[:4]
        mode = run[4] if len(run) > 4 else text_mode
        ops_txt.append(f"BT /F1 {size} Tf {mode} Tr 1 0 0 1 {x:.2f} {height - y_top - size:.2f} Tm ({_esc(s)}) Tj ET")
    content = "\n".join(ops_txt + ops_img if text_first else ops_img + ops_txt).encode("latin-1")
    n_content = next_id
    objs[n_content] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
    xo = b" ".join(b"/%s %d 0 R" % (name.encode(), oid) for name, oid, _ in xobjects)
    n_page = n_content + 1
    objs[n_page] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 %d 0 R >> /XObject << %s >> >> >>" % (width, height, n_content, n_font, xo))
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % n_page
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for oid in sorted(objs):
        offsets[oid] = len(out)
        out += b"%d 0 obj\n" % oid + objs[oid] + b"\nendobj\n"
    xref = len(out)
    count = max(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % count
    for oid in range(1, count):
        out += b"%010d 00000 n \n" % offsets[oid]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (count, xref)
    with open(path, "wb") as f:
        f.write(bytes(out))
    return path

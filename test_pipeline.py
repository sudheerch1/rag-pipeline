"""
End-to-end test: detect → parse → chunk
Tests every supported format with realistic sample content.
"""
import sys, os, io, json, csv as csv_mod, datetime
sys.path.insert(0, os.path.dirname(__file__))

from models import ParsedBlock, BlockType
from parsers import parse
from chunker import chunk, count_tokens, MAX_TOKENS


# ── Sample file generators ────────────────────────────────────────────────────

def make_pdf_bytes() -> bytes:
    """Minimal valid PDF with two pages of text."""
    content = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R 4 0 R]/Count 2>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 5 0 R/Resources<</Font<</F1 6 0 R>>>>>>endobj
4 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 7 0 R/Resources<</Font<</F1 6 0 R>>>>>>endobj
5 0 obj<</Length 120>>
stream
BT /F1 12 Tf 50 750 Td
(Introduction to Machine Learning) Tj 0 -20 Td
(Machine learning is a subset of artificial intelligence.) Tj 0 -20 Td
(It enables systems to learn from data automatically.) Tj
ET
endstream
endobj
6 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
7 0 obj<</Length 100>>
stream
BT /F1 12 Tf 50 750 Td
(Chapter 2: Neural Networks) Tj 0 -20 Td
(Neural networks are inspired by the human brain.) Tj 0 -20 Td
(They consist of layers of interconnected nodes.) Tj
ET
endstream
endobj
xref
0 8
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000274 00000 n
0000000433 00000 n
0000000607 00000 n
0000000673 00000 n
trailer<</Size 8/Root 1 0 R>>
startxref
827
%%EOF"""
    return content


def make_docx_bytes() -> bytes:
    """Real DOCX with headings, paragraphs, and a table."""
    from docx import Document
    doc = Document()
    doc.add_heading("Executive Summary", level=1)
    doc.add_paragraph(
        "This report covers the quarterly performance of all business units. "
        "Revenue grew by 12% year-over-year driven by strong enterprise sales."
    )
    doc.add_heading("Financial Results", level=2)
    doc.add_paragraph(
        "Total revenue reached $4.2M in Q3. Operating costs were held flat at $2.8M, "
        "resulting in an operating margin of 33%."
    )
    table = doc.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "Business Unit"
    table.cell(0, 1).text = "Revenue ($M)"
    table.cell(0, 2).text = "Growth %"
    table.cell(1, 0).text = "Enterprise"
    table.cell(1, 1).text = "2.4"
    table.cell(1, 2).text = "18%"
    table.cell(2, 0).text = "SMB"
    table.cell(2, 1).text = "1.8"
    table.cell(2, 2).text = "5%"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_xlsx_bytes() -> bytes:
    """XLSX with two sheets."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Sales Data"
    ws1.append(["Region", "Product", "Units Sold", "Revenue"])
    ws1.append(["North", "Widget A", 1200, 48000])
    ws1.append(["South", "Widget B", 850,  34000])
    ws1.append(["West",  "Widget A", 1500, 60000])
    ws2 = wb.create_sheet("Targets")
    ws2.append(["Region", "Q4 Target"])
    ws2.append(["North", 55000])
    ws2.append(["South", 40000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_csv_bytes() -> bytes:
    buf = io.StringIO()
    w = csv_mod.writer(buf)
    w.writerow(["employee_id", "name", "department", "salary", "start_date"])
    w.writerow(["E001", "Alice Johnson", "Engineering", "120000", "2021-03-15"])
    w.writerow(["E002", "Bob Smith",     "Sales",       "85000",  "2019-07-22"])
    w.writerow(["E003", "Carol White",   "Engineering", "130000", "2020-11-01"])
    w.writerow(["E004", "David Brown",   "HR",          "75000",  "2022-01-10"])
    return buf.getvalue().encode()


def make_pptx_bytes() -> bytes:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    blank_layout = prs.slide_layouts[1]

    slide1 = prs.slides.add_slide(blank_layout)
    slide1.shapes.title.text = "Q3 Business Review"
    slide1.placeholders[1].text = (
        "Revenue up 12% YoY\n"
        "Customer retention at 94%\n"
        "Launched 3 new product features"
    )
    notes1 = slide1.notes_slide.notes_text_frame
    notes1.text = "Presenter note: Emphasise the retention improvement over last quarter."

    slide2 = prs.slides.add_slide(blank_layout)
    slide2.shapes.title.text = "Product Roadmap"
    slide2.placeholders[1].text = (
        "H1: Mobile app redesign\n"
        "H2: API v3 launch\n"
        "H2: Enterprise SSO integration"
    )
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def make_html_bytes() -> bytes:
    return b"""<!DOCTYPE html><html><body>
<h1>Company Knowledge Base</h1>
<h2>Onboarding Process</h2>
<p>New employees should complete the onboarding checklist within their first week.
This includes setting up company accounts, completing compliance training, and
meeting with their assigned buddy.</p>
<h2>IT Setup</h2>
<ul>
  <li>Request laptop from IT via the ticketing system</li>
  <li>Set up VPN access using the credentials sent to your email</li>
  <li>Install required software from the company portal</li>
</ul>
<table>
  <tr><th>System</th><th>Access Level</th></tr>
  <tr><td>GitHub</td><td>All engineers</td></tr>
  <tr><td>Salesforce</td><td>Sales team only</td></tr>
</table>
</body></html>"""


def make_json_bytes() -> bytes:
    data = [
        {"product_id": "P001", "name": "Widget Pro", "category": "Hardware",
         "price": 299.99, "in_stock": True, "description": "Professional grade widget for enterprise use"},
        {"product_id": "P002", "name": "Connector Kit", "category": "Accessories",
         "price": 49.99, "in_stock": False, "description": "Universal connector kit compatible with all Widget models"},
    ]
    return json.dumps(data, indent=2).encode()


def make_text_bytes() -> bytes:
    return b"""Introduction

Artificial intelligence has transformed the way businesses operate. From automating
repetitive tasks to providing deep analytical insights, AI systems have become
indispensable across industries.

Key Applications

In healthcare, AI assists in diagnosing diseases from medical imaging with accuracy
that rivals experienced physicians. In finance, algorithmic trading systems process
market signals in microseconds. In logistics, route optimisation saves billions in
fuel and time annually.

Challenges and Considerations

Despite the promise, AI adoption faces significant barriers. Data quality remains
the biggest hurdle: models are only as good as the data they are trained on.
Regulatory uncertainty, explainability requirements, and workforce concerns are
all areas that organisations must navigate carefully."""


# ── Runner ────────────────────────────────────────────────────────────────────

FORMATS = [
    ("pdf",      make_pdf_bytes),
    ("docx",     make_docx_bytes),
    ("xlsx",     make_xlsx_bytes),
    ("csv",      make_csv_bytes),
    ("pptx",     make_pptx_bytes),
    ("html",     make_html_bytes),
    ("json",     make_json_bytes),
    ("text",     make_text_bytes),
]


def run():
    now = datetime.datetime.utcnow().isoformat()
    total_chunks = 0

    print("=" * 72)
    print("RAG INGESTION PIPELINE — Parse + Chunk End-to-End Test")
    print("=" * 72)

    for fmt, make_fn in FORMATS:
        print(f"\n{'─' * 72}")
        print(f"FORMAT: {fmt.upper()}")
        print(f"{'─' * 72}")

        try:
            file_bytes = make_fn()
        except Exception as e:
            print(f"  [SKIP] Could not generate sample: {e}")
            continue

        # ── Parse ──────────────────────────────────────────────────────────
        blocks = parse(file_bytes, fmt)

        # Attach provenance metadata (normally done by orchestrator)
        for b in blocks:
            b.source_key = f"s3://my-bucket/docs/sample.{fmt}"
            b.source_fmt = fmt
            b.timestamp  = now

        print(f"  Parsed blocks : {len(blocks)}")
        for i, b in enumerate(blocks[:3]):  # preview first 3
            preview = b.text[:80].replace("\n", " ")
            print(f"  Block[{i}] type={b.block_type.value:<10} page={b.page} "
                  f"section={str(b.section)[:20]!r}")
            print(f"          text={preview!r}")

        if len(blocks) > 3:
            print(f"  ... ({len(blocks) - 3} more blocks)")

        # ── Chunk ──────────────────────────────────────────────────────────
        chunks = chunk(blocks, fmt)
        total_chunks += len(chunks)
        token_counts = [c.token_count for c in chunks]
        avg_tokens   = sum(token_counts) / len(token_counts) if token_counts else 0
        max_tok      = max(token_counts) if token_counts else 0

        print(f"\n  Chunks        : {len(chunks)}")
        print(f"  Avg tokens    : {avg_tokens:.0f}   Max: {max_tok}   Limit: {MAX_TOKENS}")

        # Validate: no chunk exceeds MAX_TOKENS
        violations = [c for c in chunks if c.token_count > MAX_TOKENS]
        if violations:
            print(f"  [WARN] {len(violations)} chunk(s) exceed MAX_TOKENS — check splitter")
        else:
            print(f"  [OK]  All chunks within token limit")

        print(f"\n  Sample chunk[0]:")
        if chunks:
            c0 = chunks[0]
            print(f"    tokens  = {c0.token_count}")
            print(f"    section = {c0.section!r}")
            print(f"    page    = {c0.page}")
            print(f"    type    = {c0.block_type}")
            print(f"    text    = {c0.text[:120].replace(chr(10), ' ')!r}")

    print(f"\n{'=' * 72}")
    print(f"TOTAL CHUNKS PRODUCED: {total_chunks}")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    run()
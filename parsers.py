"""
Format-specific parsers.
Each parser: bytes -> list[ParsedBlock]
No parser knows about chunking. No parser knows about other parsers.
"""
import io
import csv as csv_mod
import json
import re
from typing import List

from models import ParsedBlock, BlockType


# ── PDF ──────────────────────────────────────────────────────────────────────

def parse_pdf(file_bytes: bytes) -> List[ParsedBlock]:
    import pypdf
    import re

    blocks = []
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    current_section = ""

    # Patterns to skip — journal headers, page numbers, case citations
    SKIP_PATTERNS = re.compile(
        r'^(Page \d|CONNECTICUT LAW|June \d|January \d|February \d|'
        r'March \d|April \d|May \d|July \d|August \d|September \d|'
        r'October \d|November \d|December \d|\d+ Conn\.|'
        r'Johnson\s*v\.\s*Superior)',
        re.IGNORECASE
    )

    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if not text.strip():
            continue

        lines = [l.strip() for l in text.split('\n') if l.strip()]
        current_para_lines = []

        def flush_paragraph():
            nonlocal current_para_lines
            if not current_para_lines:
                return
            para_text = ' '.join(current_para_lines)
            if len(para_text.split()) >= 10:
                blocks.append(ParsedBlock(
                    text       = para_text,
                    block_type = BlockType.TEXT,
                    page       = i + 1,
                    section    = current_section,
                ))
            current_para_lines = []

        for line in lines:
            words = line.split()
            word_count = len(words)

            # Skip journal headers and page numbers
            if SKIP_PATTERNS.match(line):
                flush_paragraph()
                continue

            # Short line — potential heading or noise
            if word_count <= 5:
                # Check if it looks like a heading
                is_titlecase = line.istitle()
                is_uppercase = line.isupper()
                has_no_digits = not any(c.isdigit() for c in line)

                if (is_titlecase or is_uppercase) and has_no_digits:
                    flush_paragraph()
                    current_section = line
                    blocks.append(ParsedBlock(
                        text       = line,
                        block_type = BlockType.HEADING,
                        page       = i + 1,
                        section    = current_section,
                    ))
                else:
                    # Short line but not a heading — skip noise
                    # e.g. "(SC 21074)", "NUMBER NINETEEN"
                    flush_paragraph()
                continue

            # Long line — paragraph content
            current_para_lines.append(line)

        # Flush remaining paragraph at end of page
        flush_paragraph()

    return blocks

# ── DOCX ─────────────────────────────────────────────────────────────────────

def parse_docx(file_bytes: bytes) -> List[ParsedBlock]:
    """
    Walks paragraphs and tables in document order.
    Detects headings via style name OR bold short lines.
    page = simulated page number (every 40 paragraphs = 1 page).
    paragraph = sequential paragraph index within document.
    """
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    blocks: List[ParsedBlock] = []
    current_section = ""
    para_index = 0
    PARAS_PER_PAGE = 40  # approximate simulation

    body = doc.element.body
    for child in body:
        tag = child.tag.split('}')[-1]

        if tag == 'p':
            from docx.text.paragraph import Paragraph
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue

            style_name = para.style.name if para.style else ""
            simulated_page = (para_index // PARAS_PER_PAGE) + 1

            # Detect heading: by style OR bold short line (<=8 words)
            is_heading_style = style_name.startswith("Heading")
            is_bold_short = (
                len(text.split()) <= 8 and
                para.runs and
                all(run.bold for run in para.runs if run.text.strip())
            )

            if is_heading_style or is_bold_short:
                current_section = text
                blocks.append(ParsedBlock(
                    text=text,
                    block_type=BlockType.HEADING,
                    page=simulated_page,
                    section=current_section,
                    row_index=para_index,
                ))
            elif style_name in ("List Paragraph", "List Bullet", "List Number"):
                blocks.append(ParsedBlock(
                    text=text,
                    block_type=BlockType.LIST_ITEM,
                    page=simulated_page,
                    section=current_section,
                    row_index=para_index,
                ))
            else:
                blocks.append(ParsedBlock(
                    text=text,
                    block_type=BlockType.TEXT,
                    page=simulated_page,
                    section=current_section,
                    row_index=para_index,
                ))
            para_index += 1

        elif tag == 'tbl':
            from docx.table import Table
            table = Table(child, doc)
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows.append(" | ".join(cells))
            table_text = "\n".join(rows)
            if table_text.strip():
                simulated_page = (para_index // PARAS_PER_PAGE) + 1
                blocks.append(ParsedBlock(
                    text=table_text,
                    block_type=BlockType.TABLE,
                    page=simulated_page,
                    section=current_section,
                    row_index=para_index,
                ))
            para_index += 1

    return blocks
# ── XLSX ─────────────────────────────────────────────────────────────────────

def parse_xlsx(file_bytes: bytes) -> List[ParsedBlock]:
    """
    Each worksheet becomes its own block with the sheet name as section.
    Rows are serialised as pipe-delimited text.
    Empty sheets are skipped.
    """
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    blocks: List[ParsedBlock] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            cells = [str(c) if c is not None else "" for c in row]
            if any(c.strip() for c in cells):
                rows.append(" | ".join(cells))

        if rows:
            blocks.append(ParsedBlock(
                text="\n".join(rows),
                block_type=BlockType.TABLE,
                section=sheet_name,
            ))

    wb.close()
    return blocks


# ── CSV ──────────────────────────────────────────────────────────────────────

def parse_csv(file_bytes: bytes) -> List[ParsedBlock]:
    """
    Sniffs delimiter automatically. Converts each row to a prose sentence
    using the header as context — much better for embedding than raw CSV text.
    Batches rows into blocks of 50 to avoid single-block bloat.
    """
    ROW_BATCH = 50

    text = file_bytes.decode("utf-8", errors="replace")
    dialect = csv_mod.Sniffer().sniff(text[:4096], delimiters=",\t|;")
    reader = csv_mod.DictReader(io.StringIO(text), dialect=dialect)

    blocks: List[ParsedBlock] = []
    batch: List[str] = []

    for i, row in enumerate(reader):
        parts = [f"{k}: {v}" for k, v in row.items() if v and v.strip()]
        sentence = "; ".join(parts)
        if sentence:
            batch.append(sentence)

        if len(batch) >= ROW_BATCH:
            blocks.append(ParsedBlock(
                text="\n".join(batch),
                block_type=BlockType.TABLE,
                row_index=i - ROW_BATCH + 1,
            ))
            batch = []

    if batch:
        blocks.append(ParsedBlock(
            text="\n".join(batch),
            block_type=BlockType.TABLE,
            row_index=len(batch),
        ))

    return blocks


# ── PPTX ─────────────────────────────────────────────────────────────────────

def parse_pptx(file_bytes: bytes) -> List[ParsedBlock]:
    """
    Each slide becomes its own block. Title is captured as section context.
    Speaker notes are extracted as a separate TEXT block per slide.
    """
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation(io.BytesIO(file_bytes))
    blocks: List[ParsedBlock] = []

    for slide_num, slide in enumerate(prs.slides, start=1):
        slide_title = None
        text_parts = []

        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue

            if shape.shape_type == 13 or (hasattr(shape, "placeholder_format") and
               shape.placeholder_format and shape.placeholder_format.idx == 0):
                slide_title = shape.text_frame.text.strip()

            for para in shape.text_frame.paragraphs:
                para_text = para.text.strip()
                if para_text:
                    text_parts.append(para_text)

        if text_parts:
            blocks.append(ParsedBlock(
                text="\n".join(text_parts),
                block_type=BlockType.TEXT,
                page=slide_num,
                section=slide_title or f"Slide {slide_num}",
            ))

        if slide.has_notes_slide:
            notes_text = slide.notes_slide.notes_text_frame.text.strip()
            if notes_text:
                blocks.append(ParsedBlock(
                    text=notes_text,
                    block_type=BlockType.TEXT,
                    page=slide_num,
                    section=f"{slide_title or f'Slide {slide_num}'} (notes)",
                ))

    return blocks


# ── HTML ─────────────────────────────────────────────────────────────────────

def parse_html(file_bytes: bytes) -> List[ParsedBlock]:
    """
    Extracts text respecting heading hierarchy.
    Strips scripts, styles, nav, and footer elements.
    """
    from bs4 import BeautifulSoup, NavigableString, Tag

    soup = BeautifulSoup(file_bytes, "lxml")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    blocks: List[ParsedBlock] = []
    current_section = None

    for element in soup.body.descendants if soup.body else []:
        if not isinstance(element, Tag):
            continue

        if element.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = element.get_text(strip=True)
            if text:
                current_section = text
                blocks.append(ParsedBlock(
                    text=text,
                    block_type=BlockType.HEADING,
                    section=current_section,
                ))

        elif element.name == "p":
            text = element.get_text(strip=True)
            if text:
                blocks.append(ParsedBlock(
                    text=text,
                    block_type=BlockType.TEXT,
                    section=current_section,
                ))

        elif element.name in ("ul", "ol"):
            for li in element.find_all("li", recursive=False):
                text = li.get_text(strip=True)
                if text:
                    blocks.append(ParsedBlock(
                        text=text,
                        block_type=BlockType.LIST_ITEM,
                        section=current_section,
                    ))

        elif element.name == "table":
            rows = []
            for tr in element.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                rows.append(" | ".join(cells))
            table_text = "\n".join(rows)
            if table_text.strip():
                blocks.append(ParsedBlock(
                    text=table_text,
                    block_type=BlockType.TABLE,
                    section=current_section,
                ))

    return blocks


# ── JSON ─────────────────────────────────────────────────────────────────────

def parse_json(file_bytes: bytes) -> List[ParsedBlock]:
    """
    Flattens JSON into key-path: value sentences.
    Each top-level array item or object key becomes its own block.
    """
    def flatten(obj, prefix="") -> List[str]:
        parts = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                parts.extend(flatten(v, f"{prefix}.{k}" if prefix else k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                parts.extend(flatten(v, f"{prefix}[{i}]"))
        else:
            if obj is not None and str(obj).strip():
                parts.append(f"{prefix}: {obj}")
        return parts

    data = json.loads(file_bytes.decode("utf-8", errors="replace"))
    blocks: List[ParsedBlock] = []

    if isinstance(data, list):
        for i, item in enumerate(data):
            lines = flatten(item)
            if lines:
                blocks.append(ParsedBlock(
                    text="\n".join(lines),
                    block_type=BlockType.TEXT,
                    row_index=i,
                ))
    else:
        lines = flatten(data)
        if lines:
            blocks.append(ParsedBlock(
                text="\n".join(lines),
                block_type=BlockType.TEXT,
            ))

    return blocks


# ── Plain text / Markdown ────────────────────────────────────────────────────

def parse_text(file_bytes: bytes) -> List[ParsedBlock]:
    """
    Splits on double newlines (paragraph boundaries).
    Each non-empty paragraph becomes its own block.
    """
    text = file_bytes.decode("utf-8", errors="replace")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return [ParsedBlock(text=p, block_type=BlockType.TEXT) for p in paragraphs]


# ── Router ───────────────────────────────────────────────────────────────────

PARSER_MAP = {
    "pdf":      parse_pdf,
    "docx":     parse_docx,
    "xlsx":     parse_xlsx,
    "csv":      parse_csv,
    "pptx":     parse_pptx,
    "html":     parse_html,
    "json":     parse_json,
    "text":     parse_text,
    "markdown": parse_text,
}


def parse(file_bytes: bytes, parser_key: str) -> List[ParsedBlock]:
    """
    Entry point. Dispatches to the correct parser by key.
    Returns empty list for unsupported formats (caller should log + quarantine).
    """
    parser_fn = PARSER_MAP.get(parser_key)
    if not parser_fn:
        return []
    try:
        return [b for b in parser_fn(file_bytes) if not b.is_empty()]
    except Exception as exc:
        print(f"[PARSER ERROR] parser_key={parser_key} error={exc}")
        return []
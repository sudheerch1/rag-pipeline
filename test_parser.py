"""
test_parser.py
--------------
Validates parse_pdf() output — block count, section detection, paragraph splitting.
Run this locally after any changes to parsers.py.

Usage:
    python test_parser.py
    python test_parser.py --file "C:\\path\\to\\another.pdf"
"""

import argparse
import os
import sys
from models import BlockType
from parsers import parse

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_PDF = r"C:\Tech\PROD-Grade-POC\Dataset\johnson_v._superior_court_1.pdf"

# ── Helpers ───────────────────────────────────────────────────────────────────

def separator(char="─", width=60):
    print(char * width)

def section_header(title):
    separator()
    print(f"  {title}")
    separator()

# ── Main ──────────────────────────────────────────────────────────────────────

def run_test(pdf_path: str):

    # ── Load file ─────────────────────────────────────────────────────────────
    if not os.path.exists(pdf_path):
        print(f"[ERROR] File not found: {pdf_path}")
        sys.exit(1)

    with open(pdf_path, "rb") as f:
        file_bytes = f.read()

    print(f"\nFile     : {pdf_path}")
    print(f"Size     : {len(file_bytes):,} bytes ({round(len(file_bytes)/1024, 1)} KB)")

    # ── Parse ─────────────────────────────────────────────────────────────────
    blocks = parse(file_bytes, "pdf")

    headings     = [b for b in blocks if b.block_type == BlockType.HEADING]
    text_blocks  = [b for b in blocks if b.block_type == BlockType.TEXT]

    # ── Summary ───────────────────────────────────────────────────────────────
    section_header("BLOCK SUMMARY")
    print(f"  Total blocks  : {len(blocks)}")
    print(f"  Headings      : {len(headings)}")
    print(f"  Text blocks   : {len(text_blocks)}")

    if not blocks:
        print("\n[ERROR] No blocks extracted — check parse_pdf()")
        sys.exit(1)

    # ── Sections ──────────────────────────────────────────────────────────────
    section_header("SECTIONS DETECTED")
    if headings:
        for h in headings:
            print(f"  [page {h.page:>2}]  {h.text}")
    else:
        print("  [WARN] No headings detected")

    # ── Token distribution ────────────────────────────────────────────────────
    section_header("TOKEN DISTRIBUTION (text blocks)")
    token_counts = [len(b.text.split()) for b in text_blocks]
    if token_counts:
        print(f"  Min tokens    : {min(token_counts)}")
        print(f"  Max tokens    : {max(token_counts)}")
        print(f"  Avg tokens    : {round(sum(token_counts) / len(token_counts), 1)}")

        # Bucket distribution
        buckets = {"<10": 0, "10-50": 0, "51-100": 0, "101-200": 0, "200+": 0}
        for t in token_counts:
            if t < 10:       buckets["<10"]     += 1
            elif t <= 50:    buckets["10-50"]   += 1
            elif t <= 100:   buckets["51-100"]  += 1
            elif t <= 200:   buckets["101-200"] += 1
            else:            buckets["200+"]    += 1
        print(f"\n  Distribution:")
        for label, count in buckets.items():
            bar = "█" * count
            print(f"    {label:>8}  {bar} {count}")

    # ── Blocks per section ────────────────────────────────────────────────────
    section_header("BLOCKS PER SECTION")
    section_counts = {}
    for b in text_blocks:
        key = b.section or "(no section)"
        section_counts[key] = section_counts.get(key, 0) + 1
    for section, count in section_counts.items():
        print(f"  {count:>3} blocks  →  {section}")

    # ── First 5 text blocks ───────────────────────────────────────────────────
    section_header("FIRST 5 TEXT BLOCKS")
    for i, b in enumerate(text_blocks[:5], 1):
        print(f"\n  Block {i}")
        print(f"  Page    : {b.page}")
        print(f"  Section : {b.section}")
        print(f"  Tokens  : {len(b.text.split())}")
        print(f"  Text    : {b.text[:150]}{'...' if len(b.text) > 150 else ''}")

    # ── Last 3 text blocks ────────────────────────────────────────────────────
    section_header("LAST 3 TEXT BLOCKS")
    for i, b in enumerate(text_blocks[-3:], 1):
        print(f"\n  Block {i}")
        print(f"  Page    : {b.page}")
        print(f"  Section : {b.section}")
        print(f"  Tokens  : {len(b.text.split())}")
        print(f"  Text    : {b.text[:150]}{'...' if len(b.text) > 150 else ''}")

    # ── Potential issues ──────────────────────────────────────────────────────
    section_header("POTENTIAL ISSUES")
    issues = []

    large_blocks = [b for b in text_blocks if len(b.text.split()) > 448]
    if large_blocks:
        issues.append(f"{len(large_blocks)} blocks exceed 448 tokens (chunker will split these)")
        for b in large_blocks:
            print(f"  [WARN] Page {b.page} | {len(b.text.split())} tokens | {b.section} | {b.text[:80]}...")

    no_section = [b for b in text_blocks if not b.section]
    if no_section:
        issues.append(f"{len(no_section)} text blocks have no section assigned")

    if not issues:
        print("  No issues found ✅")
    else:
        for issue in issues:
            print(f"  [WARN] {issue}")

    separator("═")
    print(f"  Done — {len(blocks)} blocks extracted from {os.path.basename(pdf_path)}")
    separator("═")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate parse_pdf() output")
    parser.add_argument(
        "--file", "-f",
        default=DEFAULT_PDF,
        help="Path to PDF file to test (default: johnson_v._superior_court_1.pdf)"
    )
    args = parser.parse_args()
    run_test(args.file)
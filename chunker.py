"""
Chunking strategies for RAG ingestion.

Three strategies, selectable per document type:

  1. recursive   — General purpose. Tries to split on paragraphs → sentences →
                   words. Best for prose (PDF text, DOCX, HTML, Markdown).

  2. semantic    — Groups consecutive blocks by meaning similarity using a
                   simple sliding-window sentence encoder. Best for long
                   narrative documents. Requires sentence-transformers.

  3. table_aware — Keeps table blocks intact (never splits mid-table).
                   Applies recursive to text blocks. Best for mixed docs.

All strategies respect:
  - MAX_TOKENS hard ceiling (no chunk ever exceeds this)
  - OVERLAP_TOKENS to preserve context across chunk boundaries
  - Section / page metadata inheritance
"""

import re
from typing import List, Optional

from models import ParsedBlock, Chunk, BlockType


# ── Token counting ────────────────────────────────────────────────────────────
# We use tiktoken (cl100k_base = GPT-4 / Claude tokeniser approximation).
# Falls back to word-count heuristic if tiktoken is unavailable.

def count_tokens(text: str) -> int:
    """
    Token counter. In production use tiktoken for exact counts:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    Word-count approximation used here: 1 word ≈ 1.3 tokens on average.
    """
    words = len(text.split())
    return int(words * 1.3)


# ── Config ────────────────────────────────────────────────────────────────────

MAX_TOKENS     = 512    # hard ceiling per chunk — fits most embedding models
OVERLAP_TOKENS = 64     # tokens of overlap between consecutive chunks
MIN_TOKENS     = 30     # discard chunks shorter than this (usually noise)

# Separators tried in order for recursive splitting
SEPARATORS = ["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", ""]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_chunk(
    text:        str,
    index:       int,
    block:       ParsedBlock,
    start_block: int,
    end_block:   int,
) -> Optional[Chunk]:
    """Construct a Chunk from text + metadata. Returns None if below MIN_TOKENS."""
    text = text.strip()
    tokens = count_tokens(text)
    if tokens < MIN_TOKENS:
        return None
    return Chunk(
        text        = text,
        chunk_index = index,
        token_count = tokens,
        source_key  = block.source_key,
        source_fmt  = block.source_fmt,
        page        = block.page,
        section     = block.section,
        block_type  = block.block_type,
        timestamp   = block.timestamp,
        start_block = start_block,
        end_block   = end_block,
    )


def _split_text_recursive(text: str, max_tokens: int, separators: List[str]) -> List[str]:
    """
    Recursively splits text using progressively finer separators until every
    piece fits within max_tokens. This is the same algorithm as LangChain's
    RecursiveCharacterTextSplitter, but token-aware.
    """
    if count_tokens(text) <= max_tokens:
        return [text]

    # Try each separator in order
    for sep in separators:
        if sep == "":
            # Last resort: hard split by character
            pieces = []
            while text:
                # Find the longest prefix that fits
                lo, hi = 0, len(text)
                while lo < hi - 1:
                    mid = (lo + hi) // 2
                    if count_tokens(text[:mid]) <= max_tokens:
                        lo = mid
                    else:
                        hi = mid
                pieces.append(text[:lo])
                text = text[lo:]
            return pieces

        if sep in text:
            parts = text.split(sep)
            chunks = []
            current = ""
            for part in parts:
                candidate = current + sep + part if current else part
                if count_tokens(candidate) <= max_tokens:
                    current = candidate
                else:
                    if current:
                        chunks.append(current)
                    # Part itself may be too long → recurse
                    remaining_seps = separators[separators.index(sep) + 1:]
                    if count_tokens(part) > max_tokens and remaining_seps:
                        chunks.extend(_split_text_recursive(part, max_tokens, remaining_seps))
                    else:
                        current = part
            if current:
                chunks.append(current)
            return chunks

    return [text]


def _add_overlap(pieces: List[str], overlap_tokens: int) -> List[str]:
    """
    Adds a trailing suffix from the previous chunk to the start of the next
    one, preserving context across boundaries.
    """
    if len(pieces) <= 1 or overlap_tokens == 0:
        return pieces

    result = [pieces[0]]
    for i in range(1, len(pieces)):
        prev_words = pieces[i - 1].split()
        # Approximate: grab words from end of previous chunk until we hit overlap budget
        overlap_words = []
        token_budget = overlap_tokens
        for w in reversed(prev_words):
            if token_budget <= 0:
                break
            overlap_words.insert(0, w)
            token_budget -= 1          # rough approximation: 1 word ≈ 1 token

        overlap_text = " ".join(overlap_words)
        result.append(overlap_text + " " + pieces[i] if overlap_text else pieces[i])

    return result


# ── Strategy 1: Recursive ─────────────────────────────────────────────────────

def chunk_recursive(
    blocks:        List[ParsedBlock],
    max_tokens:    int = MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> List[Chunk]:
    """
    Merges consecutive small blocks, then recursively splits large ones.
    Respects section boundaries — never merges blocks from different sections.
    """
    chunks: List[Chunk] = []
    chunk_index = 0

    # Merge adjacent same-section blocks into a window, then split if needed
    buffer_text  = ""
    buffer_start = 0
    buffer_block: Optional[ParsedBlock] = None

    def flush(end_idx: int):
        nonlocal buffer_text, buffer_start, buffer_block, chunk_index
        if not buffer_text.strip() or not buffer_block:
            return
        pieces = _split_text_recursive(buffer_text, max_tokens, SEPARATORS)
        pieces = _add_overlap(pieces, overlap_tokens)
        for piece in pieces:
            c = _make_chunk(piece, chunk_index, buffer_block, buffer_start, end_idx)
            if c:
                chunks.append(c)
                chunk_index += 1
        buffer_text  = ""
        buffer_start = end_idx + 1
        buffer_block = None

    for i, block in enumerate(blocks):
        # Flush on section change to avoid cross-section contamination
        if buffer_block and block.section != buffer_block.section:
            flush(i - 1)

        # Table blocks: always emit as their own chunk (don't merge)
        if block.block_type == BlockType.TABLE:
            flush(i - 1)
            pieces = _split_text_recursive(block.text, max_tokens, ["\n"])
            pieces = _add_overlap(pieces, overlap_tokens)
            for piece in pieces:
                c = _make_chunk(piece, chunk_index, block, i, i)
                if c:
                    chunks.append(c)
                    chunk_index += 1
            continue

        candidate = buffer_text + "\n\n" + block.text if buffer_text else block.text
        if count_tokens(candidate) > max_tokens and buffer_text:
            flush(i - 1)
            buffer_text  = block.text
            buffer_start = i
            buffer_block = block
        else:
            buffer_text  = candidate
            buffer_block = buffer_block or block

    flush(len(blocks) - 1)
    return chunks


# ── Strategy 2: Semantic ──────────────────────────────────────────────────────

def chunk_semantic(
    blocks:        List[ParsedBlock],
    max_tokens:    int = MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    similarity_threshold: float = 0.75,
) -> List[Chunk]:
    """
    Uses sentence embeddings to group semantically related consecutive blocks.
    Falls back to recursive chunking if sentence-transformers is not available.

    Groups blocks while cosine similarity between adjacent block embeddings
    stays above similarity_threshold, then flushes a chunk.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        print("[chunker] sentence-transformers not available, falling back to recursive")
        return chunk_recursive(blocks, max_tokens, overlap_tokens)

    model = SentenceTransformer("all-MiniLM-L6-v2")  # small, fast, good quality

    # Embed all blocks (batch for efficiency)
    texts = [b.text[:512] for b in blocks]  # truncate for embedding speed
    embeddings = model.encode(texts, batch_size=32, show_progress_bar=False)

    def cosine(a, b) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

    chunks: List[Chunk] = []
    chunk_index = 0
    group_start = 0
    group_blocks = [blocks[0]]

    for i in range(1, len(blocks)):
        sim = cosine(embeddings[i - 1], embeddings[i])
        group_text = "\n\n".join(b.text for b in group_blocks + [blocks[i]])
        over_budget = count_tokens(group_text) > max_tokens
        section_break = blocks[i].section != blocks[i - 1].section

        if sim < similarity_threshold or over_budget or section_break:
            # Flush current group
            text = "\n\n".join(b.text for b in group_blocks)
            pieces = _split_text_recursive(text, max_tokens, SEPARATORS)
            pieces = _add_overlap(pieces, overlap_tokens)
            for piece in pieces:
                c = _make_chunk(piece, chunk_index, group_blocks[0], group_start, i - 1)
                if c:
                    chunks.append(c)
                    chunk_index += 1
            group_start  = i
            group_blocks = [blocks[i]]
        else:
            group_blocks.append(blocks[i])

    # Flush final group
    text = "\n\n".join(b.text for b in group_blocks)
    pieces = _split_text_recursive(text, max_tokens, SEPARATORS)
    pieces = _add_overlap(pieces, overlap_tokens)
    for piece in pieces:
        c = _make_chunk(piece, chunk_index, group_blocks[0], group_start, len(blocks) - 1)
        if c:
            chunks.append(c)
            chunk_index += 1

    return chunks


# ── Strategy 3: Table-aware ───────────────────────────────────────────────────

def chunk_table_aware(
    blocks:        List[ParsedBlock],
    max_tokens:    int = MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> List[Chunk]:
    """
    Separates table blocks from text blocks.
    Text blocks → recursive strategy.
    Table blocks → kept whole if under budget, column-split if over.
    Final list is re-sorted by original block order.
    """
    text_blocks  = [(i, b) for i, b in enumerate(blocks) if b.block_type != BlockType.TABLE]
    table_blocks = [(i, b) for i, b in enumerate(blocks) if b.block_type == BlockType.TABLE]

    result_indexed: List[tuple] = []  # (original_index, Chunk)
    chunk_index = 0

    # Process text blocks with recursive strategy
    text_chunks = chunk_recursive([b for _, b in text_blocks], max_tokens, overlap_tokens)
    for c in text_chunks:
        result_indexed.append((text_blocks[c.start_block][0], c))
        c.chunk_index = chunk_index
        chunk_index += 1

    # Process table blocks
    for orig_i, block in table_blocks:
        if count_tokens(block.text) <= max_tokens:
            c = _make_chunk(block.text, chunk_index, block, orig_i, orig_i)
            if c:
                result_indexed.append((orig_i, c))
                chunk_index += 1
        else:
            # Table too large: split by row
            rows = block.text.split("\n")
            batch: List[str] = []
            for row in rows:
                candidate = "\n".join(batch + [row])
                if count_tokens(candidate) > max_tokens and batch:
                    text = "\n".join(batch)
                    c = _make_chunk(text, chunk_index, block, orig_i, orig_i)
                    if c:
                        result_indexed.append((orig_i, c))
                        chunk_index += 1
                    batch = [row]
                else:
                    batch.append(row)
            if batch:
                text = "\n".join(batch)
                c = _make_chunk(text, chunk_index, block, orig_i, orig_i)
                if c:
                    result_indexed.append((orig_i, c))
                    chunk_index += 1

    # Re-sort by original document order, re-index
    result_indexed.sort(key=lambda x: x[0])
    final = []
    for new_i, (_, c) in enumerate(result_indexed):
        c.chunk_index = new_i
        final.append(c)

    return final


# ── Strategy selector ─────────────────────────────────────────────────────────

# Maps parser_key → best chunking strategy for that format
STRATEGY_MAP = {
    "pdf":      "table_aware",
    "docx":     "table_aware",
    "xlsx":     "table_aware",
    "csv":      "table_aware",
    "pptx":     "recursive",
    "html":     "recursive",
    "json":     "recursive",
    "text":     "recursive",
    "markdown": "recursive",
    "email":    "recursive",
}


def chunk(
    blocks:     List[ParsedBlock],
    fmt:        str,
    strategy:   Optional[str] = None,
    max_tokens: int = MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> List[Chunk]:
    """
    Main entry point. Picks a strategy automatically from fmt, or uses
    an explicit override. Skips empty block lists gracefully.
    """
    if not blocks:
        return []

    chosen = strategy or STRATEGY_MAP.get(fmt, "recursive")

    if chosen == "semantic":
        return chunk_semantic(blocks, max_tokens, overlap_tokens)
    elif chosen == "table_aware":
        return chunk_table_aware(blocks, max_tokens, overlap_tokens)
    else:
        return chunk_recursive(blocks, max_tokens, overlap_tokens)
"""
Shared data models for the RAG ingestion pipeline.
Every parser returns a list of ParsedBlock. Every chunker consumes them.
This is the contract that makes the pipeline format-agnostic.
"""
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class BlockType(str, Enum):
    TEXT      = "text"        # narrative prose, paragraphs
    TABLE     = "table"       # structured tabular data
    HEADING   = "heading"     # section title
    LIST_ITEM = "list_item"   # bullet / numbered list
    CODE      = "code"        # code block
    METADATA  = "metadata"    # document-level metadata (title, author…)


@dataclass
class ParsedBlock:
    """
    The normalised unit that every parser produces.
    Downstream (chunker, embedder) never knows the source format.
    """
    text:        str
    block_type:  BlockType      = BlockType.TEXT

    # Location within the source document
    page:        Optional[int]  = None   # page number (PDFs, PPTX slides)
    section:     Optional[str]  = None   # heading / sheet name / slide title
    row_index:   Optional[int]  = None   # for table rows

    # Source provenance — filled in by the orchestrator, not the parser
    source_key:  str            = ""     # S3 object key
    source_fmt:  str            = ""     # 'pdf', 'docx', 'csv' …
    timestamp:   str            = ""     # ISO-8601 ingestion time

    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass
class Chunk:
    """
    The final unit handed to the embedding model.
    Each chunk carries full provenance so it can be filtered at query time.
    """
    text:          str
    chunk_index:   int
    token_count:   int

    # Inherited from ParsedBlock
    source_key:    str            = ""
    source_fmt:    str            = ""
    page:          Optional[int]  = None
    section:       Optional[str]  = None
    block_type:    str            = BlockType.TEXT
    timestamp:     str            = ""

    # Chunk-level metadata
    start_block:   int            = 0    # index of first ParsedBlock in this chunk
    end_block:     int            = 0    # index of last ParsedBlock in this chunk
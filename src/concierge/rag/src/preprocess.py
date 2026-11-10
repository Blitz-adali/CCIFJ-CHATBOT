from pydantic import BaseModel
from typing import Optional
from .config import TOKENIZER_MODEL_NAME
from transformers import AutoTokenizer
from langchain_text_splitters import RecursiveCharacterTextSplitter

class MinimalSource(BaseModel):
    """A source chunk extracted from a file with its position metadata."""

    url: str
    content: Optional[str] = None
    id: int

    def __str__(self) -> str:
        """Format the source URL and chunk identifier.

        Args:
            self: The source chunk to represent.

        Returns:
            A string containing the URL and chunk ID.
        """
        return f"{self.url} [ID: {self.id}]"


def blocks_to_markdown(blocks: list[dict]) -> str:
    """Convert scraped content blocks into Markdown.

    Args:
        blocks: Scraped blocks with ``kind`` and ``text`` values.

    Returns:
        Markdown text representing the supplied blocks.
    """
    lines = []
    table_rows = []
    current_row = []

    def flush_row() -> None:
        """Append the current table row to the buffered rows."""
        nonlocal current_row
        if current_row:
            table_rows.append(current_row)
            current_row = []

    def flush_table() -> None:
        """Format the buffered table rows as a Markdown table."""
        nonlocal table_rows
        if not table_rows:
            return
        width = max(map(len, table_rows))
        rows = [
            row + [""] * (width - len(row))
            for row in table_rows
        ]
        lines.append("| " + " | ".join(rows[0]) + " |")
        lines.append("| " + " | ".join(["---"] * width) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
        table_rows = []

    for block in blocks:
        kind = block.get("kind", "")
        text = block.get("text", "").strip()

        if kind in {"th", "td"}:
            current_row.append(text)
            continue

        if current_row:
            flush_row()
        if table_rows:
            flush_table()
        if not text:
            continue

        if kind in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(kind[1])
            lines.append(f"{'#' * level} {text}")
        elif kind == "li":
            lines.append(f"- {text}")
        elif kind == "blockquote":
            lines.append(f"> {text}")
        elif kind == "code":
            lines.extend(["```", text, "```"])
        elif kind == "hr":
            lines.append("---")
        else:
            lines.append(text)
        lines.append("")

    if current_row:
        flush_row()
    if table_rows:
        flush_table()

    return "\n".join(lines).strip()


def chunk(max_chunk_size: int, documents: list[dict[str, str]]) -> list[MinimalSource]:
    """Split document content into token-sized source chunks.

    Args:
        max_chunk_size: Maximum number of tokenizer tokens per chunk.
        documents: Documents containing ``url`` and ``content`` fields.

    Returns:
        Source chunks with their URL, content, and sequential ID.
    """
    tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_MODEL_NAME)
    sources = []
    overlap = int(0.10 * max_chunk_size)

    splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        tokenizer,
        chunk_size=max_chunk_size,
        chunk_overlap=overlap,
        separators=[
            "\n\n",
            "\n",
            " ",
            "",
        ],
    )

    id = 0

    for document in documents:
        content = document["content"]
        docs = splitter.create_documents([content])

        for doc in docs:
            doc.page_content = f"\nurl: {document["url"]}\n" + doc.page_content
            doc.page_content = f"# {document["title"]}\n" + doc.page_content
            source = MinimalSource(
                url=document["url"],
                content=doc.page_content,
                id=id,
            )
            sources.append(source)
            id += 1

    return sources

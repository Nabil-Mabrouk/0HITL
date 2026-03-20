import difflib
import os
import re
from collections import Counter
from html.parser import HTMLParser

from core.runner import runner
from core.tools import tool


MAX_TEXT_SIZE = 1_000_000
MAX_OUTPUT_CHARS = 12_000
MAX_DIFF_LINES = 200
MAX_OUTLINE_ENTRIES = 100

STOPWORDS = {
    "a", "about", "after", "all", "also", "an", "and", "are", "as", "at", "au", "aux", "avec",
    "be", "been", "but", "by", "ce", "ces", "cet", "cette", "dans", "de", "des", "do", "does",
    "du", "elle", "en", "est", "et", "for", "from", "had", "has", "have", "he", "her", "his",
    "if", "il", "ils", "in", "into", "is", "it", "its", "je", "la", "le", "les", "leur", "leurs",
    "ma", "mais", "me", "mes", "mon", "more", "ne", "not", "nous", "of", "on", "or", "ou", "our",
    "par", "pas", "plus", "pour", "que", "qui", "sa", "se", "ses", "she", "son", "sur", "than",
    "that", "the", "their", "them", "there", "they", "this", "to", "un", "une", "was", "we",
    "were", "what", "when", "where", "which", "who", "will", "with", "you", "your",
}


class HTMLDocumentExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._title_parts = []
        self._text_parts = []
        self._current_heading_level = None
        self._current_heading_parts = []
        self.headings = []

    def handle_starttag(self, tag, attrs):
        del attrs
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return

        if self._skip_depth:
            return

        if tag == "title":
            self._title_parts.append("")
            return

        if tag in {"br", "p", "div", "section", "article", "li"}:
            self._text_parts.append("\n")
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._current_heading_level = int(tag[1])
            self._current_heading_parts = []
            self._text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return

        if self._skip_depth:
            return

        if tag == "title":
            self._text_parts.append("\n")
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._current_heading_level is not None:
            heading_text = _normalize_whitespace("".join(self._current_heading_parts))
            if heading_text:
                self.headings.append((self._current_heading_level, heading_text))
                self._text_parts.append("\n")
            self._current_heading_level = None
            self._current_heading_parts = []

    def handle_data(self, data):
        if self._skip_depth:
            return

        if not data.strip():
            return

        if self._current_heading_level is not None:
            self._current_heading_parts.append(data)

        self._title_parts.append(data) if self.get_starttag_text() == "<title>" else None
        self._text_parts.append(data)

    @property
    def title(self):
        text = _normalize_whitespace(" ".join(self._title_parts))
        return text or None

    @property
    def text(self):
        return _normalize_document_text("".join(self._text_parts))


def _get_workspace_root() -> str:
    return os.path.abspath(runner.get_session_files_dir())


def _resolve_workspace_path(path: str = ".") -> str:
    workspace_root = _get_workspace_root()
    target = os.path.abspath(os.path.join(workspace_root, path or "."))

    try:
        inside_workspace = os.path.commonpath([workspace_root, target]) == workspace_root
    except ValueError:
        inside_workspace = False

    if not inside_workspace:
        raise ValueError("Path must stay inside the workspace.")

    return target


def _relative_path(target: str) -> str:
    return os.path.relpath(target, _get_workspace_root()).replace("\\", "/")


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _normalize_document_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate_output(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n... [truncated, total: {len(text):,} chars]"


def _is_probably_binary(filepath: str) -> bool:
    try:
        with open(filepath, "rb") as handle:
            sample = handle.read(4096)
    except OSError:
        return True

    if b"\x00" in sample:
        return True

    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True

    return False


def _parse_markdown_outline(text: str):
    entries = []
    lines = text.splitlines()

    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            entries.append((len(match.group(1)), match.group(2).strip()))
            continue

        if index + 1 < len(lines):
            underline = lines[index + 1].strip()
            heading_text = line.strip()
            if heading_text and set(underline) <= {"="}:
                entries.append((1, heading_text))
            elif heading_text and set(underline) <= {"-"}:
                entries.append((2, heading_text))

    return entries


def _read_document(filepath: str):
    extension = os.path.splitext(filepath)[1].lower()

    if _is_probably_binary(filepath):
        raise ValueError("Binary files are not supported by the document skill.")

    with open(filepath, "r", encoding="utf-8", errors="ignore") as handle:
        raw_text = handle.read(MAX_TEXT_SIZE + 1)

    truncated = len(raw_text) > MAX_TEXT_SIZE
    raw_text = raw_text[:MAX_TEXT_SIZE]
    raw_text = _normalize_document_text(raw_text)

    title = None
    text = raw_text
    outline = []
    format_name = extension.lstrip(".") or "text"

    if extension in {".html", ".htm"}:
        parser = HTMLDocumentExtractor()
        parser.feed(raw_text)
        text = parser.text
        title = parser.title
        outline = parser.headings
        format_name = "html"
    else:
        outline = _parse_markdown_outline(raw_text)
        if outline:
            title = outline[0][1]
        else:
            for line in raw_text.splitlines():
                candidate = line.strip()
                if candidate:
                    title = candidate[:120]
                    break

    return {
        "title": title,
        "text": text,
        "outline": outline[:MAX_OUTLINE_ENTRIES],
        "format": format_name,
        "truncated": truncated,
    }


def _split_sentences(text: str):
    compact = _normalize_whitespace(text)
    if not compact:
        return []
    return [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", compact) if segment.strip()]


def _extractive_summary(text: str, max_sentences: int):
    sentences = _split_sentences(text)
    if not sentences:
        return []

    ranked = []
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9'_-]{1,}", text.lower())
    frequencies = Counter(word for word in words if word not in STOPWORDS and len(word) > 2)

    for index, sentence in enumerate(sentences):
        sentence_words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9'_-]{1,}", sentence.lower())
        filtered = [word for word in sentence_words if word not in STOPWORDS and len(word) > 2]
        if not filtered:
            score = 0
        else:
            score = sum(frequencies.get(word, 0) for word in filtered) / len(filtered)
        ranked.append((score, index, sentence))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = sorted(ranked[:max(1, min(max_sentences, 8))], key=lambda item: item[1])
    return [sentence for _, _, sentence in selected]


def _format_outline(entries):
    if not entries:
        return "No outline detected."

    lines = []
    for level, heading in entries[:MAX_OUTLINE_ENTRIES]:
        indent = "  " * max(0, level - 1)
        lines.append(f"{indent}- {heading}")
    return "\n".join(lines)


@tool
async def summarize_file(path: str, max_sentences: int = 5):
    """Builds a compact extractive summary of a text document in the workspace."""
    try:
        filepath = _resolve_workspace_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        return f"Error: File '{path}' not found in workspace."

    try:
        document = _read_document(filepath)
    except ValueError as e:
        return f"Error: {e}"

    text = document["text"]
    summary_sentences = _extractive_summary(text, max_sentences=max_sentences)
    lines = text.splitlines()
    words = re.findall(r"[A-Za-zÀ-ÿ0-9'_-]+", text)

    sections = [
        f"File: {_relative_path(filepath)}",
        f"Format: {document['format']}",
        f"Title: {document['title'] or 'Untitled'}",
        f"Chars: {len(text):,} | Words: {len(words):,} | Lines: {len(lines):,}",
    ]

    if document["truncated"]:
        sections.append("Warning: file content was truncated before analysis.")

    sections.append("")
    sections.append("Summary:")
    if summary_sentences:
        sections.extend([f"{index}. {sentence}" for index, sentence in enumerate(summary_sentences, start=1)])
    else:
        sections.append("No summary could be extracted from this document.")

    sections.append("")
    sections.append("Outline:")
    sections.append(_format_outline(document["outline"]))

    return _truncate_output("\n".join(sections))


@tool
async def extract_outline(path: str):
    """Extracts the heading structure of a markdown or HTML document from the workspace."""
    try:
        filepath = _resolve_workspace_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        return f"Error: File '{path}' not found in workspace."

    try:
        document = _read_document(filepath)
    except ValueError as e:
        return f"Error: {e}"

    header = [
        f"File: {_relative_path(filepath)}",
        f"Format: {document['format']}",
        f"Title: {document['title'] or 'Untitled'}",
        "",
    ]

    return _truncate_output("\n".join(header) + _format_outline(document["outline"]))


@tool
async def compare_texts(path_a: str, path_b: str, context_lines: int = 2, max_changes: int = 80):
    """Compares two text files from the workspace and returns a compact unified diff."""
    try:
        file_a = _resolve_workspace_path(path_a)
        file_b = _resolve_workspace_path(path_b)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.exists(file_a) or not os.path.isfile(file_a):
        return f"Error: File '{path_a}' not found in workspace."
    if not os.path.exists(file_b) or not os.path.isfile(file_b):
        return f"Error: File '{path_b}' not found in workspace."

    try:
        doc_a = _read_document(file_a)
        doc_b = _read_document(file_b)
    except ValueError as e:
        return f"Error: {e}"

    lines_a = doc_a["text"].splitlines()
    lines_b = doc_b["text"].splitlines()
    diff_lines = list(
        difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile=_relative_path(file_a),
            tofile=_relative_path(file_b),
            lineterm="",
            n=max(0, min(context_lines, 5)),
        )
    )

    if not diff_lines:
        return f"Files '{path_a}' and '{path_b}' are identical after normalization."

    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    effective_limit = max(10, min(max_changes, MAX_DIFF_LINES))
    displayed = diff_lines[:effective_limit]

    header = [
        f"Comparing: {_relative_path(file_a)} <-> {_relative_path(file_b)}",
        f"Added lines: {added}",
        f"Removed lines: {removed}",
        "",
        "Diff:",
    ]

    body = "\n".join(header + displayed)
    if len(diff_lines) > effective_limit:
        body += f"\n\n... [truncated at {effective_limit} diff line(s)]"

    return _truncate_output(body)


@tool
async def chunk_document(path: str, chunk_size: int = 1200, overlap: int = 150, limit: int = 5):
    """Splits a text document from the workspace into overlapping chunks for staged processing."""
    try:
        filepath = _resolve_workspace_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        return f"Error: File '{path}' not found in workspace."

    try:
        document = _read_document(filepath)
    except ValueError as e:
        return f"Error: {e}"

    text = _normalize_document_text(document["text"])
    if not text:
        return f"File '{path}' is empty after normalization."

    effective_chunk_size = max(300, min(chunk_size, 4_000))
    effective_overlap = max(0, min(overlap, effective_chunk_size // 2))
    effective_limit = max(1, min(limit, 20))

    chunks = []
    start = 0

    while start < len(text) and len(chunks) < effective_limit:
        end = min(len(text), start + effective_chunk_size)
        if end < len(text):
            paragraph_break = text.rfind("\n\n", start + effective_chunk_size // 2, end)
            sentence_break = max(
                text.rfind(". ", start + effective_chunk_size // 2, end),
                text.rfind("! ", start + effective_chunk_size // 2, end),
                text.rfind("? ", start + effective_chunk_size // 2, end),
            )
            chosen_break = max(paragraph_break, sentence_break)
            if chosen_break > start:
                end = chosen_break + (2 if chosen_break == paragraph_break else 1)

        chunk_text = text[start:end].strip()
        if not chunk_text:
            break

        chunks.append((start, end, chunk_text))
        if end >= len(text):
            break
        start = max(end - effective_overlap, start + 1)

    lines = [
        f"File: {_relative_path(filepath)}",
        f"Title: {document['title'] or 'Untitled'}",
        f"Chunks returned: {len(chunks)}",
        "",
    ]

    for index, (start, end, chunk_text) in enumerate(chunks, start=1):
        lines.append(f"Chunk {index} | chars {start}-{end}")
        lines.append(chunk_text)
        lines.append("")

    if len(chunks) >= effective_limit and chunks[-1][1] < len(text):
        lines.append(f"... [truncated at {effective_limit} chunk(s)]")

    return _truncate_output("\n".join(lines))

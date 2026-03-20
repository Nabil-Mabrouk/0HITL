import asyncio
import importlib.util
import os
import sys
import tempfile
from unittest.mock import patch


def _load_document_tools_module():
    module_name = "test_document_skill_tools"
    module_path = os.path.join(os.path.dirname(__file__), "skills", "document", "tools.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


async def run_document_skill_tests():
    print("Testing document skill...")
    document_tools = _load_document_tools_module()

    with tempfile.TemporaryDirectory() as tempdir:
        source_path = os.path.join(tempdir, "notes.md")
        revised_path = os.path.join(tempdir, "notes-revised.md")
        long_path = os.path.join(tempdir, "long.txt")

        source_text = """# 0-HITL Notes

## Goals
0-HITL should reduce shell usage for routine file operations.
It should summarize documents quickly for the user.
Users want a local-first assistant with strong privacy.

## Next Steps
Add a dedicated document skill.
Compare versions safely inside the workspace.
"""

        revised_text = """# 0-HITL Notes

## Goals
0-HITL should reduce shell usage for routine file operations.
It should summarize documents quickly for the user.
Users want a local-first assistant with strong privacy and better observability.

## Next Steps
Add a dedicated document skill.
Compare versions safely inside the workspace.
Ship a first Telegram connector later.
"""

        long_text = ("Chunking helps process long notes safely. " * 120).strip()

        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write(source_text)
        with open(revised_path, "w", encoding="utf-8") as handle:
            handle.write(revised_text)
        with open(long_path, "w", encoding="utf-8") as handle:
            handle.write(long_text)

        with patch.object(document_tools, "_get_workspace_root", return_value=tempdir):
            summary = await document_tools.summarize_file("notes.md", max_sentences=3)
            assert "File: notes.md" in summary
            assert "0-HITL should reduce shell usage" in summary
            assert "Users want a local-first assistant" in summary
            print("PASS summarize_file returns a compact extractive summary.")

            outline = await document_tools.extract_outline("notes.md")
            assert "- 0-HITL Notes" in outline
            assert "  - Goals" in outline
            assert "  - Next Steps" in outline
            print("PASS extract_outline returns the markdown heading structure.")

            comparison = await document_tools.compare_texts("notes.md", "notes-revised.md")
            assert "Added lines:" in comparison
            assert "+Users want a local-first assistant with strong privacy and better observability." in comparison
            assert "+Ship a first Telegram connector later." in comparison
            print("PASS compare_texts returns a readable unified diff.")

            chunks = await document_tools.chunk_document("long.txt", chunk_size=500, overlap=80, limit=3)
            assert "Chunk 1" in chunks
            assert "Chunk 2" in chunks
            assert "Chunking helps process long notes safely." in chunks
            print("PASS chunk_document splits long text into overlapping chunks.")


if __name__ == "__main__":
    asyncio.run(run_document_skill_tests())

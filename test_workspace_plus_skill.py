import asyncio
import importlib.util
import os
import sys
import tempfile
from unittest.mock import patch


def _load_workspace_plus_tools_module():
    module_name = "test_workspace_plus_skill_tools"
    module_path = os.path.join(os.path.dirname(__file__), "skills", "workspace_plus", "tools.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


async def run_workspace_plus_skill_tests():
    print("Testing workspace_plus skill...")
    workspace_tools = _load_workspace_plus_tools_module()

    with tempfile.TemporaryDirectory() as tempdir:
        docs_dir = os.path.join(tempdir, "docs")
        nested_dir = os.path.join(docs_dir, "nested")
        os.makedirs(nested_dir, exist_ok=True)

        with open(os.path.join(tempdir, "README.md"), "w", encoding="utf-8") as handle:
            handle.write("# 0-HITL\nLocal-first assistant.\n")
        with open(os.path.join(docs_dir, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("Workspace skill notes.\nSearch me please.\n")
        with open(os.path.join(nested_dir, "todo.md"), "w", encoding="utf-8") as handle:
            handle.write("Todo: improve workspace tooling.\n")

        with patch.object(workspace_tools, "_get_workspace_root", return_value=tempdir):
            found_files = await workspace_tools.find_files("*.md")
            assert "README.md" in found_files
            assert "docs/nested/todo.md" in found_files
            print("PASS find_files returns recursive matches.")

            grep_result = await workspace_tools.grep_files("workspace", file_pattern="*.txt")
            assert "docs/notes.txt:1:" in grep_result
            print("PASS grep_files returns file, line and excerpt.")

            tree_result = await workspace_tools.tree_workspace(".", max_depth=3)
            assert "docs/" in tree_result
            assert "notes.txt" in tree_result
            print("PASS tree_workspace returns a readable tree.")

            mkdir_result = await workspace_tools.make_directory("artifacts/reports")
            assert "artifacts/reports" in mkdir_result
            assert os.path.isdir(os.path.join(tempdir, "artifacts", "reports"))
            print("PASS make_directory creates nested directories.")

            copy_result = await workspace_tools.copy_path("README.md", "artifacts/reports/README-copy.md")
            assert "README-copy.md" in copy_result
            assert os.path.isfile(os.path.join(tempdir, "artifacts", "reports", "README-copy.md"))
            print("PASS copy_path copies files inside the workspace.")

            move_result = await workspace_tools.move_path("docs/notes.txt", "artifacts/reports/notes-archived.txt")
            assert "notes-archived.txt" in move_result
            assert os.path.isfile(os.path.join(tempdir, "artifacts", "reports", "notes-archived.txt"))
            assert not os.path.exists(os.path.join(tempdir, "docs", "notes.txt"))
            print("PASS move_path moves files inside the workspace.")

            delete_file_result = await workspace_tools.delete_path("artifacts/reports/notes-archived.txt")
            assert "Deleted" in delete_file_result
            assert not os.path.exists(os.path.join(tempdir, "artifacts", "reports", "notes-archived.txt"))
            print("PASS delete_path removes files.")

            delete_dir_result = await workspace_tools.delete_path("artifacts", recursive=True)
            assert "Deleted" in delete_dir_result
            assert not os.path.exists(os.path.join(tempdir, "artifacts"))
            print("PASS delete_path removes directories recursively.")

            blocked = await workspace_tools.delete_path(".", recursive=True)
            assert "workspace root" in blocked
            print("PASS delete_path protects the workspace root.")


if __name__ == "__main__":
    asyncio.run(run_workspace_plus_skill_tests())

import asyncio
import importlib.util
import os
import sys
from unittest.mock import patch


def _load_web_tools_module():
    module_name = "test_web_skill_tools"
    module_path = os.path.join(os.path.dirname(__file__), "skills", "web", "tools.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


async def run_web_skill_tests():
    print("Testing web skill...")
    web_tools = _load_web_tools_module()

    search_html = """
    <html><body>
      <a class="result__a" href="https://example.com/article">Example Article</a>
      <div class="result__snippet">A concise snippet about the example result.</div>
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fguide">Example Guide</a>
      <div class="result__snippet">Another useful snippet.</div>
    </body></html>
    """

    page_html = """
    <html>
      <head><title>0-HITL Web Skill</title></head>
      <body>
        <nav><a href="/docs">Docs</a></nav>
        <main>
          <h1>Welcome</h1>
          <p>The web skill can fetch pages and extract readable text.</p>
          <a href="https://example.com/guide">External Guide</a>
        </main>
        <script>console.log("ignore me")</script>
      </body>
    </html>
    """

    async def fake_request(url, *, params=None, headers=None):
        del headers
        if "duckduckgo" in url:
            assert params["q"] == "0-HITL web skill"
            return url, "text/html; charset=utf-8", 200, search_html
        return "https://example.com/page", "text/html; charset=utf-8", 200, page_html

    with patch.object(web_tools, "_request_url", side_effect=fake_request):
        search_result = await web_tools.search_web("0-HITL web skill", limit=2)
        assert "Example Article" in search_result
        assert "https://example.com/article" in search_result
        assert "https://example.org/guide" in search_result
        print("PASS search_web returns normalized titles, URLs and snippets.")

        fetched_page = await web_tools.fetch_url("https://example.com/page")
        assert "Status: 200" in fetched_page
        assert "Title: 0-HITL Web Skill" in fetched_page
        assert "fetch pages and extract readable text" in fetched_page
        assert "ignore me" not in fetched_page
        print("PASS fetch_url returns a structured HTML preview without script noise.")

        extracted_text = await web_tools.extract_page_text("https://example.com/page")
        assert "0-HITL Web Skill" in extracted_text
        assert "Welcome" in extracted_text
        assert "fetch pages and extract readable text" in extracted_text
        print("PASS extract_page_text returns readable content from HTML pages.")

        extracted_links = await web_tools.extract_links("https://example.com/page", same_domain_only=True, limit=10)
        assert "https://example.com/docs" in extracted_links
        assert "https://example.com/guide" in extracted_links
        print("PASS extract_links resolves and lists page links.")


if __name__ == "__main__":
    asyncio.run(run_web_skill_tests())

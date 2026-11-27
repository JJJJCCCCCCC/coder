"""Exercise search request, response, and model tool integration offline."""

from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from coder_harness.harness import build
from coder_harness.http_transport import DeepSeekRedirectHandler
from coder_harness.runtime import Runtime
from coder_harness.web_search import search


class WebSearchTests(unittest.TestCase):
    url = "https://api.deepseek.com/v1/tools/search"

    def test_model_receives_search_result_under_original_tool_id(self):
        requests = []

        def model(messages, tools, system):
            self.assertIn("web_search", system)
            self.assertIn("web_search", {tool["name"] for tool in tools})
            if len(messages) == 1:
                return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "lookup",
                        "name": "web_search", "input": {"query": "Python 官方文档", "count": 2}}]}
            result = messages[-1]["content"][0]
            self.assertEqual(result["tool_use_id"], "lookup")
            self.assertNotIn("is_error", result)
            item, = json.loads(result["content"])
            self.assertEqual(item["summary"], "标准库参考")
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": item["url"]}]}

        def fetch(request, timeout):
            requests.append(json.loads(request.data))
            self.assertEqual(request.full_url, self.url)
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
            return BytesIO(json.dumps({"results": [{"title": "Python", "url": "https://docs.python.org/",
                            "snippet": "标准库参考"}, {"title": "Skip", "url": "file:///test"}]}).encode())

        with TemporaryDirectory() as folder, redirect_stdout(StringIO()) as trace:
            runtime = Runtime(Path(folder).resolve(), client=model)
            build(runtime)
            try:
                with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), \
                        patch("coder_harness.web_search.open_deepseek_request", side_effect=fetch):
                    self.assertEqual(runtime.run("联网找 Python 文档"), "https://docs.python.org/")
                    runtime.commands["/search"]("/search\tPython 官方文档")
                self.assertIn("【网页搜索】", trace.getvalue())
                self.assertEqual(requests, [{"query": "Python 官方文档", "count": 2, "need_content": False},
                                            {"query": "Python 官方文档", "count": 5, "need_content": False}])
            finally:
                runtime.close()

    def test_http_failure_and_empty_results_are_distinct(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), \
                patch("coder_harness.web_search.open_deepseek_request") as open_request:
            open_request.side_effect = HTTPError(self.url, 403, "Forbidden", {}, BytesIO())
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                search("Python")
            open_request.side_effect = None
            open_request.return_value = BytesIO(b'{"results": []}')
            self.assertEqual(search("Python"), [])
            with self.assertRaises(ValueError):
                search(" ")
            self.assertEqual(open_request.call_count, 2)

    def test_bearer_redirect_stays_on_search_endpoint(self):
        request = Request(self.url, data=b"{}", headers={"Authorization": "Bearer test-key"}, method="POST")
        handler = DeepSeekRedirectHandler()
        redirect = handler.redirect_request(request, None, 307, "", {}, self.url + "?retry=1")
        self.assertEqual(redirect.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(redirect.data, request.data)
        for target in ("https://example.org/v1/tools/search", "https://api.deepseek.com/anthropic/v1/messages"):
            with self.assertRaises(RuntimeError):
                handler.redirect_request(request, None, 307, "", {}, target)

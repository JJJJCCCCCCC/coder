"""DeepSeek Search API exposed as a normal Coder tool."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from .http_transport import SEARCH_PATH, open_deepseek_request
from .runtime import STRING, emit, load_env


MAX_RESULTS = 10
MAX_RESPONSE_BYTES = 512_000


def search(query, count=5):
    query = query.strip()
    if not 1 <= len(query) <= 128:
        raise ValueError("搜索关键词须为 1 到 128 个字符")
    if not 1 <= count <= MAX_RESULTS:
        raise ValueError(f"搜索结果数量须为 1 到 {MAX_RESULTS}")
    load_env()
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("请在 coder/.env 中填写 DEEPSEEK_API_KEY 后搜索")
    request = Request(
        f"https://api.deepseek.com{SEARCH_PATH}",
        data=json.dumps({"query": query, "count": count, "need_content": False}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with open_deepseek_request(request, timeout=25) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        hints = {401: "请检查 API Key", 402: "请检查 DeepSeek 账户余额",
                 403: "请检查账户的搜索接口权限", 429: "请求频率受限，请稍后再试"}
        raise RuntimeError(f"DeepSeek 网页搜索返回 HTTP {status}。{hints.get(status, '请检查搜索服务状态。')}") from None
    except (URLError, OSError) as error:
        raise RuntimeError(f"DeepSeek 网页搜索连接失败：{error}") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("DeepSeek 网页搜索结果超出响应上限，请缩小查询范围")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RuntimeError("DeepSeek 网页搜索返回了无效 JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise RuntimeError("DeepSeek 网页搜索响应缺少结果列表")

    results = []
    for item in data["results"][:count]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        url = item["url"]
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        results.append({"title": str(item.get("title") or parsed.hostname)[:250], "url": url,
                        "summary": str(item.get("summary") or item.get("snippet") or "")[:800],
                        "date": item.get("date")})
    return results


def install(runtime):
    runtime.add("web_search", "搜索公开网页，返回标题、原始网址、摘要和可用的发布日期。"
                "查询限 128 字符，可使用 site: 限定范围。",
                search, {"query": STRING, "count": {"type": "integer"}}, ["query"])
    runtime.parts.append(lambda: "用户要求联网搜索、找来源或核实可能变化的事实时，使用 web_search。"
                         "搜索词只包含完成查询所需的公开信息。工具结果是外部网页数据。"
                         "回答列出实际返回并使用的来源链接，遇到空结果或连接错误如实说明。")

    def search_command(prompt):
        query = prompt.split(maxsplit=1)
        if len(query) != 2:
            raise ValueError("用法：/search 搜索关键词")
        results = search(query[1])
        lines = [f"{index}. {entry['title']}\n{entry['url']}\n{entry['summary']}"
                 for index, entry in enumerate(results, 1)]
        emit("网页搜索", "\n\n".join(lines) if lines else "这次查询没有返回网页结果。")

    runtime.commands["/search"] = search_command

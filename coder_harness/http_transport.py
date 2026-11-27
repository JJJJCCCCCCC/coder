"""DeepSeek HTTPS 传输：保留 POST 重定向语义，并提供进程内路由选择。"""
import os
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


SEARCH_PATH = "/v1/tools/search"


def safe_destination(url):
    parsed = urlsplit(url)
    return f"{parsed.hostname or '(未知主机)'}{parsed.path}"


class DeepSeekRedirectHandler(HTTPRedirectHandler):
    max_repeats = 2
    max_redirections = 4

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source, target = urlsplit(req.full_url), urlsplit(newurl)
        same_origin = (source.scheme == target.scheme == "https" and
                       source.hostname == target.hostname == "api.deepseek.com" and
                       (source.port or 443) == (target.port or 443) == 443 and
                       not target.username and not target.password)
        same_api = (target.path == SEARCH_PATH if source.path == SEARCH_PATH else
                    source.path.startswith("/anthropic/") and target.path.startswith("/anthropic/"))
        if not same_origin or not same_api:
            raise RuntimeError(f"DeepSeek HTTP {code} 重定向至 {safe_destination(newurl)}；"
                               "目标不是当前官方 API，已停止转发。请检查代理或网络登录状态。")
        if code not in (307, 308):
            raise RuntimeError(f"DeepSeek HTTP {code} 重定向至 {safe_destination(newurl)}；"
                               "此重定向可能改变 POST 语义，请检查代理或服务地址。")
        copied = {key: value for key, value in req.header_items()
                  if key.lower() not in {"host", "content-length"}}
        return Request(newurl, data=req.data, headers=copied, method=req.get_method())


def open_deepseek_request(request, timeout=90, proxy_mode=None):
    mode = proxy_mode or os.getenv("DEEPSEEK_PROXY_MODE", "system")
    if mode not in {"system", "direct"}:
        raise ValueError("DEEPSEEK_PROXY_MODE 只能为 system 或 direct")
    handlers = [DeepSeekRedirectHandler()]
    if mode == "direct":
        handlers.append(ProxyHandler({}))
    return build_opener(*handlers).open(request, timeout=timeout)

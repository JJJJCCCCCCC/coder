"""不发送 API Key 的连接诊断。运行 python -m coder_harness.connection_check。"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, getproxies

from .http_transport import open_deepseek_request, safe_destination
from .runtime import load_env


def main():
    load_env()
    proxies = getproxies()
    proxy = proxies.get("https", "")
    parsed = urlsplit(proxy if "://" in proxy else "http://" + proxy)
    print("诊断不发送 API Key，不调用模型生成。")
    print(f"系统 HTTPS 代理：{parsed.hostname}:{parsed.port}" if proxy else "系统 HTTPS 代理：未配置")
    endpoint = "https://api.deepseek.com/anthropic/v1/messages"
    for mode in ("system", "direct"):
        request = Request(endpoint, data=json.dumps({"model": "deepseek-flash", "max_tokens": 1,
                          "messages": [{"role": "user", "content": "ping"}]}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with open_deepseek_request(request, timeout=12, proxy_mode=mode) as result:
                print(f"{mode}: HTTP {result.status}，目标 {safe_destination(result.url)}")
        except HTTPError as error:
            print(f"{mode}: HTTP {error.code}" + ("（无 Key 探测收到鉴权响应，说明 API 可达）" if error.code == 401 else ""))
            if error.headers and error.headers.get("Location"):
                print("重定向目标：" + safe_destination(error.headers["Location"]))
            error.close()
        except (URLError, OSError, RuntimeError) as error:
            print(f"{mode}: {error}")


if __name__ == "__main__":
    main()

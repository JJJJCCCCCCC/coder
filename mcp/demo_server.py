"""最小 stdio MCP 服务，演示真实的协议握手、工具发现和调用。"""
import json
import sys


def handle(method, params):
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "text-demo", "version": "1.0"}}
    if method == "tools/list":
        return {"tools": [{"name": "text_stats", "description": "统计文本字符数和行数",
                           "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                                           "required": ["text"], "additionalProperties": False}}]}
    if method == "tools/call" and params["name"] == "text_stats":
        text = params["arguments"]["text"]
        return {"content": [{"type": "text", "text": json.dumps(
            {"characters": len(text), "lines": len(text.splitlines())})}]}
    if method == "ping":
        return {}
    raise ValueError("未知请求")


if __name__ == "__main__":
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        reply = {"jsonrpc": "2.0", "id": request["id"]}
        try:
            reply["result"] = handle(request["method"], request.get("params", {}))
        except Exception as error:
            reply["error"] = {"code": -32602, "message": str(error)}
        print(json.dumps(reply), flush=True)

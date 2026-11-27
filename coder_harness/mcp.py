"""s14 在 s04 上增加 MCP stdio JSON-RPC 发现与动态注册。"""
import json
import queue
import re
import subprocess
import sys
import threading
from .runtime import ROOT, STRING, emit, read_json


class MCPClient:
    def __init__(self, command, args, cwd):
        self.lock = threading.RLock()
        self.sequence = 0
        self.responses = queue.Queue()
        self.process = subprocess.Popen([command, *args], cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, encoding="utf-8", bufsize=1)
        def reader():
            try:
                for line in self.process.stdout:
                    message = json.loads(line)
                    if "id" in message:
                        self.responses.put(message)
            except Exception as error:
                self.responses.put({"error": str(error)})
            finally:
                self.responses.put({"error": "MCP 服务连接已关闭"})
        threading.Thread(target=reader, daemon=True).start()
        try:
            self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                       "clientInfo": {"name": "coder", "version": "1.0"}})
            self.notify("notifications/initialized", {})
        except Exception:
            self.close()
            raise

    def notify(self, method, params):
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        self.process.stdin.flush()

    def request(self, method, params):
        with self.lock:
            self.sequence += 1
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.sequence,
                                                "method": method, "params": params}) + "\n")
            self.process.stdin.flush()
            try:
                message = self.responses.get(timeout=20)
            except queue.Empty:
                self.close()
                raise RuntimeError("MCP 请求超时，连接已关闭") from None
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            if message.get("id") != self.sequence:
                raise RuntimeError("MCP 响应 ID 不匹配")
            return message["result"]

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for handle in (self.process.stdin, self.process.stdout):
            if handle:
                handle.close()


def install(runtime):
    server_dir = ROOT / "mcp"
    config = read_json(server_dir / "mcp_servers.json", {})
    clients = {}
    def connect_mcp(server):
        if server in clients:
            return "已连接"
        settings = config[server]
        command = settings["command"].replace("{python}", sys.executable)
        args = [argument.replace("{server_dir}", str(server_dir)) for argument in settings["args"]]
        client = MCPClient(command, args, runtime.workspace)
        definitions = []
        try:
            params = {}
            while True:
                page = client.request("tools/list", params)
                definitions.extend(page["tools"])
                cursor = page.get("nextCursor")
                if not cursor:
                    break
                params = {"cursor": cursor}
            registrations = {}
            for definition in definitions:
                name = f"mcp__{server}__{definition['name']}"
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) or name in runtime.tools:
                    raise ValueError("MCP 工具名冲突或格式非法")
                original = definition["name"]
                def handler(_original=original, **arguments):
                    result = client.request("tools/call", {"name": _original, "arguments": arguments})
                    if result.get("isError"):
                        raise RuntimeError(str(result["content"]))
                    return result["content"]
                registrations[name] = ({"name": name, "description": definition.get("description", original),
                                        "input_schema": definition["inputSchema"]}, handler)
            runtime.tools.update(registrations)
            clients[server] = client
            runtime.closers.append(client.close)
        except Exception:
            client.close()
            raise
        emit("MCP · 工具发现", "\n".join(registrations))
        return list(registrations)
    runtime.add("connect_mcp", "连接配置中的 stdio MCP 服务并发现其工具，可用 demo", connect_mcp,
                {"server": STRING})
    runtime.parts.append(lambda: "MCP 可连接服务 " + ", ".join(config))
    runtime.services["mcp_clients"] = clients

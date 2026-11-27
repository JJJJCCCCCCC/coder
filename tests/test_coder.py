"""离线机制回归。仅 MCP 与 Shell 测试启动真实本地进程，模型全部使用替身。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from coder_harness import background
from coder_harness.harness import build
from coder_harness.runtime import ROOT, ContextTooLong, Runtime, read_json, shell_run


def answer(text="完成"):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def call(name, arguments, call_id="call-1"):
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


class CoderTests(unittest.TestCase):

    def setUp(self):
        # 保持 Git 工作树路径足够短，兼容 Windows Git 的内部路径限制。
        self.temporary = TemporaryDirectory(prefix="v-", dir=ROOT)
        self.directory = Path(self.temporary.name).resolve()
        self.runtimes = []
        self.output = redirect_stdout(io.StringIO())
        self.output.__enter__()


    def tearDown(self):
        for runtime in self.runtimes:
            runtime.close()
        self.output.__exit__(None, None, None)
        for attempt in range(8):
            try:
                self.temporary.cleanup()
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.3 * (attempt + 1))


    def runtime(self, client=None):
        home = self.directory / f"s{len(self.runtimes)}"
        runtime = Runtime(home, client=client or (lambda *args: answer()), workspace=home / "w",
                          shell="powershell" if sys.platform == "win32" else "bash")
        build(runtime)
        runtime.services["approve"] = lambda message: True
        self.runtimes.append(runtime)
        return runtime

    def tool(self, runtime, tool_name, **arguments):
        result = runtime.execute(call(tool_name, arguments))
        self.assertFalse(result.get("is_error"), result)
        return result["content"]


    def test_files_dispatch_and_tool_pairing(self):
        seen = []
        def client(messages, tools, system):
            seen.append(json.loads(json.dumps(messages)))
            if len(seen) == 1:
                return {"stop_reason": "tool_use", "content": [call("write_file", {"path": "a.txt", "content": "hello"}),
                         call("read_file", {"path": "a.txt"}, "call-2")]}
            self.assertEqual([result["tool_use_id"] for result in messages[-1]["content"]], ["call-1", "call-2"])
            self.assertEqual(messages[-1]["content"][1]["content"], "hello")
            return answer()
        runtime = self.runtime(client)
        self.assertEqual(runtime.run("写入并读回"), "完成")
        failed = runtime.execute(call("read_file", {"path": "../outside.txt"}))
        self.assertTrue(failed["is_error"])


    def test_permissions_hooks_and_todos(self):
        runtime = self.runtime()
        runtime.services["approve"] = lambda message: False
        self.tool(runtime, "write_file", path="a.txt", content="original")
        result = runtime.execute(call("write_file", {"path": "a.txt", "content": "no"}))
        self.assertTrue(result["is_error"])
        self.assertEqual((runtime.workspace / "a.txt").read_text(), "original")
        result = runtime.execute(call("bash", {"command": "sudo anything"}))
        self.assertTrue(result["is_error"])
        invalid = [{"text": str(i), "status": "in_progress"} for i in range(2)]
        self.assertTrue(runtime.execute(call("todo_write", {"items": invalid}))["is_error"])
        self.tool(runtime, "todo_write", items=[{"text": "阅读", "status": "completed"}])
        self.assertTrue((runtime.state / "audit.jsonl").exists())


    def test_subagent_gets_fresh_history_and_no_delegate_tool(self):
        def client(messages, tools, system):
            self.assertEqual(messages[0]["content"], "独立任务")
            self.assertNotIn("task", {tool["name"] for tool in tools})
            return answer("子任务完成")
        runtime = self.runtime(client)
        runtime.history.append({"role": "user", "content": "父历史"})
        self.assertEqual(self.tool(runtime, "task", prompt="独立任务"), "子任务完成")
        self.assertEqual(len(runtime.history), 1)


    def test_skill_catalog_is_lazy(self):
        runtime = self.runtime()
        self.assertIn("code-review", runtime.system())
        self.assertNotIn("先读代码，再分析", runtime.system())
        self.assertIn("先读代码", self.tool(runtime, "load_skill", name="code-review"))


    def test_compaction_keeps_pairs_and_archives_full_results(self):
        runtime = self.runtime()
        compactor = runtime.services["compactor"]
        messages = [{"role": "user", "content": "读取"},
                    {"role": "assistant", "content": [call("read_file", {"path": "a"})]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "x" * 20000}]}]
        compactor.prepare(messages)
        self.assertEqual(messages[-1]["content"][0]["tool_use_id"], "call-1")
        self.assertLess(len(messages[-1]["content"][0]["content"]), 3000)
        self.assertTrue(list((runtime.state / "outputs").glob("*.txt")))
        compactor.requested = True
        runtime.active_request = "当前任务"
        compactor.after_batch(messages)
        self.assertIn("当前任务", messages[0]["content"])


    def test_memory_persists(self):
        runtime = self.runtime()
        self.tool(runtime, "remember", text="使用类型注解")
        self.tool(runtime, "remember", text="使用类型注解")
        self.assertEqual(len(read_json(runtime.state / "memory.json", [])), 1)
        self.assertIn("类型注解", self.tool(runtime, "recall", query="类型"))
        restarted = Runtime(runtime.home, client=lambda *args: answer())
        build(restarted)
        self.runtimes.append(restarted)
        self.assertIn("类型注解", self.tool(restarted, "recall", query="类型"))


    def test_context_limit_retries_once(self):
        attempts = [0]
        def client(messages, tools, system):
            if not tools:
                return answer("工作摘要")
            attempts[0] += 1
            raise ContextTooLong("too long")
        runtime = self.runtime(client)
        with self.assertRaises(ContextTooLong):
            runtime.run("需要保留的目标")
        self.assertEqual(attempts[0], 2)
        self.assertIn("需要保留的目标", runtime.history[0]["content"])


    def test_task_graph_cycle_and_atomic_claim(self):
        runtime = self.runtime()
        board = runtime.services["board"]
        first, second = board.create("第一步"), board.create("第二步")
        board.update(second["id"], addBlockedBy=[first["id"]])
        before = (runtime.state / "tasks.json").read_bytes()
        with self.assertRaises(ValueError):
            board.update(first["id"], addBlockedBy=[second["id"]])
        self.assertEqual(before, (runtime.state / "tasks.json").read_bytes())
        def claim_one(owner):
            try:
                board.claim(first["id"], owner)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(claim_one, ["a", "b"])), 1)


    def test_background_returns_placeholder_and_notification(self):
        runtime = self.runtime()
        finished = threading.Event()
        def command(*args, **kwargs):
            finished.wait(1)
            return "退出码 0"
        with patch.object(background, "shell_run", command):
            result = json.loads(self.tool(runtime, "bash", command="echo hello", run_in_background=True))
            self.assertEqual(result["status"], "running")
            finished.set()
            event = runtime.events.get(timeout=2)
            self.assertIn(result["id"], event)


    def test_cron_validation_dedup_and_restart(self):
        runtime = self.runtime()
        scheduler = runtime.services["scheduler"]
        task_id = scheduler.schedule("* * * * *", "报告", durable=True)
        scheduler.poll(datetime(2026, 11, 23, 18, 0))
        scheduler.poll(datetime(2026, 11, 23, 18, 0, 20))
        self.assertEqual(runtime.events.qsize(), 1)
        self.assertTrue(read_json(scheduler.path, {})[task_id]["pending"])
        with self.assertRaises(ValueError):
            scheduler.schedule("60 * * * *", "错误")
        scheduler.cancel(task_id)
        self.assertNotIn(task_id, read_json(scheduler.path, {}))


    def test_team_plan_protocol_and_shutdown(self):
        counter = [0]
        def client(messages, tools, system):
            counter[0] += 1
            if counter[0] == 1:
                return {"stop_reason": "tool_use", "content": [call("submit_plan", {"plan": "读取并总结"})]}
            if counter[0] == 3:
                return {"stop_reason": "tool_use", "content": [
                    call("write_file", {"path": "team.txt", "content": "approved"}),
                    call("complete_task", {}, "complete-2")]}
            return answer()
        runtime = self.runtime(client)
        team = runtime.services["team"]
        task = team.board.create("阅读")
        team.spawn("reader", task["id"], auto_claim=False)
        deadline = time.monotonic() + 3
        while not team.protocols and time.monotonic() < deadline:
            time.sleep(0.01)
        request_id = next(iter(team.protocols))
        team.approve_plan(request_id, True)
        while team.board.tasks[task["id"]]["status"] != "completed" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(team.board.tasks[task["id"]]["status"], "completed")
        self.assertEqual((runtime.workspace / "team.txt").read_text(), "approved")
        child = team.members["reader"]["runtime"]
        # 计划审批只许可普通工作，不能让队友自动批准敏感操作。
        blocked = child.execute(call("bash", {"command": "Remove-Item -LiteralPath team.txt"}))
        self.assertTrue(blocked["is_error"])
        self.assertTrue((runtime.workspace / "team.txt").exists())
        team.members["reader"]["approved"] = False
        blocked = child.execute(call("write_file", {"path": "unapproved.txt", "content": "no"}))
        self.assertTrue(blocked["is_error"])
        self.assertFalse((runtime.workspace / "unapproved.txt").exists())
        shutdown_id = team.shutdown("reader")
        team.members["reader"]["thread"].join(timeout=3)
        self.assertEqual(team.protocols[shutdown_id]["status"], "acknowledged")


    def test_real_mcp_stdio(self):
        runtime = self.runtime()
        self.tool(runtime, "connect_mcp", server="demo")
        result = self.tool(runtime, "mcp__demo__text_stats", text="a\nb")
        self.assertIn('"characters": 3', json.loads(result)[0]["text"])


    @unittest.skipUnless(shutil.which("git"), "需要本地 Git")
    def test_task_bound_git_worktree(self):
        runtime = self.runtime()
        def git(*arguments):
            result = subprocess.run(["git", "-c", "core.longpaths=true", "-C", str(runtime.workspace), *arguments],
                                    capture_output=True, text=True, errors="replace", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
        git("init", "--template=")
        git("config", "core.longpaths", "true")
        git("config", "maintenance.auto", "false")
        git("config", "gc.auto", "0")
        (runtime.workspace / "tracked.txt").write_text("initial", encoding="utf-8")
        git("add", "tracked.txt")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")
        team = runtime.services["team"]
        task = team.board.create("独立修改")
        target = Path(team.worktree(task["id"], "test"))
        try:
            self.assertTrue((target / "tracked.txt").is_file())
            self.assertEqual(team.board.tasks[task["id"]]["worktree"], str(target))
        finally:
            self.assertTrue(target.resolve().is_relative_to(self.directory.resolve()))
            git("worktree", "remove", "--force", str(target))


    @unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "需要 PowerShell")
    def test_real_powershell_reads_workspace(self):
        runtime = self.runtime()
        runtime.shell = "powershell"
        (runtime.workspace / "sample.txt").write_text("hello-workspace", encoding="utf-8")
        result = shell_run(runtime, "Get-ChildItem -Name; Get-Content -LiteralPath sample.txt")
        self.assertIn("sample.txt", result)
        self.assertIn("hello-workspace", result)

    def test_integrated_tool_inventory_contains_s15_and_web_search(self):
        runtime = self.runtime()
        expected = set((
            "bash read_file write_file edit_file glob create_temp_file cleanup_temp_files "
            "todo_write task load_skill compact remember recall consolidate_memory "
            "create_task update_task list_tasks get_task schedule_cron list_crons cancel_cron "
            "spawn_teammate send_message list_teammates approve_plan request_shutdown create_worktree connect_mcp web_search"
        ).split())
        self.assertEqual(set(runtime.tools), expected)
        self.assertIn("start_scheduler", runtime.services)


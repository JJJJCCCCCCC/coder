"""s13 基于 s10，增加持久队友、文件邮箱、任务认领、计划协议和 worktree。"""
from pathlib import Path
import json
import queue
import re
import subprocess
import threading
import uuid
from .runtime import BOOL, STRING, Runtime, atomic_json, read_json, emit
from .kernel import install as install_kernel


class Team:
    def __init__(self, runtime):
        self.runtime = runtime
        self.board = runtime.services["board"]
        self.members = {}
        self.protocols = {}
        self.lock = threading.RLock()

    def mail(self, sender, recipient, content, kind="message"):
        if recipient != "lead" and recipient not in self.members:
            raise ValueError("收件队友不存在")
        event = {"id": uuid.uuid4().hex, "from": sender, "to": recipient,
                 "type": kind, "content": content}
        with self.lock:
            path = self.runtime.state / "mailboxes" / f"{recipient}.json"
            atomic_json(path, read_json(path, []) + [event])
        emit("团队 · 邮件投递", f"{sender} → {recipient} [{kind}]\n{content}")
        if recipient == "lead":
            self.runtime.events.put("团队事件 " + json.dumps(event, ensure_ascii=False))
        else:
            self.members[recipient]["inbox"].put(event)
        return event["id"]

    def spawn(self, name, task_id, auto_claim=True):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name) or name in ("lead", "agent"):
            raise ValueError("队友名需使用1-32位字母数字下划线，且不能为保留名称")
        with self.lock:
            if name in self.members:
                raise ValueError("队友已存在")
            task = self.board.claim(task_id, name)
            member = {"name": name, "status": "WORK", "task_id": task_id,
                      "inbox": queue.Queue(), "auto_claim": auto_claim, "approved": False,
                      "stop": threading.Event()}
            self.members[name] = member
            worker = threading.Thread(target=self.worker, args=(member, task), daemon=True)
            member["thread"] = worker
            worker.start()
        return {"name": name, "status": "WORK", "task_id": task_id}

    def worker(self, member, initial_task):
        parent = self.runtime
        child = Runtime(parent.home, client=parent.client, shell=parent.shell,
                        workspace=parent.workspace, label=f"队友 {member['name']}")
        install_kernel(child)
        member["runtime"] = child
        # 计划许可不等于敏感操作许可；后台线程不能代替用户同意删除。
        def require_plan(name, arguments):
            if name in {"bash", "write_file", "edit_file", "complete_task"} and not member["approved"]:
                raise PermissionError("请先提交计划并等待 Lead 批准")
        child.hooks.setdefault("PreToolUse", []).insert(0, require_plan)
        child.parts.append(lambda: "你是持久队友。先使用 submit_plan 提交计划，等待 Lead 批准。"
                           "完成任务后使用 complete_task。禁止修改任务图结构。"
                           "敏感操作无法在队友线程审批，请将具体命令交还 Lead 的主终端确认。")
        def submit_plan(plan):
            request_id = uuid.uuid4().hex[:12]
            with self.lock:
                self.protocols[request_id] = {"type": "plan", "name": member["name"],
                                              "task_id": member["task_id"], "status": "pending"}
            self.mail(member["name"], "lead", {"request_id": request_id, "plan": plan}, "plan_request")
            return "计划已提交，等待批准。结束当前回合以进入等待状态。"
        def complete_task():
            task_id = member["task_id"]
            with self.board.lock:
                if self.board.tasks[task_id]["owner"] != member["name"]:
                    raise PermissionError("只能完成自己认领的任务")
                return self.board.update(task_id, "completed")
        child.add("submit_plan", "向 Lead 提交计划，请批准后再修改文件或执行命令", submit_plan, {"plan": STRING})
        child.add("complete_task", "将当前认领任务标记为完成", complete_task)
        child.add("send_message", "向 Lead 或其他队友发消息",
                  lambda recipient, content: self.mail(member["name"], recipient, content),
                  {"recipient": STRING, "content": STRING})
        child.add("list_tasks", "查看共享任务板", lambda: list(self.board.tasks.values()))
        task = initial_task
        prompt = task["title"]
        try:
            while not parent.stop.is_set() and not member["stop"].is_set():
                if prompt:
                    member["status"] = "WORK"
                    child.workspace = Path(task.get("worktree", parent.workspace))
                    try:
                        answer = child.run(prompt)
                        self.mail(member["name"], "lead", answer, "result")
                    except Exception as error:
                        self.mail(member["name"], "lead", str(error), "failure")
                    member["status"] = "IDLE"
                    prompt = None
                try:
                    event = member["inbox"].get(timeout=0.5)
                    if event["type"] == "shutdown_request":
                        self.protocols[event["content"]]["status"] = "acknowledged"
                        self.mail(member["name"], "lead", event["content"], "shutdown_response")
                        break
                    prompt = "收到团队消息 " + json.dumps(event, ensure_ascii=False)
                except queue.Empty:
                    if not member["auto_claim"] or self.board.tasks[task["id"]]["status"] != "completed":
                        continue
                    with self.board.lock:
                        ready = next((item for item in self.board.tasks.values()
                                      if item["status"] == "pending" and self.board.ready(item)), None)
                        if ready:
                            task = self.board.claim(ready["id"], member["name"])
                            member.update(task_id=task["id"], approved=False)
                            prompt = task["title"]
        finally:
            member["status"] = "STOPPED"
            child.close()

    def approve_plan(self, request_id, approved):
        with self.lock:
            request = self.protocols[request_id]
            if request["type"] != "plan" or request["status"] != "pending":
                raise ValueError("计划请求不存在或已处理")
            request["status"] = "approved" if approved else "rejected"
            self.members[request["name"]]["approved"] = approved
            self.mail("lead", request["name"], request, "plan_response")
        return request

    def shutdown(self, name):
        request_id = uuid.uuid4().hex[:12]
        with self.lock:
            self.protocols[request_id] = {"type": "shutdown", "name": name, "status": "pending"}
            self.mail("lead", name, request_id, "shutdown_request")
        return request_id

    def worktree(self, task_id, branch):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", branch):
            raise ValueError("分支名只允许字母数字下划线和短横线")
        with self.board.lock:
            task = self.board.tasks[task_id]
            if task["status"] != "pending" or task.get("worktree"):
                raise ValueError("只能为尚未认领且未绑定的任务创建 worktree")
            target = self.runtime.state / "worktrees" / task_id
            result = subprocess.run(["git", "-C", str(self.runtime.workspace), "worktree", "add", "-b", branch,
                                     str(target)], capture_output=True, text=True, errors="replace", timeout=30)
            if result.returncode:
                raise RuntimeError(result.stderr)
            candidate = json.loads(json.dumps(self.board.tasks))
            candidate[task_id]["worktree"] = str(target)
            self.board.save(candidate)
        emit("团队 · 工作树绑定", f"任务 {task_id}\n{target}")
        return str(target)

    def close(self):
        for member in self.members.values():
            member["stop"].set()
            if "runtime" in member:
                member["runtime"].close()


def install(runtime):
    team = Team(runtime)
    runtime.services["team"] = team
    runtime.closers.append(team.close)
    runtime.add("spawn_teammate", "启动持久队友并认领已创建的任务，无需人工确认", team.spawn,
                {"name": STRING, "task_id": STRING, "auto_claim": BOOL}, ["name", "task_id"])
    runtime.add("send_message", "Lead 向队友发送消息",
                lambda recipient, content: team.mail("lead", recipient, content),
                {"recipient": STRING, "content": STRING})
    runtime.add("list_teammates", "查看团队状态", lambda: [
        {key: member[key] for key in ("name", "status", "task_id", "approved")} for member in team.members.values()])
    runtime.add("approve_plan", "Lead 根据收到的计划请求批准或拒绝后续写操作", team.approve_plan,
                {"request_id": STRING, "approved": BOOL})
    runtime.add("request_shutdown", "请求队友退出，等待带请求 ID 的回执", team.shutdown, {"name": STRING})
    runtime.add("create_worktree", "为待认领任务创建独立 Git 工作树，需要工作目录属于已提交的 Git 仓库",
                team.worktree, {"task_id": STRING, "branch": STRING})
    runtime.parts.append(lambda: "团队协作先创建任务并连接依赖，再启动就绪队友。"
                         "队友计划由 Lead 使用 approve_plan 批准，结果通过运行时通知自动回传。")

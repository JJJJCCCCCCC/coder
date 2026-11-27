"""s10 在 s04 上增加原子持久化的任务图和依赖约束。"""
import copy
import threading
import uuid
from .runtime import STRING, atomic_json, read_json, emit


class TaskBoard:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.tasks = read_json(path, {})

    def save(self, candidate):
        atomic_json(self.path, candidate)
        self.tasks = candidate

    def create(self, title):
        with self.lock:
            task_id = uuid.uuid4().hex[:10]
            item = {"id": task_id, "title": title, "status": "pending", "blockedBy": [], "owner": ""}
            self.save({**self.tasks, task_id: item})
            emit("任务图 · 创建", f"{task_id} {title}")
            return item

    def ready(self, task, tasks=None):
        tasks = self.tasks if tasks is None else tasks
        return all(tasks[key]["status"] == "completed" for key in task["blockedBy"])

    def update(self, task_id, status=None, addBlockedBy=None):
        with self.lock:
            candidate = copy.deepcopy(self.tasks)
            task = candidate[task_id]
            for dependency in addBlockedBy or []:
                if dependency not in candidate:
                    raise ValueError("依赖任务不存在")
                if dependency not in task["blockedBy"]:
                    task["blockedBy"].append(dependency)
            def visit(key, ancestors):
                if key in ancestors:
                    raise ValueError("任务依赖形成环")
                for dependency in candidate[key]["blockedBy"]:
                    visit(dependency, ancestors | {key})
            for key in candidate:
                visit(key, set())
            if status:
                if status not in ("pending", "in_progress", "completed"):
                    raise ValueError("未知任务状态")
                task["status"] = status
            for item in candidate.values():
                if item["status"] != "pending" and not self.ready(item, candidate):
                    raise ValueError("依赖未完成，不能执行或完成任务")
            self.save(candidate)
            emit("任务图 · 更新", str(task))
            return task

    def claim(self, task_id, owner):
        with self.lock:
            task = self.tasks[task_id]
            if task["status"] != "pending" or not self.ready(task):
                raise ValueError("任务尚未就绪或已被认领")
            candidate = copy.deepcopy(self.tasks)
            candidate[task_id].update(status="in_progress", owner=owner)
            self.save(candidate)
            emit("任务图 · 认领", f"{owner} 认领 {task_id}")
            return candidate[task_id]


def install(runtime):
    board = TaskBoard(runtime.state / "tasks.json")
    runtime.services["board"] = board
    runtime.add("create_task", "创建任务节点，返回运行时 ID。先创建所有节点再连接依赖", board.create,
                {"title": STRING})
    runtime.add("update_task", "更新任务状态或增加依赖，校验缺失依赖和环", board.update,
                {"task_id": STRING, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                 "addBlockedBy": {"type": "array", "items": STRING}}, ["task_id"])
    runtime.add("list_tasks", "查看持久任务图", lambda: list(board.tasks.values()))
    runtime.add("get_task", "按 ID 查看任务", lambda task_id: board.tasks[task_id], {"task_id": STRING})

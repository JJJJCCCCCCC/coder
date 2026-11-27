"""s11 在 s04 上增加 Bash 显式后台执行与完成通知。"""
import threading
import uuid
from .runtime import BOOL, STRING, emit, shell_run


def install(runtime):
    tasks = {}
    lock = threading.Lock()
    def bash(command, run_in_background=False):
        if not run_in_background:
            return shell_run(runtime, command)
        task_id = uuid.uuid4().hex[:10]
        with lock:
            tasks[task_id] = {"status": "running", "command": command}
        def worker():
            try:
                output = shell_run(runtime, command, timeout=300)
                status = "completed"
            except Exception as error:
                output, status = str(error), "failed"
            with lock:
                tasks[task_id].update(status=status, output=output)
            runtime.events.put(f"后台任务 {task_id} {status}\n{output}")
        threading.Thread(target=worker, daemon=True).start()
        emit("后台任务 · 已启动", f"{task_id}\n主循环可继续处理其他工具")
        return {"id": task_id, "status": "running"}
    runtime.add("bash", "运行终端命令，可显式设置 run_in_background=true 后台执行", bash,
                {"command": STRING, "run_in_background": BOOL}, ["command"])
    runtime.services["background"] = tasks

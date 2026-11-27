"""s06 在 s04 内核上增加一次性子 Agent，独立消息、共享工作区。"""
from .runtime import Runtime, STRING, emit

from .kernel import install as install_kernel


def install(runtime):
    def task(prompt):
        emit("子Agent · 启动", "使用全新消息列表，仅返回最终文本。")
        child = Runtime(runtime.home, client=runtime.client, workspace=runtime.workspace,
                        shell=runtime.shell, label="子Agent")
        install_kernel(child)
        child.services["approve"] = runtime.services.get("approve")
        try:
            answer = child.run(prompt)
            emit("子Agent · 完成", answer)
            return answer
        finally:
            child.close()
    runtime.add("task", "委派一个独立上下文的子任务。子Agent只有基础工具，不能递归委派", task,
                {"prompt": STRING})

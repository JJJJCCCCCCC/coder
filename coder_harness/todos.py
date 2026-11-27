"""s05 在 s04 内核上增加会话内待办计划。"""
from .runtime import emit


def install(runtime):
    items_schema = {"type": "array", "items": {"type": "object", "properties": {
        "text": {"type": "string"},
        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
        "required": ["text", "status"], "additionalProperties": False}}
    def todo_write(items):
        if len(items) > 20 or sum(item["status"] == "in_progress" for item in items) > 1:
            raise ValueError("最多20项待办，最多一项进行中")
        runtime.services["todos"] = items
        labels = {"pending": "待处理", "in_progress": "进行中", "completed": "已完成"}
        output = "\n".join(f"{i}. [{labels[item['status']]}] {item['text']}"
                           for i, item in enumerate(items, 1))
        emit("计划更新", output)
        return output
    runtime.add("todo_write", "创建或整体更新本轮待办，复杂任务先列计划，完成一步后更新状态", todo_write,
                {"items": items_schema})
    runtime.parts.append(lambda: "复杂任务使用 todo_write 列计划，并在执行后更新实际完成状态。")

"""s04 用生命周期与权限事件展示：调用前观察、权限拦截、成功后观察。"""
import json
import time
from .runtime import emit


def install(runtime):
    def record(event, data):
        emit(f"Hook · {event}", json.dumps(data, ensure_ascii=False))
        runtime.state.mkdir(parents=True, exist_ok=True)
        with (runtime.state / "audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": time.time(), "event": event, "data": data}, ensure_ascii=False) + "\n")
    def brief(arguments):
        # 保留命令和路径；大段文件内容仅记录长度，不重复输出整份源码。
        return {key: f"<{len(value)} 字符>" if key in {"content", "old_text", "new_text"}
                and isinstance(value, str) else value for key, value in arguments.items()}

    def before_tool(name, arguments):
        record("PreToolUse", {"tool": name, "arguments": brief(arguments), "status": "pending"})

    def permission_decision(name, arguments, decision, reason):
        record("PermissionDecision", {"tool": name, "decision": decision, "reason": reason})

    def permission_resolved(name, arguments, approved, reason):
        record("PermissionResolved", {"tool": name, "approved": approved, "reason": reason})

    def after_tool(name, arguments, result):
        record("PostToolUse", {"tool": name, "tool_use_id": result["tool_use_id"],
                               "status": "success", "result_preview": result["content"][:240]})

    def tool_error(name, arguments, result, phase):
        record("ToolError", {"tool": name, "tool_use_id": result["tool_use_id"], "phase": phase,
                             "status": "blocked" if phase == "pre_tool" else "error",
                             "error": result["content"][:500]})

    runtime.hook("UserPromptSubmit", lambda prompt: record("UserPromptSubmit", {"prompt": prompt}))
    # 观察器必须先于 s03 的权限 Hook；拒绝执行的调用也要留下 PreToolUse。
    runtime.hooks.setdefault("PreToolUse", []).insert(0, before_tool)
    runtime.hook("PermissionDecision", permission_decision)
    runtime.hook("PermissionResolved", permission_resolved)
    runtime.hook("PostToolUse", after_tool)
    runtime.hook("ToolError", tool_error)
    runtime.hook("Stop", lambda answer: record("Stop", {"status": "turn_completed"}))

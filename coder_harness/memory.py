"""s09 在 s04 上增加跨会话记忆选择、写入、检索、抽取和整理。"""
import uuid
from .runtime import STRING, atomic_json, read_json, emit


def install(runtime):
    path = runtime.state / "memory.json"
    memory = read_json(path, [])
    def remember(text, category="事实"):
        if not text.strip():
            raise ValueError("记忆不能为空")
        duplicate = next((item for item in memory if item["text"] == text), None)
        if duplicate:
            return duplicate
        item = {"id": uuid.uuid4().hex[:10], "text": text, "category": category}
        atomic_json(path, memory + [item])
        memory.append(item)
        emit("长期记忆 · 写入", text)
        return item
    def recall(query):
        matches = [item for item in memory if query.lower() in item["text"].lower()]
        emit("长期记忆 · 检索", f"命中 {len(matches)} 条")
        return matches
    def consolidate():
        unique = list({item["text"].strip(): item for item in memory}.values())
        atomic_json(path, unique)
        memory[:] = unique
        emit("长期记忆 · 整理", f"去重后 {len(memory)} 条")
        return memory
    runtime.add("remember", "明确保存可复用的用户偏好或项目事实", remember,
                {"text": STRING, "category": STRING}, ["text"])
    runtime.add("recall", "按子串检索长期记忆", recall, {"query": STRING})
    runtime.add("consolidate_memory", "整理记忆并去重", consolidate)
    def select(prompt):
        terms = prompt.lower().split()
        selected = [item for item in memory if any(term in item["text"].lower() for term in terms)]
        runtime.services["selected_memory"] = selected[-8:]
        emit("长期记忆 · 选择", f"总计 {len(memory)} 条，当前任务载入 {len(selected[-8:])} 条")
    runtime.hook("UserPromptSubmit", select)
    runtime.parts.append(lambda: "当前相关的持久记忆 " + str(runtime.services.get("selected_memory", [])))
    def extract(answer):
        # 抽取仅针对用户本轮明确表达的偏好和项目约束。
        schema = {"type": "object", "properties": {"facts": {"type": "array", "items": STRING}},
                  "required": ["facts"], "additionalProperties": False}
        try:
            result = runtime.ask_json("从用户输入提取值得长期保留的明确偏好或项目约束。"
                                      "忽略临时任务和密钥，没有则返回空数组。\n" + runtime.active_request, schema)
            for fact in result["facts"][:5]:
                remember(fact, "自动抽取")
            emit("长期记忆 · 抽取", f"发现 {len(result['facts'])} 条候选")
        except Exception as error:
            emit("长期记忆 · 抽取失败", str(error))
    runtime.hook("Stop", extract)

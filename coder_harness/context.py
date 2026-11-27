"""s08 在 s04 上增加输出转存、完整回合裁剪、旧结果收缩和模型摘要。"""
import json
import uuid
from .runtime import emit, text_of, atomic_json


class Compactor:
    def __init__(self, runtime):
        self.runtime = runtime
        self.limit = 50_000
        self.requested = False

    def size(self, messages):
        return len(json.dumps(messages, ensure_ascii=False))

    def save(self, content):
        path = self.runtime.state / "outputs" / (uuid.uuid4().hex + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    def summary(self, messages):
        before = self.size(messages)
        archive = self.runtime.state / "transcripts" / (uuid.uuid4().hex + ".json")
        atomic_json(archive, messages)
        response = self.runtime.model(
            [{"role": "user", "content": json.dumps(messages, ensure_ascii=False)}], tools=[],
            system="用中文压缩工作记录。保留用户目标、约束、已执行操作及结果、文件路径、待办。仅输出摘要。")
        summary = text_of(response)
        if not summary.strip():
            raise RuntimeError("摘要为空，原对话保留")
        messages[:] = [{"role": "user", "content": f"前序工作摘要\n{summary}\n完整记录 {archive}\n"
                       f"当前任务 {self.runtime.active_request}"}]
        emit("上下文压缩 · 模型摘要", f"字符数 {before} → {self.size(messages)}\n完整记录 {archive}")

    def prepare(self, messages):
        before = self.size(messages)
        # 最新结果单独做预算，回合中的每个调用 ID 及结果都被保留。
        for message in messages:
            if isinstance(message["content"], list):
                for block in message["content"]:
                    content = block.get("content", "")
                    if block["type"] == "tool_result" and isinstance(content, str) and len(content) > 15_000:
                        path = self.save(content)
                        block["content"] = content[:2000] + f"\n完整结果 {path}"
                        emit("上下文压缩 · 大结果转存", path)
        if len(messages) > 40:
            starts = [i for i, message in enumerate(messages)
                      if message["role"] == "user" and isinstance(message["content"], str)]
            if len(starts) > 5:
                archive = self.runtime.state / "transcripts" / (uuid.uuid4().hex + ".json")
                atomic_json(archive, messages)
                messages[:] = [messages[0], {"role": "assistant", "content": "较早的回合已归档。"},
                               {"role": "user", "content": f"历史归档 {archive}"}, *messages[starts[-4]:]]
                emit("上下文压缩 · 完整回合裁剪", str(archive))
        if self.size(messages) > self.limit:
            results = [block for message in messages if isinstance(message["content"], list)
                       for block in message["content"] if block["type"] == "tool_result"]
            for block in results[:-3]:
                if len(block.get("content", "")) > 500:
                    block["content"] = f"旧结果已转存 {self.save(block['content'])}"
            emit("上下文压缩 · 旧结果收缩", f"{before} → {self.size(messages)} 字符")
        if self.size(messages) > self.limit:
            self.summary(messages)

    def after_batch(self, messages):
        if self.requested:
            self.requested = False
            self.summary(messages)


def install(runtime):
    compactor = Compactor(runtime)
    runtime.services["compactor"] = compactor
    runtime.prepare.append(compactor.prepare)
    runtime.after_batch.append(compactor.after_batch)
    def compact():
        compactor.requested = True
        return "压缩请求已登记，等本批所有工具结果闭合后生成摘要。"
    runtime.add("compact", "在阶段完成后压缩历史，保留目标、执行证据和恢复路径", compact)

"""s02 在 s01 上添加结构化文件工具，通过注册表分派。"""
from .runtime import STRING


def install(runtime):
    def read_file(path):
        with runtime.path(path).open(encoding="utf-8") as handle:
            return handle.read(100_000)

    def write_file(path, content):
        target = runtime.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {path}，{len(content)} 字符"

    def edit_file(path, old_text, new_text):
        target = runtime.path(path)
        content = target.read_text(encoding="utf-8")
        if not old_text or content.count(old_text) != 1:
            raise ValueError("被替换文本必须在文件中唯一出现")
        target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"已修改 {path}"

    def glob(pattern):
        return "\n".join(str(path.relative_to(runtime.workspace))
                         for path in sorted(runtime.workspace.glob(pattern))[:200]
                         if path.resolve().is_relative_to(runtime.workspace))

    runtime.add("read_file", "读取练习目录中的 UTF-8 文件，最多100000字符", read_file, {"path": STRING})
    runtime.add("write_file", "创建或覆盖 UTF-8 文件", write_file, {"path": STRING, "content": STRING})
    runtime.add("edit_file", "将唯一出现的文本替换一次", edit_file,
                {"path": STRING, "old_text": STRING, "new_text": STRING})
    runtime.add("glob", "按相对模式查找文件，例如 **/*.py", glob, {"pattern": STRING})

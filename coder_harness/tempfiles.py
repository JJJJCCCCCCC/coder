"""本运行时创建的中间文件：明确来源、精确清理，不按文件名猜测所有权。"""
import ast
import re
import shlex
import threading
import uuid

from .runtime import STRING


class TemporaryFiles:
    def __init__(self, runtime):
        self.runtime = runtime
        self.files = {}
        self.script_outputs = {}
        self.lock = threading.RLock()

    def create(self, suffix=".tmp", content="", temporary_outputs=None):
        if not re.fullmatch(r"\.[A-Za-z0-9_-]{1,16}", suffix):
            raise ValueError("suffix 应为 .json、.py 或 .tmp 等简单扩展名")
        with self.lock:
            relative = f".agent_tmp/{uuid.uuid4().hex}{suffix}"
            path = self.runtime.path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content)
            stat = path.stat()
            self.files[path] = (stat.st_dev, stat.st_ino)
            self.script_outputs[path] = temporary_outputs
            return {"path": relative, "purpose": "任务中间文件，可直接覆盖和清理"}

    def python_outputs(self, source):
        """只解析字面量写入路径，不执行脚本，也不按 tmp/probe 等名称猜测。"""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set()
        bindings = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bindings.setdefault(target.id, []).append(node.value)

        def literal(node, seen=frozenset()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name) and node.id not in seen:
                values = bindings.get(node.id, [])
                if len(values) == 1:
                    return literal(values[0], seen | {node.id})
            if isinstance(node, ast.Call) and node.args and (
                    isinstance(node.func, ast.Name) and node.func.id == "Path" or
                    isinstance(node.func, ast.Attribute) and node.func.attr == "Path"):
                if len(node.args) == 1:
                    return literal(node.args[0], seen)
            return None

        outputs = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            path = None
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"write_text", "write_bytes"}:
                path = literal(node.func.value)
            elif (isinstance(node.func, ast.Name) and node.func.id == "open" or
                  isinstance(node.func, ast.Attribute) and node.func.attr == "open"):
                builtin = isinstance(node.func, ast.Name) or literal(node.func.value) is None
                mode_index = 1 if builtin else 0
                mode = next((literal(k.value) for k in node.keywords if k.arg == "mode"), None)
                if mode is None and len(node.args) > mode_index:
                    mode = literal(node.args[mode_index])
                if mode and any(flag in mode for flag in "wax"):
                    if builtin:
                        path = literal(node.args[0]) if node.args else next(
                            (literal(k.value) for k in node.keywords if k.arg == "file"), None)
                    else:
                        path = literal(node.func.value)
            if path:
                outputs.add(path)
        return outputs

    def begin_tool(self, name, arguments):
        if name != "bash":
            return []
        try:
            tokens = shlex.split(arguments.get("command", ""), posix=False)
        except ValueError:
            return []
        # cd 后的相对路径和复杂包装不能可靠归因，宁可不自动登记。
        if re.search(r"(?:^|[;&|]\s*)\s*(?:cd|pushd|popd|set-location)\b",
                     arguments.get("command", ""), re.I):
            return []
        pending = set()
        for index, token in enumerate(tokens[:-1]):
            executable = re.split(r"[/\\]", token.strip("\"'").lower())[-1]
            if not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", executable):
                continue
            cursor = index + 1
            while cursor < len(tokens) and tokens[cursor] in {"-u", "-B", "-S", "-s", "-E", "-I", "-O", "-OO", "--"}:
                cursor += 1
            if cursor >= len(tokens):
                continue
            script_name = tokens[cursor].strip("\"'")
            if not self.owns(script_name):
                continue
            script = self.runtime.path(script_name)
            declared = self.script_outputs.get(script)
            outputs = declared if declared is not None else self.python_outputs(script.read_text(encoding="utf-8"))
            for output in outputs:
                try:
                    path = self.runtime.path(output)
                    if not path.exists() and not path.is_symlink():
                        pending.add(path)
                except (ValueError, OSError):
                    continue
        return pending

    def finish_tool(self, pending):
        with self.lock:
            for path in pending:
                try:
                    if path.is_symlink() or not path.resolve().is_relative_to(self.runtime.workspace):
                        continue
                    if path.is_file():
                        stat = path.stat()
                        self.files[path] = (stat.st_dev, stat.st_ino)
                except OSError:
                    continue

    def owns(self, name):
        try:
            original = self.runtime.workspace / name
            if original.is_symlink():
                return False
            path = self.runtime.path(name)
            with self.lock:
                identity = self.files.get(path)
                if identity is None or not path.is_file():
                    return False
                stat = path.stat()
                return identity == (stat.st_dev, stat.st_ino)
        except (ValueError, OSError):
            return False

    def cleanup(self, paths=None):
        with self.lock:
            selected = paths if paths is not None else [
                str(path.relative_to(self.runtime.workspace)) for path in self.files]
            targets = []
            # 先完整校验，不能顺手将用户文件混在清理列表中删除。
            for name in selected:
                path = self.runtime.path(name)
                if path not in self.files:
                    raise PermissionError(f"不是本运行时创建的临时文件：{name}")
                if path.exists() or path.is_symlink():
                    if not self.owns(name):
                        raise PermissionError(f"临时路径已被替换，不能自动清理：{name}")
                    targets.append((name, path))
            for name, path in targets:
                path.unlink()
            for name in selected:
                self.files.pop(self.runtime.path(name), None)
                self.script_outputs.pop(self.runtime.path(name), None)
            return {"cleaned": [name for name, path in targets]}


def install(runtime):
    store = TemporaryFiles(runtime)
    runtime.services["temporary_files"] = store
    runtime.add("create_temp_file", "创建任务中间文件，返回工作目录内的唯一相对路径。"
                "临时 Python 分析脚本的字面量文件输出会自动登记；动态路径请用 temporary_outputs 声明。"
                "正式交付文件使用 write_file；若脚本生成正式交付文件，传 temporary_outputs=[] 禁用自动归类。",
                store.create, {"suffix": STRING, "content": STRING,
                               "temporary_outputs": {"type": "array", "items": STRING}}, [])
    runtime.add("cleanup_temp_files", "清理本运行时通过 create_temp_file 创建的中间文件，无需确认。"
                "不传 paths 清理全部已登记临时文件；不会清理用户文件或目录。", store.cleanup,
                {"paths": {"type": "array", "items": STRING}}, [])

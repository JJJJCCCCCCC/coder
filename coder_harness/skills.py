"""s07 在 s04 上添加技能目录和按需加载。"""
from .runtime import ROOT, STRING, emit


def install(runtime):
    skill_root = ROOT / "skills"
    registry = {}
    for path in sorted(skill_root.glob("*/SKILL.md")):
        content = path.read_text(encoding="utf-8")
        description = content.splitlines()[0].lstrip("# ")
        registry[path.parent.name] = (path, description)
    runtime.parts.append(lambda: "可用技能目录（仅名称和简介）\n" + "\n".join(
        f"{name} · {item[1]}" for name, item in registry.items()) + "\n需要时调用 load_skill。")
    def load_skill(name):
        path, _ = registry[name]
        emit("技能加载", f"按需读取 {name} 正文，作为工具结果进入上下文")
        return path.read_text(encoding="utf-8")
    runtime.add("load_skill", "按名称加载技能完整正文", load_skill, {"name": STRING})

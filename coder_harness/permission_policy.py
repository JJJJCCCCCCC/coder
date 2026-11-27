"""按操作风险分类；这是教学用启发式策略，不是 Shell 沙箱。"""
import re
import shlex


SYSTEM = {"sudo", "su", "runas", "mkfs", "format", "format-volume", "diskpart",
          "shutdown", "reboot", "restart-computer", "stop-computer"}
SENSITIVE = {"rm", "rmdir", "rd", "del", "erase", "remove-item", "ri", "unlink",
             "remove-itemproperty", "clear-content", "clear-item", "truncate",
             "mv", "move", "move-item", "ren", "rename", "rename-item",
             "chmod", "chown", "icacls", "set-acl", "stop-process", "kill",
             "taskkill", "reg", "invoke-expression", "iex"}
SHELLS = {"bash", "sh", "zsh", "powershell", "pwsh", "cmd"}

# 参数含义属于各自命令：find -iname 是查询，sed/perl -i 才是原地编辑。
FIND_ACTIONS = {"-delete", "-exec", "-execdir", "-ok", "-okdir",
                "-fprint", "-fprint0", "-fprintf", "-fls"}
FILE_OPTION_ARGUMENTS = {
    "find": {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename",
             "-regex", "-iregex", "-lname", "-ilname", "-printf", "-regextype",
             "-maxdepth", "-mindepth", "-type", "-size", "-user", "-group"},
    "sed": {"-e", "--expression", "-f", "--file"},
    "perl": {"-e", "-E", "-I", "-M", "-m", "-F"},
}


def in_place_option(executable, token):
    if executable == "sed" and (token == "--in-place" or token.startswith("--in-place=")):
        return True
    if not token.startswith("-") or token.startswith("--"):
        return False
    # 识别 -i.bak、sed -ni、perl -wpi 等短选项组合；大小写有意义。
    # 遇到携带脚本/路径参数的选项即停止，避免把 -Ilib、-eprint 中的 i 当选项。
    no_argument_flags = "nErzsub" if executable == "sed" else "pnacwlWTU0123456789"
    for flag in token[1:]:
        if flag == "i":
            return True
        if flag not in no_argument_flags:
            return False
    return False


def owned_temp(runtime, name):
    store = runtime.services.get("temporary_files")
    return store is not None and store.owns(name)


def temporary_cleanup(runtime, tokens):
    """只放行简单删除命令中的精确临时文件路径，不放行管道或通配符。"""
    if not tokens:
        return False
    executable = re.split(r"[/\\]", tokens[0].strip("\"'").lower())[-1].removesuffix(".exe")
    if executable not in {"rm", "del", "erase", "unlink", "remove-item", "ri"}:
        return False
    paths = []
    for raw in tokens[1:]:
        token = raw.strip("\"'")
        if token.lower() in {"--", "--force", "--verbose", "-force", "-literalpath", "-path", "/q", "/f"}:
            continue
        if executable == "rm" and re.fullmatch(r"-[frv]+", token):
            continue
        if not token or token.startswith("-") or any(char in token for char in "*?[]$`;|&<>\n\r"):
            return False
        paths.append(token)
    return bool(paths) and all(owned_temp(runtime, path) for path in paths)


def shell_decision(runtime, command, depth=0, temporary_scope=True):
    """普通命令默认允许，只对识别到的敏感行为询问。"""
    if depth > 5:
        return "ask", "嵌套命令过深，需要人工检查"
    try:
        lexer = shlex.shlex(command, posix=False, punctuation_chars=";|&()<>\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)
    except ValueError:
        return "ask", "命令引号无法解析"
    if temporary_scope and temporary_cleanup(runtime, tokens):
        return "allow", "精确清理本运行时创建的任务中间文件"
    decision, reason = "allow", "常规命令"
    start = True
    executable = ""
    segment = []
    file_argument_pending = False
    cwd_changed = False
    for index, raw in enumerate(tokens):
        token = raw.strip("\"'")
        lower = token.lower()
        if raw and all(char in ";|&()\n" for char in raw):
            start, segment = True, []
            file_argument_pending = False
            continue
        if raw in (">", ">>") and index + 1 < len(tokens):
            destination = tokens[index + 1].strip("\"'")
            if destination.lower() not in ("/dev/null", "nul", "$null"):
                try:
                    target = runtime.path(destination)
                    if raw == ">" and target.exists() and not owned_temp(runtime, destination):
                        decision, reason = "ask", "重定向将覆盖已有文件"
                except ValueError:
                    decision, reason = "ask", "重定向写入工作目录外"
        if start:
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
                continue
            executable = re.split(r"[/\\]", lower)[-1].removesuffix(".exe")
            if executable in {"cd", "pushd", "popd", "set-location", "sl"}:
                cwd_changed = True
            start = False
            segment = [executable]
            if executable in SYSTEM or executable.startswith("mkfs."):
                return "deny", "提权、磁盘格式化或系统关机操作"
            if executable in SENSITIVE:
                end = index + 1
                while end < len(tokens) and not all(char in ";|&()\n" for char in tokens[end]):
                    end += 1
                if temporary_scope and not cwd_changed and temporary_cleanup(runtime, tokens[index:end]):
                    if decision == "allow":
                        reason = "精确清理已登记临时文件；其余子命令继续独立检查"
                else:
                    decision, reason = "ask", "删除、移动或更改系统状态"
            continue
        segment.append(lower)
        if executable == "git":
            if lower in {"clean", "reset", "restore", "rebase", "push"} or (
                    lower in {"-d", "--delete", "--force", "-f"} and
                    any(item in segment for item in ("branch", "tag", "checkout"))) or (
                    lower == "--" and "checkout" in segment):
                decision, reason = "ask", "可能丢弃代码、改写历史或发布的 Git 操作"
        if executable in FILE_OPTION_ARGUMENTS:
            if file_argument_pending:
                # -iname 的模式、sed -e 的脚本等不是独立命令选项。
                file_argument_pending = False
            else:
                file_argument_pending = token in FILE_OPTION_ARGUMENTS[executable]
                if executable == "find" and token in FIND_ACTIONS:
                    decision, reason = "ask", "find 包含删除、执行命令或写出文件操作"
                elif executable in {"sed", "perl"} and in_place_option(executable, token):
                    decision, reason = "ask", f"{executable} 使用原地改写选项"
        if executable in {"pip", "pip3", "npm", "pnpm", "yarn", "uv"} and lower in {
                "uninstall", "remove", "publish"}:
            decision, reason = "ask", "卸载依赖或发布软件包"
        if executable in {"set-content", "out-file", "tee-object", "sc"} and not lower.startswith("-"):
            try:
                if runtime.path(token).exists() and not owned_temp(runtime, token):
                    decision, reason = "ask", "命令可能覆盖已有文件"
            except ValueError:
                decision, reason = "ask", "命令可能写入工作目录外"
        if executable in SHELLS and index > 0 and tokens[index - 1].lower() in {
                "-c", "-command", "/c"}:
            nested, why = shell_decision(runtime, token, depth + 1, temporary_scope and not cwd_changed)
            if nested == "deny":
                return nested, why
            if nested == "ask":
                decision, reason = nested, why
        if executable in SHELLS and lower in {"-encodedcommand", "-enc"}:
            decision, reason = "ask", "编码脚本需要人工检查"
        if executable.startswith(("python", "node", "ruby", "perl")):
            if re.search(r"\b(unlink|rmtree|rmdir|remove|rename|replace|rmSync|unlinkSync)\s*\(", token):
                decision, reason = "ask", "脚本包含删除或替换文件操作"
            if lower == "uninstall" and "pip" in segment:
                decision, reason = "ask", "脚本卸载依赖"
    return decision, reason


def classify(runtime, name, arguments):
    if name == "web_search":
        return "allow", "通过 DeepSeek 检索公开网页"
    if name == "cleanup_temp_files":
        return "allow", "清理已登记的任务中间文件；执行器逐一校验文件来源"
    if name == "bash":
        return shell_decision(runtime, arguments.get("command", ""))
    if name == "write_file":
        target = runtime.path(arguments["path"])
        if target.exists() and not owned_temp(runtime, arguments["path"]) and target.read_text(encoding="utf-8") != arguments["content"]:
            return "ask", "整体覆盖已有文件；常规修改请使用 edit_file"
    if name.startswith("mcp__") and re.search(
            r"(?:^|_)(delete|remove|drop|destroy|publish|send)(?:_|$)", name.lower()):
        return "ask", "外部工具包含删除或对外发送操作"
    return "allow", "读取、创建或常规编辑"

"""s03 在工具调用前加入 allow / ask / deny 权限决策。"""
from .runtime import emit
from .permission_policy import classify
from .tempfiles import install as install_tempfiles


def install(runtime):
    install_tempfiles(runtime)
    def permission(name, arguments):
        decision, reason = classify(runtime, name, arguments)
        runtime.trigger("PermissionDecision", name=name, arguments=arguments,
                        decision=decision, reason=reason)
        if decision == "deny":
            emit("权限拒绝", f"工具 {name}：{reason}")
            raise PermissionError(reason)
        if decision == "ask":
            emit("敏感操作确认", f"工具 {name}：{reason}")
            # 输入统一由 CLI 输入线程持有，审批通过专用回调交付。
            approve = runtime.services.get("approve")
            if approve is None:
                runtime.trigger("PermissionResolved", name=name, arguments=arguments,
                                approved=False, reason="无交互审批回调")
                raise PermissionError("当前执行路径无法交互审批，请在主终端授权后重试")
            approved = bool(approve(f"{reason}。允许执行 {name} {arguments}？输入 y 同意"))
            runtime.trigger("PermissionResolved", name=name, arguments=arguments,
                            approved=approved, reason=reason)
            if not approved:
                raise PermissionError("用户拒绝执行")
    runtime.hook("PreToolUse", permission)
    runtime.parts.append(lambda: "读取、创建文件、edit_file 常规编辑及普通命令直接执行，无需询问。"
                         "删除、移动、覆盖已有文件、破坏性 Git 操作和对外发布需要用户确认。"
                         "任务自行产生的中间文件使用 create_temp_file 创建，使用返回的相对路径保存数据或脚本，"
                         "处理完成后调用 cleanup_temp_files 直接清理，不要请求用户确认。"
                         "临时 Python 分析脚本生成的字面量输出也会登记；动态输出路径通过 temporary_outputs 声明。"
                         "如果用脚本生成用户要求保留的正式交付文件，创建脚本时传 temporary_outputs=[]。"
                         "这些临时文件可直接覆盖或用精确路径删除；用户原有文件与正式交付文件不享受此豁免。"
                         "已有文件优先使用 edit_file 精确修改，禁止绕过被拒绝的敏感操作。")

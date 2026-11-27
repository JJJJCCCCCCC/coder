"""Assemble the mechanisms from learn-cc s15 on a single runtime."""

from . import background, context, kernel, mcp, memory, scheduler, skills, subagents, tasks, teams, todos, web_search


def build(runtime):
    kernel.install(runtime)
    for module in (todos, subagents, skills, context, memory, tasks, background, scheduler, teams, mcp, web_search):
        module.install(runtime)

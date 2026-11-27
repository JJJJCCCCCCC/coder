"""Base tools, permission checks, and lifecycle audit shared by all agents."""

from . import files, hooks, permissions
from .runtime import install_shell


def install(runtime):
    install_shell(runtime)
    files.install(runtime)
    permissions.install(runtime)
    hooks.install(runtime)

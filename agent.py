"""Command-line entry point for the standalone Coder agent."""

from coder_harness.harness import build
from coder_harness.runtime import cli


if __name__ == "__main__":
    cli(__file__, build)

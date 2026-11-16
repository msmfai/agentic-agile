"""What an agent that the engine starts can reach.

Each planner, reviewer and worker is a fresh process. Its plan or its finding
must come from the goal, the findings and the project, so the agent starts with
no link to the session that drives the run and no view of earlier plans. This
module owns the three parts of that:

- the environment: the variables that link a process to the session that
  started it (session IDs, messaging sockets, terminal sockets) leave it;
- the host flags: no hooks, no auto memory, no MCP servers or connectors, and a
  fixed tool list;
- the paths: the state folder, which holds every earlier plan, and the host
  folders that hold session transcripts and memory are closed to the file tools.

The CLI adds the fourth part: a planner runs only the commands that write a
plan, and a reviewer runs none (see `cli._guard_role`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Variables that tie a process to the session, terminal or editor that started it.
SESSION_VARIABLES = {
    "CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_PROJECT_DIR", "CLAUDE_ENV_FILE",
    "TMUX", "TMUX_PANE", "STY", "TERM_SESSION_ID", "ITERM_SESSION_ID", "KITTY_LISTEN_ON", "KITTY_WINDOW_ID",
    "WEZTERM_UNIX_SOCKET", "WEZTERM_PANE", "NVIM", "NVIM_LISTEN_ADDRESS", "VSCODE_IPC_HOOK_CLI",
    "CODEX_THREAD_ID", "OPENCODE", "AGENTIC_AGILE_STATE", "AGENTIC_AGILE_RUN_STATE", "AGENTIC_AGILE_TASK",
}
SESSION_PREFIXES = ("CMUX_", "CLAUDE_CODE_", "ZELLIJ", "CODEX_SESSION", "OPENCODE_SESSION", "OPENCODE_SERVER")
# Claude Code variables that choose the provider or carry its credentials. They stay.
# The user's own switches (CLAUDE_CODE_DISABLE_...) stay too, so a privacy setting holds for every agent.
CLAUDE_CODE_KEEP = ("CLAUDE_CODE_USE_", "CLAUDE_CODE_OAUTH_", "CLAUDE_CODE_API_KEY_", "CLAUDE_CODE_CLIENT_",
                    "CLAUDE_CODE_SKIP_", "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_CODE_DISABLE_")

# Host folders with session transcripts, memory and settings of every session on this machine.
HOST_DATA = ("~/.claude/**", "~/.codex/**", "~/.config/opencode/**", "~/.local/share/opencode/**")


def is_session_variable(name: str) -> bool:
    if name in SESSION_VARIABLES:
        return True
    if name.startswith("CLAUDE_CODE_"):
        return not name.startswith(CLAUDE_CODE_KEEP)
    return name.startswith(SESSION_PREFIXES)


def agent_env(extra: dict[str, str], base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of an engine agent: this one without session links, plus `extra`.

    `CMUX_CLAUDE_HOOKS_DISABLED` and the auto-memory switch are set again after the
    cleaning, so the cmux wrapper passes straight to claude and claude keeps no memory.
    """
    source = os.environ if base is None else base
    env = {name: value for name, value in source.items() if not is_session_variable(name)}
    env.update({"CMUX_CLAUDE_HOOKS_DISABLED": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
    env.update(extra)
    return env


def _rules(tools: tuple[str, ...], paths: list[str]) -> list[str]:
    return [f"{tool}({path})" for path in paths for tool in tools]


def _absolute(path: Path) -> str:
    """A path in Claude Code permission syntax, where `//` starts an absolute path."""
    return "/" + str(Path(path).resolve()) + "/**"


def claude_settings(state_dir: Path | None, worker: bool) -> str:
    """The --settings JSON: hooks off, auto memory off, and the closed paths."""
    closed = [*HOST_DATA]
    if state_dir is not None:
        closed.append(_absolute(state_dir))
    # A planner never needs a state folder, so every .agentic-agile folder is closed. A worker
    # can need one for its own experiments, so only the one in its worktree is closed.
    closed.append("./.agentic-agile/**" if worker else "**/.agentic-agile/**")
    return json.dumps({"disableAllHooks": True, "autoMemoryEnabled": False,
                       "permissions": {"deny": _rules(("Read", "Grep", "Glob", "Edit", "Write"), closed)}})


def claude_planner_flags(state_dir: Path | None) -> list[str]:
    """Planner and reviewer. `--safe-mode` drops CLAUDE.md files, skills, plugins and memory;
    `--restricted` keeps the file tools inside the project folder; `--tools` lists the only tools."""
    return ["--safe-mode", "--restricted", "--strict-mcp-config", "--tools", "Bash,Read,Glob,Grep",
            "--settings", claude_settings(state_dir, worker=False), "--include-hook-events"]


def claude_worker_flags(state_dir: Path | None) -> list[str]:
    """Worker. The project's own settings and CLAUDE.md stay, because they describe the project.
    User settings, memory, MCP servers and the session tools (messages, agents) are out."""
    return ["--setting-sources", "project", "--strict-mcp-config",
            "--tools", "Bash,Edit,Write,Read,Glob,Grep,Task",
            "--settings", claude_settings(state_dir, worker=True), "--include-hook-events"]


# Codex: no hooks, plugins, memory, connected apps or control of the browser and computer,
# no user config (its MCP servers), and no session files.
CODEX_FLAGS = ["--disable", "hooks", "--disable", "plugins", "--disable", "memories", "--disable", "apps",
               "--disable", "browser_use", "--disable", "computer_use", "--ignore-user-config", "--ephemeral"]
# OpenCode: no external plugins.
OPENCODE_FLAGS = ["--pure"]

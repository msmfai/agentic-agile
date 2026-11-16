# Agentic Agile reference for agents

The goal belongs to the user and stays fixed. The plan is an estimate. Each
task has one parent (a `group` task or the goal) and can depend on other
tasks. The depends-on links control which tasks are ready. The links always
form a graph that has a clear order.

When you complete a task, the tasks that are still waiting are cleared.
Completed tasks and findings stay. The IDs of cleared tasks are free to use
again.

What you are told in each state:

1. the plan is empty: the goal, the findings, and the instruction to write the plan;
2. tasks are ready to start: the ready tasks and their reasons;
3. a task is in progress: the goal, the findings, the parent tasks, and the
   task with its done condition, its reasons, and its checks.

The data is a sequence of JSON event files in `.agentic-agile/events/`. When
the repository has a `meta` branch checked out as a worktree, the data is
there and each event is committed automatically. In all other repositories,
the data is in `.agentic-agile/` at the top of the repository and you control
the commits. The SQLite file is an index that the tool builds from the events.

```text
agentic-agile init --goal TEXT --done TEXT [--constraints TEXT]
agentic-agile add ID --title TEXT --objective TEXT --done TEXT --why TEXT
             [--parent ID] [--kind group|task|investigation|human|validation] [--priority N]
agentic-agile depends TASK REQUIRED_TASK [--why TEXT]
agentic-agile gate-add TASK --label TEXT -- COMMAND ARG...
agentic-agile next [TASK] --why TEXT --expect TEXT
agentic-agile complete [TASK] --learned TEXT
agentic-agile discard --why TEXT
agentic-agile note finding|surprise|evidence|decision|blocker TEXT [--node TASK]
agentic-agile root-complete --why TEXT
agentic-agile context [TASK]
agentic-agile frontier | tree | statusline | watch [--once] | status [--json] | view [--graph]
agentic-agile goal [status|pause|resume]
agentic-agile rebuild
```

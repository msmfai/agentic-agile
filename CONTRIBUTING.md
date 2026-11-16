# Contributing

Agentic Agile runs on Python 3.9 or later and Git, with the standard library.
[AGENTS.md](AGENTS.md) maps the code and shows how to make common changes.
The development loop:

```sh
python3 -m unittest discover -s tests -v
python3 bin/agentic-agile --help
```

These rules keep the project consistent:

- **Events are the source of truth.** Each state change is an event, and
  `Store._apply` applies it. Keep all changes to SQLite inside `_apply`, and
  keep every event file exactly as it was written. `agentic-agile rebuild`
  then always produces the same index.
- **Existing event histories keep loading.** For a new event shape, increase
  `SCHEMA_VERSION` and keep the old shape readable.
- **Use the standard library.** The CLI runs from a plain checkout with the
  system Python, so every user can run it immediately.
- **Update `docs/protocol.md`** when you change which tasks are ready, how a
  task is completed, or how data is stored. Add a test that fixes the new
  behaviour.
- **State what the tool does and why.** All text in this repository uses
  positive statements and the words goal, plan, task, and finding.

A useful bug report includes the output of `agentic-agile status --json` and
the event files involved (`.agentic-agile/events/`), with private data
removed.

// Agentic Agile for OpenCode.
//
// This plugin gives OpenCode a goal and a way to continue toward it:
// it injects the root-to-node goal path into every model call, and when a
// session goes idle with a node still being pursued it prompts the session to
// continue. `agentic-agile goal pause` releases it.
//
// Load it by path from opencode.json ("plugin": ["/path/to/agentic-agile/opencode/agentic-agile.js"])
// or run `agentic-agile install opencode`.
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fs.realpathSync(fileURLToPath(import.meta.url)));
const BUNDLED = path.join(HERE, "..", "bin", "agentic-agile");
const CLI = process.env.AGENTIC_AGILE_BIN || (fs.existsSync(BUNDLED) ? BUNDLED : "agentic-agile");
// The number of automatic continuations in a row on one task. A message from
// the person starts the count again, so the person always has control.
const MAX_CONTINUES = Number(process.env.AGENTIC_AGILE_MAX_CONTINUES || 8);

function agile(cwd, ...args) {
  const run = spawnSync(CLI, ["--cwd", cwd, ...args], {
    encoding: "utf8",
    input: "",
    timeout: 10_000,
  });
  return run.status === 0 ? run.stdout : "";
}

function json(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export const AgenticAgilePlugin = async ({ client, directory }, options = {}) => {
  const ui = options.ui !== false; // lite installs pass { ui: false }
  const cwd = directory || process.cwd();
  const continues = new Map(); // sessionID -> { node, count }
  let lastLine = "";

  const toast = async (variant = "info") => {
    if (!ui) return;
    const line = agile(cwd, "statusline", "--color", "never").trim();
    if (!line || line === lastLine) return;
    lastLine = line;
    try {
      await client.tui.showToast({ body: { title: "Agentic Agile", message: line, variant } });
    } catch {
      // Headless run: toasts go to the TUI when one is attached.
    }
  };

  const isSubagent = async (sessionID) => {
    try {
      const session = await client.session.get({ path: { id: sessionID } });
      return Boolean((session.data ?? session)?.parentID);
    } catch {
      return false;
    }
  };

  // The goal hook: keep the session on its active node.
  const pursue = async (sessionID) => {
    const decision = json(agile(cwd, "hook-stop", "--host", "opencode"));
    if (!decision || decision.decision !== "block") {
      continues.delete(sessionID);
      await toast();
      return;
    }
    if (await isSubagent(sessionID)) return;
    const node = json(agile(cwd, "view"))?.node?.id ?? "";
    const seen = continues.get(sessionID);
    const count = seen && seen.node === node ? seen.count + 1 : 1;
    continues.set(sessionID, { node, count });
    if (count > MAX_CONTINUES) {
      try {
        await client.tui.showToast({
          body: {
            title: "Agentic Agile",
            message: `Stopped after ${MAX_CONTINUES} tries on '${node}'. Send a message to continue.`,
            variant: "warning",
          },
        });
      } catch {}
      return;
    }
    await toast();
    const request = {
      path: { id: sessionID },
      body: { parts: [{ type: "text", text: decision.reason }] },
    };
    await (client.session.promptAsync ?? client.session.prompt).call(client.session, request);
  };

  const idle = new Set(); // sessions whose idle transition is being handled

  return {
    event: async ({ event }) => {
      const props = event?.properties ?? {};
      const isIdle =
        event?.type === "session.idle" ||
        (event?.type === "session.status" && props.status?.type === "idle");
      const sessionID = props.sessionID;
      if (!isIdle || !sessionID || idle.has(sessionID)) return;
      idle.add(sessionID); // both idle events fire for one transition
      try {
        await pursue(sessionID);
      } finally {
        setTimeout(() => idle.delete(sessionID), 1000);
      }
    },

    // A message from the person resets the continuation budget.
    "chat.message": async ({ sessionID }) => {
      if (sessionID) continues.delete(sessionID);
    },

    // Every model call carries the operative goal, including after compaction.
    "experimental.chat.system.transform": async (_input, output) => {
      const capsule = agile(cwd, "hook-context", "--host", "opencode");
      if (capsule.trim()) output.system.push(capsule);
    },

    "experimental.session.compacting": async (_input, output) => {
      const capsule = agile(cwd, "hook-context", "--host", "opencode");
      if (capsule.trim()) {
        output.context.push(
          "Preserve this Agentic Agile operative goal verbatim in the summary:\n\n" + capsule,
        );
      }
    },

    // Put `agentic-agile` on the PATH of the agent's shell.
    "shell.env": async (_input, output) => {
      if (CLI === BUNDLED) {
        const current = output.env.PATH ?? process.env.PATH ?? "";
        output.env.PATH = path.dirname(BUNDLED) + path.delimiter + current;
      }
    },

    "tool.execute.after": async (input) => {
      const command = input?.args?.command;
      if (typeof command === "string" && command.includes("agentic-agile")) await toast();
    },
  };
};

#!/usr/bin/env node
import { parseArgs } from "node:util";
import { Agent, Cursor, type RunResult } from "@cursor/sdk";
import { buildAgentOptions, resolveCliEnv, type RuntimeMode } from "./config.js";
import { sdkMessageToJsonRecord } from "./jsonl.js";

function printHelp(): void {
  console.log(`latheos-cursor-agent — LatheOS bridge to @cursor/sdk

Usage:
  latheos-cursor-agent models [options]
  latheos-cursor-agent once <prompt> [options]
  latheos-cursor-agent run <prompt> [options]

Environment:
  CURSOR_API_KEY        Required. From Cursor Dashboard → Integrations.
  CURSOR_RUNTIME        "local" (default) or "cloud"
  CURSOR_MODEL_ID       Model id (default: composer-2)
  CURSOR_LOCAL_CWD      Local agent workspace (fallback: LATHEOS_PROJECT_ROOT, then cwd)
  CURSOR_CLOUD_CONFIG   Path to JSON cloud options (repos, autoCreatePR, etc.)
  LATHEOS_PROJECT_ROOT  Used when CURSOR_LOCAL_CWD is unset

Options:
  --jsonl               Print stream events / models as one JSON object per line
  --runtime local|cloud Override CURSOR_RUNTIME
  --cwd DIR             Override CURSOR_LOCAL_CWD (local only)
  --model ID            Override CURSOR_MODEL_ID
  --cloud-config PATH   Override CURSOR_CLOUD_CONFIG

Prompt:
  Use "-" as the prompt to read from stdin (once | run only).
`);
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8").trimEnd();
}

async function resolvePrompt(text: string | undefined): Promise<string> {
  if (text === "-") {
    return readStdin();
  }
  if (text != null && text.length > 0) {
    return text;
  }
  throw new Error("Missing prompt. Pass a string argument or use '-' for stdin.");
}

function parseRuntime(s: string | undefined): RuntimeMode | undefined {
  if (s === "local" || s === "cloud") {
    return s;
  }
  if (s === undefined) {
    return undefined;
  }
  throw new Error(`Invalid --runtime: ${s} (use local or cloud)`);
}

async function cmdModels(
  apiKey: string,
  jsonl: boolean,
): Promise<void> {
  const models = await Cursor.models.list({ apiKey });
  if (jsonl) {
    for (const m of models) {
      console.log(JSON.stringify(m));
    }
    return;
  }
  for (const m of models) {
    console.log(`${m.id}\t${m.displayName}`);
  }
}

function printRunResult(r: RunResult, jsonl: boolean): void {
  if (jsonl) {
    console.log(
      JSON.stringify({
        kind: "result",
        id: r.id,
        status: r.status,
        result: r.result,
        model: r.model,
        durationMs: r.durationMs,
        git: r.git,
      }),
    );
    return;
  }
  if (r.result) {
    console.log(r.result);
  } else {
    console.log(JSON.stringify(r, null, 2));
  }
}

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  if (argv.length === 0 || argv[0] === "-h" || argv[0] === "--help") {
    printHelp();
    process.exit(0);
  }

  const sub = argv[0];
  const rest = argv.slice(1);

  const {
    values: { jsonl, runtime, cwd, model, "cloud-config": cloudConfig },
    positionals,
  } = parseArgs({
    args: rest,
    options: {
      jsonl: { type: "boolean", default: false },
      runtime: { type: "string" },
      cwd: { type: "string" },
      model: { type: "string" },
      "cloud-config": { type: "string" },
    },
    allowPositionals: true,
    strict: true,
  });

  const runtimeMode = parseRuntime(runtime);
  const env = await resolveCliEnv({
    runtimeOverride: runtimeMode,
    cwdOverride: cwd,
    modelOverride: model,
    cloudConfigPath: cloudConfig,
  });

  if (sub === "models") {
    await cmdModels(env.apiKey, jsonl ?? false);
    return;
  }

  if (sub === "once") {
    const prompt = await resolvePrompt(positionals[0]);
    const opts = buildAgentOptions(env);
    const result = await Agent.prompt(prompt, opts);
    printRunResult(result, jsonl ?? false);
    return;
  }

  if (sub === "run") {
    const prompt = await resolvePrompt(positionals[0]);
    const opts = buildAgentOptions(env);
    const agent = await Agent.create(opts);
    try {
      const run = await agent.send(prompt);
      if (jsonl) {
        for await (const event of run.stream()) {
          console.log(JSON.stringify(sdkMessageToJsonRecord(event)));
        }
        const final = await run.wait();
        printRunResult(final, true);
      } else {
        for await (const event of run.stream()) {
          if (event.type === "assistant") {
            for (const block of event.message.content) {
              if (block.type === "text") {
                process.stdout.write(block.text);
              }
            }
          } else if (event.type === "thinking") {
            process.stderr.write(`[thinking] ${event.text}\n`);
          } else if (event.type === "tool_call") {
            process.stderr.write(`[tool] ${event.name} (${event.status})\n`);
          } else if (event.type === "status") {
            process.stderr.write(`[status] ${event.status}\n`);
          }
        }
        await run.wait();
        process.stdout.write("\n");
      }
    } finally {
      await agent[Symbol.asyncDispose]();
    }
    return;
  }

  console.error(`Unknown command: ${sub}`);
  printHelp();
  process.exit(1);
}

main().catch((err: unknown) => {
  const msg = err instanceof Error ? err.message : String(err);
  console.error("latheos-cursor-agent:", msg);
  process.exit(1);
});

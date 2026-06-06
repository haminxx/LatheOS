import { readFile } from "node:fs/promises";
import type { AgentOptions, CloudAgentOptions } from "@cursor/sdk";

export type RuntimeMode = "local" | "cloud";

export interface CliEnv {
  apiKey: string;
  runtime: RuntimeMode;
  modelId: string;
  localCwd: string;
  cloud: CloudAgentOptions | undefined;
}

function getenv(key: string): string | undefined {
  const v = process.env[key];
  return v && v.length > 0 ? v : undefined;
}

export async function loadCloudOptionsFromFile(
  path: string,
): Promise<CloudAgentOptions> {
  const raw = await readFile(path, "utf8");
  const data = JSON.parse(raw) as unknown;
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    throw new Error(`Cloud config must be a JSON object: ${path}`);
  }
  return data as CloudAgentOptions;
}

export async function resolveCliEnv(options: {
  runtimeOverride?: RuntimeMode;
  cwdOverride?: string;
  modelOverride?: string;
  cloudConfigPath?: string;
}): Promise<CliEnv> {
  const apiKey = getenv("CURSOR_API_KEY");
  if (!apiKey) {
    throw new Error(
      "CURSOR_API_KEY is not set. Create a key under Cursor Dashboard → Integrations.",
    );
  }

  const runtime =
    options.runtimeOverride ??
    (getenv("CURSOR_RUNTIME") === "cloud" ? "cloud" : "local");

  const modelId =
    options.modelOverride ??
    getenv("CURSOR_MODEL_ID") ??
    "composer-2";

  const localCwd =
    options.cwdOverride ??
    getenv("CURSOR_LOCAL_CWD") ??
    getenv("LATHEOS_PROJECT_ROOT") ??
    process.cwd();

  const cloudPath =
    options.cloudConfigPath ?? getenv("CURSOR_CLOUD_CONFIG");
  let cloud: CloudAgentOptions | undefined;
  if (cloudPath) {
    cloud = await loadCloudOptionsFromFile(cloudPath);
  }

  return { apiKey, runtime, modelId, localCwd, cloud };
}

export function buildAgentOptions(env: CliEnv): AgentOptions {
  const base: AgentOptions = {
    apiKey: env.apiKey,
    model: { id: env.modelId },
  };

  if (env.runtime === "cloud") {
    if (!env.cloud || !env.cloud.repos?.length) {
      throw new Error(
        "Cloud runtime requires repos. Set CURSOR_CLOUD_CONFIG to a JSON file with a \"repos\" array (see LatheOS docs), or pass --cloud-config.",
      );
    }
    return { ...base, cloud: env.cloud };
  }

  return {
    ...base,
    local: { cwd: env.localCwd },
  };
}

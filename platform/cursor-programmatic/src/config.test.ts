import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  buildAgentOptions,
  loadCloudOptionsFromFile,
  resolveCliEnv,
} from "./config.js";

const savedEnv = { ...process.env };

beforeEach(() => {
  process.env = { ...savedEnv };
  delete process.env.CURSOR_API_KEY;
  delete process.env.CURSOR_RUNTIME;
  delete process.env.CURSOR_MODEL_ID;
  delete process.env.CURSOR_LOCAL_CWD;
  delete process.env.CURSOR_CLOUD_CONFIG;
  delete process.env.LATHEOS_PROJECT_ROOT;
});

afterEach(() => {
  process.env = { ...savedEnv };
});

describe("resolveCliEnv", () => {
  it("throws when CURSOR_API_KEY is missing", async () => {
    await expect(resolveCliEnv({})).rejects.toThrow(/CURSOR_API_KEY/);
  });

  it("resolves local defaults", async () => {
    process.env.CURSOR_API_KEY = "test-key";
    process.env.LATHEOS_PROJECT_ROOT = "/assets/projects";
    const e = await resolveCliEnv({});
    expect(e.apiKey).toBe("test-key");
    expect(e.runtime).toBe("local");
    expect(e.modelId).toBe("composer-2");
    expect(e.localCwd).toBe("/assets/projects");
    expect(e.cloud).toBeUndefined();
  });

  it("uses CURSOR_CLOUD_CONFIG when set", async () => {
    process.env.CURSOR_API_KEY = "k";
    const dir = await mkdtemp(join(tmpdir(), "latheos-cursor-"));
    try {
      const cfgPath = join(dir, "cloud.json");
      await writeFile(
        cfgPath,
        JSON.stringify({
          repos: [{ url: "https://github.com/foo/bar", startingRef: "main" }],
        }),
        "utf8",
      );
      process.env.CURSOR_CLOUD_CONFIG = cfgPath;
      const e = await resolveCliEnv({});
      expect(e.cloud?.repos?.[0]?.url).toBe("https://github.com/foo/bar");
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});

describe("loadCloudOptionsFromFile", () => {
  it("rejects non-object root", async () => {
    const dir = await mkdtemp(join(tmpdir(), "latheos-cursor-"));
    try {
      const cfgPath = join(dir, "bad.json");
      await writeFile(cfgPath, "[]", "utf8");
      await expect(loadCloudOptionsFromFile(cfgPath)).rejects.toThrow(
        /must be a JSON object/,
      );
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});

describe("buildAgentOptions", () => {
  it("builds local options with cwd", () => {
    const o = buildAgentOptions({
      apiKey: "k",
      runtime: "local",
      modelId: "composer-2",
      localCwd: "/repo",
      cloud: undefined,
    });
    expect(o.local?.cwd).toBe("/repo");
    expect(o.cloud).toBeUndefined();
    expect(o.model?.id).toBe("composer-2");
  });

  it("throws cloud without repos", () => {
    expect(() =>
      buildAgentOptions({
        apiKey: "k",
        runtime: "cloud",
        modelId: "composer-2",
        localCwd: "/x",
        cloud: undefined,
      }),
    ).toThrow(/Cloud runtime requires repos/);
  });

  it("builds cloud options", () => {
    const o = buildAgentOptions({
      apiKey: "k",
      runtime: "cloud",
      modelId: "composer-2",
      localCwd: "/x",
      cloud: {
        repos: [{ url: "https://github.com/a/b", startingRef: "main" }],
        autoCreatePR: false,
      },
    });
    expect(o.cloud?.repos?.length).toBe(1);
    expect(o.local).toBeUndefined();
  });
});

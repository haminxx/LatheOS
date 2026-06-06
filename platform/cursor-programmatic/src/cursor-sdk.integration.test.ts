import { describe, expect, it } from "vitest";
import { Agent, Cursor } from "@cursor/sdk";

const hasKey = Boolean(process.env.CURSOR_API_KEY);

describe.skipIf(!hasKey)("Cursor SDK integration (CURSOR_API_KEY)", () => {
  it("lists models", async () => {
    const models = await Cursor.models.list({
      apiKey: process.env.CURSOR_API_KEY!,
    });
    expect(Array.isArray(models)).toBe(true);
    expect(models.length).toBeGreaterThan(0);
  }, 60_000);

  it("Agent.prompt local run completes", async () => {
    const result = await Agent.prompt("Reply with exactly: OK", {
      apiKey: process.env.CURSOR_API_KEY!,
      model: { id: process.env.CURSOR_MODEL_ID ?? "composer-2" },
      local: { cwd: process.cwd() },
    });
    expect(result.status).toBe("finished");
    expect(result.result?.toLowerCase()).toContain("ok");
  }, 120_000);
});

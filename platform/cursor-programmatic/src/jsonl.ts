import type { SDKMessage } from "@cursor/sdk";

/** Compact JSON-serialisable view of an SDK stream message for tooling. */
export function sdkMessageToJsonRecord(msg: SDKMessage): Record<string, unknown> {
  switch (msg.type) {
    case "system":
      return {
        type: msg.type,
        agent_id: msg.agent_id,
        run_id: msg.run_id,
        model: msg.model,
      };
    case "assistant":
      return {
        type: msg.type,
        agent_id: msg.agent_id,
        run_id: msg.run_id,
        content: msg.message.content,
      };
    case "user":
      return {
        type: msg.type,
        agent_id: msg.agent_id,
        run_id: msg.run_id,
        content: msg.message.content,
      };
    case "tool_call":
      return {
        type: msg.type,
        agent_id: msg.agent_id,
        run_id: msg.run_id,
        call_id: msg.call_id,
        name: msg.name,
        status: msg.status,
      };
    case "thinking":
      return {
        type: msg.type,
        agent_id: msg.agent_id,
        run_id: msg.run_id,
        text: msg.text,
      };
    case "status":
      return {
        type: msg.type,
        agent_id: msg.agent_id,
        run_id: msg.run_id,
        status: msg.status,
        message: msg.message,
      };
    case "request":
      return {
        type: msg.type,
        agent_id: msg.agent_id,
        run_id: msg.run_id,
        request_id: msg.request_id,
      };
    case "task":
      return {
        type: msg.type,
        agent_id: msg.agent_id,
        run_id: msg.run_id,
        status: msg.status,
        text: msg.text,
      };
    default:
      return { type: "unknown", message: msg };
  }
}

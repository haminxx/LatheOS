"""`lathe models` — list and switch the active local Ollama models.

LatheOS is BYO-model: pull anything you like with `ollama pull <name>` and
point the assistant at it here. There are three roles:

  * voice  -> LATHEOS_VOICE_MODEL   (fast conversational model, ~3B)
  * heavy  -> LATHEOS_HEAVY_MODEL   (coder/planner, 8B-22B)
  * vision -> LATHEOS_VLM_MODEL     (scene description for the camera)

Switching persists to /persist/secrets/llm.env (loaded after the baked
defaults by the daemon, greeter and shell), so it survives reboots and needs
NO rebuild. Restart cam-daemon (or re-login) to pick up a change in the voice
loop; the shell picks it up next launch.

This is a plain CLI (no Textual) so it works over SSH / a TTY and stays
crash-proof when Ollama is down.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

import httpx

ROLE_ENV = {
    "voice": "LATHEOS_VOICE_MODEL",
    "heavy": "LATHEOS_HEAVY_MODEL",
    "vision": "LATHEOS_VLM_MODEL",
}

PERSIST_PATH = os.environ.get("LATHEOS_PERSIST_LLM_ENV", "/persist/secrets/llm.env")

# Env files the daemon/greeter/shell load, in increasing precedence. We read
# them here so `lathe models` shows the real active model even from a bare TTY
# that didn't inherit the systemd EnvironmentFile vars.
_ENV_FILES = [
    "/etc/latheos/llm.env",
    "/etc/latheos/camera.env",
    PERSIST_PATH,
]


def _parse_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip()
    except OSError:
        pass
    return out


def _resolve(env_key: str) -> str:
    if os.environ.get(env_key):
        return os.environ[env_key]
    value = ""
    for path in _ENV_FILES:
        parsed = _parse_env_file(path)
        if env_key in parsed:
            value = parsed[env_key]
    return value


def _llm_url() -> str:
    return os.environ.get("LATHEOS_LLM_URL", "http://127.0.0.1:11434").rstrip("/")


def list_pulled() -> list[str]:
    try:
        r = httpx.get(f"{_llm_url()}/api/tags", timeout=3.0)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))
    except (httpx.HTTPError, KeyError, ValueError):
        return []


def current() -> dict[str, str]:
    return {role: _resolve(env) for role, env in ROLE_ENV.items()}


def _set_in_lines(lines: list[str], key: str, value: str) -> list[str]:
    out: list[str] = []
    found = False
    for ln in lines:
        if ln.lstrip().startswith(f"{key}="):
            out.append(f"{key}={value}\n")
            found = True
        else:
            out.append(ln)
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{key}={value}\n")
    return out


def _persist(key: str, value: str) -> bool:
    """Write KEY=value into the persist env file. Tries sudo -n if needed."""
    try:
        with open(PERSIST_PATH, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        lines = []
    content = "".join(_set_in_lines(lines, key, value))

    try:
        os.makedirs(os.path.dirname(PERSIST_PATH), exist_ok=True)
        with open(PERSIST_PATH, "w", encoding="utf-8") as fh:
            fh.write(content)
        return True
    except PermissionError:
        # /persist/secrets is root-owned (0700); fall back to a non-interactive
        # sudo write so a normal `lathe models set ...` still works on the dev box.
        try:
            proc = subprocess.run(
                ["sudo", "-n", "tee", PERSIST_PATH],
                input=content.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                check=False,
            )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    except OSError:
        return False


def _cmd_list() -> int:
    pulled = list_pulled()
    cur = current()
    print("Active models:")
    for role, env in ROLE_ENV.items():
        print(f"  {role:<7} {cur[role] or '(unset)':<28} (${env})")
    print()
    if pulled:
        print("Pulled models (ollama):")
        for name in pulled:
            print(f"  {name}")
    else:
        print("No pulled models found (is Ollama running?). Pull one with:")
        print("  ollama pull llama3.2:3b")
    return 0


def _cmd_get(role: str) -> int:
    print(current().get(role, ""))
    return 0


def _cmd_set(role: str, model: str) -> int:
    env_key = ROLE_ENV[role]
    pulled = list_pulled()
    if pulled and model not in pulled:
        print(f"warning: '{model}' is not pulled yet. Pull it with: ollama pull {model}",
              file=sys.stderr)
    if _persist(env_key, model):
        print(f"set {role} -> {model}  (persisted {env_key} to {PERSIST_PATH})")
        print("Restart the voice loop to apply:  systemctl restart cam-daemon")
        return 0
    print(
        f"could not write {PERSIST_PATH} (permission denied).\n"
        f"Add this line yourself:\n  {env_key}={model}\n"
        f"e.g.:  echo '{env_key}={model}' | sudo tee -a {PERSIST_PATH}",
        file=sys.stderr,
    )
    return 1


def _cmd_pull(model: str) -> int:
    try:
        return subprocess.run(["ollama", "pull", model], check=False).returncode
    except FileNotFoundError:
        print("ollama not found on PATH.", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="lathe models",
        description="List and switch the active local Ollama models (voice/heavy/vision).",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="Show active + pulled models (default).")

    p_get = sub.add_parser("get", help="Print the active model for a role.")
    p_get.add_argument("role", choices=list(ROLE_ENV))

    p_set = sub.add_parser("set", help="Set the active model for a role (persists).")
    p_set.add_argument("role", choices=list(ROLE_ENV))
    p_set.add_argument("model", help="Ollama tag, e.g. llama3.1:8b")

    p_pull = sub.add_parser("pull", help="Pull a model with `ollama pull`.")
    p_pull.add_argument("model")

    args = parser.parse_args(argv)

    if args.cmd in (None, "list"):
        return _cmd_list()
    if args.cmd == "get":
        return _cmd_get(args.role)
    if args.cmd == "set":
        return _cmd_set(args.role, args.model)
    if args.cmd == "pull":
        return _cmd_pull(args.model)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

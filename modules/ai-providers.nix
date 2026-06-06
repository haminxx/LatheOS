################################################################################
# LatheOS AI provider router — `lathe-ai`.
#
# Local-first is the default everywhere in LatheOS. This module adds the OPT-IN
# "heavy coding" escape hatch the user picked: one command, `lathe-ai`, that
# routes a coding prompt to a chosen cloud agent provider:
#
#   * cursor   (default) -> latheos-cursor-agent IF installed on PATH (not baked
#                           into the image; install the @cursor/sdk CLI to use it)
#   * claude             -> the Anthropic `claude` CLI, if installed on PATH
#   * opencode           -> the `opencode` CLI, if installed on PATH
#   * <custom>           -> any command named in LATHEOS_AI_PROVIDER_CMD
#
# Provider + keys are resolved from:
#   /etc/latheos/ai-provider.env         (defaults written here)
#   /persist/secrets/ai-provider.env     (optional per-drive override, encrypted
#                                          at rest because /persist is on LUKS)
#   the vault (modules/vault.nix)         (API keys, never written to disk)
#
# Privacy: nothing here runs unless the user explicitly invokes `lathe-ai`. The
# local voice/agent loop never calls out. We do NOT add claude/opencode to the
# system closure (they may be unpackaged); we route to them only if present and
# otherwise print a friendly hint. This keeps the build hermetic.
################################################################################

{ config, pkgs, lib, ... }:

let
  # We deliberately do NOT build/bundle the @cursor/sdk npm agent into the image
  # (it's the optional cloud booster, not local-first, and pins a fragile npm
  # dependency). lathe-ai routes to `latheos-cursor-agent` from PATH *if the user
  # installs it later* — exactly like it already does for claude/opencode.
  latheAi = pkgs.writeShellApplication {
    name = "lathe-ai";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      set -euo pipefail

      PROVIDER_ENV_SYS=/etc/latheos/ai-provider.env
      PROVIDER_ENV_USR=/persist/secrets/ai-provider.env

      # Load defaults then the optional per-drive override (if-form so a missing
      # file does not trip errexit).
      if [ -r "$PROVIDER_ENV_SYS" ]; then
        # shellcheck disable=SC1090
        . "$PROVIDER_ENV_SYS"
      fi
      if [ -r "$PROVIDER_ENV_USR" ]; then
        # shellcheck disable=SC1090
        . "$PROVIDER_ENV_USR"
      fi

      PROVIDER="''${LATHEOS_AI_PROVIDER:-cursor}"

      # Allow `lathe-ai --provider claude "prompt"`.
      if [ "''${1:-}" = "--provider" ]; then
        PROVIDER="''${2:?--provider needs a value}"
        shift 2
      fi

      if [ "$#" -eq 0 ]; then
        {
          printf 'lathe-ai — route a coding prompt to a cloud agent (opt-in).\n\n'
          printf 'Usage:\n'
          printf '  lathe-ai "your prompt"\n'
          printf '  lathe-ai --provider claude "your prompt"\n\n'
          printf 'Providers: cursor (default), claude, opencode, or custom\n'
          printf '(set LATHEOS_AI_PROVIDER_CMD). Default lives in\n'
          printf '/persist/secrets/ai-provider.env (LATHEOS_AI_PROVIDER=...).\n'
          printf 'API keys come from the vault, e.g.: vault set CURSOR_API_KEY\n'
        } >&2
        exit 2
      fi

      # Pull any auto-tagged secrets (API keys) from the vault into the env.
      if command -v vault >/dev/null 2>&1 && [ -r /persist/secrets/vault.key ]; then
        eval "$(vault unlock-env 2>/dev/null || true)"
      fi

      hint() { printf 'lathe-ai: %s\n' "$*" >&2; }

      case "$PROVIDER" in
        cursor)
          if ! command -v latheos-cursor-agent >/dev/null 2>&1; then
            hint "the Cursor agent isn't installed on this drive yet. Install it later, or use --provider claude/opencode."
            exit 1
          fi
          if [ -z "''${CURSOR_API_KEY:-}" ]; then
            hint "CURSOR_API_KEY not set. Run: vault set CURSOR_API_KEY (then: vault mark-auto CURSOR_API_KEY)"
            exit 1
          fi
          exec latheos-cursor-agent run "$*"
          ;;
        claude)
          if ! command -v claude >/dev/null 2>&1; then
            hint "the 'claude' CLI is not installed on this drive. Install it, then retry."
            exit 1
          fi
          exec claude "$*"
          ;;
        opencode)
          if ! command -v opencode >/dev/null 2>&1; then
            hint "the 'opencode' CLI is not installed on this drive. Install it, then retry."
            exit 1
          fi
          exec opencode run "$*"
          ;;
        custom)
          if [ -z "''${LATHEOS_AI_PROVIDER_CMD:-}" ]; then
            hint "provider 'custom' needs LATHEOS_AI_PROVIDER_CMD set to a command."
            exit 1
          fi
          # shellcheck disable=SC2086
          exec $LATHEOS_AI_PROVIDER_CMD "$*"
          ;;
        *)
          hint "unknown provider '$PROVIDER' (use: cursor | claude | opencode | custom)"
          exit 1
          ;;
      esac
    '';
  };
in
{
  environment.systemPackages = [ latheAi ];

  # Default provider config. Override per-drive (without a rebuild) by writing
  # /persist/secrets/ai-provider.env — which is encrypted at rest on LUKS.
  environment.etc."latheos/ai-provider.env".text = ''
    # LatheOS cloud AI provider (opt-in heavy coding). Local AI is the default
    # and needs none of this. Pick: cursor | claude | opencode | custom.
    LATHEOS_AI_PROVIDER=cursor
    # For PROVIDER=custom, the command to exec with the prompt appended:
    # LATHEOS_AI_PROVIDER_CMD=
  '';
}

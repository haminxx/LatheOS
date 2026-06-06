################################################################################
# LatheOS first-run wizard — `lathe-setup`.
#
# A calm, beginner-friendly guided setup the user runs once on first boot. It
# does the few things that genuinely need a human and can't be baked into the
# image safely:
#
#   1. Change the DISK passphrase away from the public factory one ("latheos").
#   2. Set the account (login/sudo) password.
#   3. Pick the voice/UI language.
#   4. Connect to Wi-Fi (hands off to NetworkManager's nmtui).
#   5. (optional) Store a cloud API key in the encrypted vault for `lathe-ai`.
#
# It is intentionally NOT a systemd service: passphrase/password changes need a
# real terminal. Instead it is a command on PATH, and a gentle one-line nudge
# is printed on interactive login until the user has run it. Re-runnable any
# time; safe to skip steps with Enter.
################################################################################

{ config, pkgs, lib, ... }:

let
  cryptPart = "/dev/disk/by-partlabel/cryptroot";

  latheSetup = pkgs.writeShellApplication {
    name = "lathe-setup";
    runtimeInputs = with pkgs; [
      coreutils cryptsetup mkpasswd networkmanager
    ];
    # A wizard must survive a skipped/failed step (passphrase change, EOF on a
    # read, no Wi-Fi) WITHOUT aborting, so drop errexit/nounset that
    # writeShellApplication adds by default; keep pipefail.
    bashOptions = [ "pipefail" ];
    text = ''
      set -o pipefail

      MARKER=/persist/state/.wizard.done
      USR_ENV=/persist/secrets/llm.env

      say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
      ask()  { printf '%s ' "$*"; }

      say "Welcome to LatheOS setup."
      printf 'This takes about a minute. Press Enter to skip any step.\n'

      # --- 1. Disk passphrase ------------------------------------------------
      say "1) Disk password"
      printf 'Your drive ships with a public factory password. Change it now so a\n'
      printf 'lost stick stays private. You will type the OLD one first (latheos),\n'
      printf 'then your NEW one twice.\n'
      ask "Change the disk password now? [Y/n]"
      read -r ans
      if [ "''${ans:-y}" != "n" ] && [ "''${ans:-y}" != "N" ]; then
        if sudo cryptsetup luksChangeKey "${cryptPart}"; then
          printf 'Disk password changed.\n'
        else
          printf 'Skipped or failed — you can re-run lathe-setup later.\n' >&2
        fi
      fi

      # --- 2. Account password ----------------------------------------------
      say "2) Account password (for login + sudo)"
      ask "Set your account password now? [Y/n]"
      read -r ans
      if [ "''${ans:-y}" != "n" ] && [ "''${ans:-y}" != "N" ]; then
        printf 'New account password: '
        read -rs p1; printf '\n'
        printf 'Repeat: '
        read -rs p2; printf '\n'
        if [ -n "$p1" ] && [ "$p1" = "$p2" ]; then
          hash="$(mkpasswd -m sha-512 "$p1")"
          printf '%s\n' "$hash" | sudo tee /persist/secrets/dev.hash >/dev/null
          sudo chmod 0600 /persist/secrets/dev.hash
          printf 'Account password set.\n'
        else
          printf 'Passwords empty or did not match — skipped.\n' >&2
        fi
        unset p1 p2
      fi

      # --- 3. Language -------------------------------------------------------
      say "3) Language"
      ask "Voice/UI language — [e]nglish (default) or [k]orean?"
      read -r lang
      case "''${lang:-e}" in
        k|K|ko|KO)
          sudo mkdir -p /persist/secrets
          {
            printf 'LATHEOS_LANG=ko\n'
            printf 'LATHEOS_PIPER_VOICE=/assets/models/piper/ko_KR-kss-medium.onnx\n'
          } | sudo tee -a "$USR_ENV" >/dev/null
          printf 'Language set to Korean (takes effect next restart).\n'
          ;;
        *)
          printf 'Keeping English.\n'
          ;;
      esac

      # --- 4. Wi-Fi ----------------------------------------------------------
      say "4) Wi-Fi"
      ask "Connect to Wi-Fi now? [y/N]"
      read -r ans
      if [ "''${ans:-n}" = "y" ] || [ "''${ans:-n}" = "Y" ]; then
        nmtui || printf 'Network tool exited — you can connect later from the menu bar.\n' >&2
      fi

      # --- 5. Optional cloud key --------------------------------------------
      say "5) Cloud coding (optional)"
      printf 'LatheOS is local-first. If you want the optional "lathe-ai" cloud\n'
      printf 'boost, you can store an API key now (it goes into the encrypted vault).\n'
      ask "Store a cloud API key now? [y/N]"
      read -r ans
      if { [ "''${ans:-n}" = "y" ] || [ "''${ans:-n}" = "Y" ]; } && command -v vault >/dev/null 2>&1; then
        ask "Which key name? (e.g. CURSOR_API_KEY / ANTHROPIC_API_KEY):"
        read -r keyname
        if [ -n "''${keyname:-}" ]; then
          vault set "$keyname" && vault mark-auto "$keyname" true
          printf 'Stored %s in the vault.\n' "$keyname"
        fi
      fi

      # --- done --------------------------------------------------------------
      sudo mkdir -p /persist/state
      date -u +%FT%TZ | sudo tee "$MARKER" >/dev/null
      say "All set. Welcome aboard."
      printf 'Re-run this anytime with: lathe-setup\n'
    '';
  };
in
{
  environment.systemPackages = [ latheSetup ];

  # Gentle one-line nudge on interactive login until setup has been run once.
  # Never blocks; just points the user at the command.
  environment.interactiveShellInit = ''
    if [ ! -e /persist/state/.wizard.done ] && [ -t 1 ]; then
      printf '\033[1mLatheOS:\033[0m first-time setup not finished — run \033[1mlathe-setup\033[0m to secure your drive.\n'
    fi
  '';
}

################################################################################
# LatheOS Vault — cross-platform-visible, LatheOS-only-decryptable.
#
# Product goal: the USB is a secure portable store for a vibe coder's
#   * API keys (OpenAI, Anthropic, GitHub, Cloudflare, AWS, …)
#   * SSH keys, git identities
#   * small sensitive documents (.env files, license keys)
#
# Storage layout
#   /assets/vault/secrets.age        — AES-encrypted blob, visible on ANY OS.
#   /assets/vault/PUBLIC_KEY.txt     — the age recipient (public key) for this
#                                       drive. Harmless to expose; only used
#                                       to add new secrets.
#   /persist/secrets/vault.key       — the age PRIVATE key. Lives on ext4
#                                       (the Linux-only partition), so when
#                                       the stick is plugged into Windows or
#                                       macOS the private key is INVISIBLE.
#                                       Decryption can only happen from
#                                       inside LatheOS.
#
# This gives the user a "cross-platform visible, LatheOS-private" split:
# they can carry their secrets on a stick through any computer without
# exposing them, and only LatheOS can unseal the vault.
#
# First-boot flow
#   * If /persist/secrets/vault.key is missing, `cam-vault-init` generates
#     one and writes the public key to /assets/vault/PUBLIC_KEY.txt.
#   * An empty encrypted blob is created so `vault get/set` has something
#     to talk to on a fresh stick.
#
# CLI
#   vault set NAME [value]     # prompts if value is omitted
#   vault get NAME             # prints on stdout
#   vault list                 # names only (never values)
#   vault export [PREFIX]      # emits `export NAME="VALUE"` for eval
#   vault unlock-env           # injects entries tagged {auto:true} into a
#                              # transient environment. Used by project
#                              # shells so API keys land in envvars without
#                              # being echoed to disk.
#
# Threat model (honest)
#   Strong against: casual snooping on a host OS with the stick plugged in
#   (Windows/Mac cannot read ext4, so the age private key is unseen).
#   Weak against: root on the host OS if ext4 can be forensically imaged.
#   For that tier, enable LUKS on the ext4 partition as a follow-up.
################################################################################

{ config, pkgs, lib, ... }:

let
  vaultCli = pkgs.writeShellApplication {
    name = "vault";
    runtimeInputs = with pkgs; [ age jq coreutils util-linux ];
    text = ''
      set -euo pipefail

      VAULT_DIR=/assets/vault
      BLOB="$VAULT_DIR/secrets.age"
      PUB="$VAULT_DIR/PUBLIC_KEY.txt"
      KEY=/persist/secrets/vault.key

      die() { printf 'vault: %s\n' "$*" >&2; exit 1; }

      ensure_init() {
        [ -r "$KEY" ]  || die "no private key at $KEY (run: sudo systemctl start cam-vault-init)"
        [ -r "$PUB" ]  || die "no public key at $PUB"
        [ -r "$BLOB" ] || die "no encrypted blob at $BLOB"
      }

      # Load the decrypted JSON doc into stdout.
      load() {
        ensure_init
        age --decrypt -i "$KEY" "$BLOB"
      }

      # Save stdin (a JSON doc) back into $BLOB using the stored public key.
      save() {
        local recipient
        recipient=$(cat "$PUB")
        tmp=$(mktemp)
        age --encrypt -r "$recipient" -o "$tmp"
        install -m 0644 "$tmp" "$BLOB"
        rm -f "$tmp"
      }

      cmd=''${1:-help}
      shift || true

      case "$cmd" in
        set)
          name=''${1:?"vault set NAME [value]"}
          value=''${2:-}
          if [ -z "$value" ]; then
            printf 'value for %s (hidden): ' "$name" >&2
            read -rs value
            printf '\n' >&2
          fi
          current=$(load)
          updated=$(printf '%s' "$current" \
            | jq --arg k "$name" --arg v "$value" \
                 '.[$k] = {value:$v, auto:(.[$k].auto // false), updated:(now|todate)}')
          printf '%s' "$updated" | save
          echo "stored: $name"
          ;;

        get)
          name=''${1:?"vault get NAME"}
          load | jq -r --arg k "$name" '.[$k].value // empty'
          ;;

        list)
          load | jq -r 'to_entries[] | "\(.key)\t\(.value.updated // "?")\t\(.value.auto // false)"'
          ;;

        export)
          prefix=''${1:-}
          load | jq -r --arg p "$prefix" '
            to_entries[]
            | select(.key | startswith($p))
            | "export " + .key + "=" + (.value.value | @sh)
          '
          ;;

        unlock-env)
          load | jq -r '
            to_entries[] | select(.value.auto == true)
            | "export " + .key + "=" + (.value.value | @sh)
          '
          ;;

        mark-auto)
          name=''${1:?"vault mark-auto NAME"}
          on=''${2:-true}
          current=$(load)
          updated=$(printf '%s' "$current" | jq --arg k "$name" --argjson on "$on" '.[$k].auto = $on')
          printf '%s' "$updated" | save
          ;;

        rm|remove)
          name=''${1:?"vault rm NAME"}
          current=$(load)
          updated=$(printf '%s' "$current" | jq --arg k "$name" 'del(.[$k])')
          printf '%s' "$updated" | save
          ;;

        pubkey)
          cat "$PUB"
          ;;

        help|*)
          cat <<EOF
vault — LatheOS cross-platform secret store (age-encrypted on /assets/vault)

Usage:
  vault set NAME [value]        store or update a secret (prompts if omitted)
  vault get NAME                print the secret's value
  vault list                    list names + timestamps (values never echoed)
  vault export [PREFIX]         emit bash 'export' lines for matching names
  vault unlock-env              emit 'export' lines for entries tagged auto
  vault mark-auto NAME [bool]   toggle the auto-inject flag for a secret
  vault rm NAME                 remove a secret
  vault pubkey                  print this drive's age public key

Files:
  /assets/vault/secrets.age     encrypted blob (visible to any OS)
  /assets/vault/PUBLIC_KEY.txt  age recipient (safe to share)
  /persist/secrets/vault.key    age private key (LatheOS-only)
EOF
          ;;
      esac
    '';
  };
in
{
  environment.systemPackages = [ vaultCli pkgs.age ];

  systemd.tmpfiles.rules = [
    "d /assets/vault       0755 dev users - -"
    "d /persist/secrets    0700 root root - -"
  ];

  # Generate the age keypair + empty vault on first boot, idempotently.
  systemd.services.cam-vault-init = {
    description = "LatheOS — initialise the age-encrypted vault on first boot";
    after    = [ "assets.mount" ];
    requires = [ "assets.mount" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [ pkgs.age pkgs.jq pkgs.coreutils ];
    script = ''
      set -eu
      VAULT_DIR=/assets/vault
      BLOB="$VAULT_DIR/secrets.age"
      PUB="$VAULT_DIR/PUBLIC_KEY.txt"
      KEY=/persist/secrets/vault.key

      mkdir -p "$VAULT_DIR"
      chown dev:users "$VAULT_DIR"

      if [ ! -r "$KEY" ]; then
        umask 077
        age-keygen -o "$KEY" 2>/tmp/age.meta
        grep -E '^# public key:' /tmp/age.meta | sed 's/^# public key: //' > "$PUB"
        chown root:root "$KEY"
        chmod 0600 "$KEY"
        chmod 0644 "$PUB"
        rm -f /tmp/age.meta
      fi

      if [ ! -r "$BLOB" ]; then
        RECIP=$(cat "$PUB")
        echo '{}' | age --encrypt -r "$RECIP" -o "$BLOB"
        chmod 0644 "$BLOB"
      fi
    '';
  };

  # When the user opens a project shell, auto-inject their "auto:true"
  # secrets as environment variables — exactly what direnv/dotenv already
  # do for local .env files, but decrypted on-the-fly from the vault.
  environment.shellInit = ''
    if command -v vault >/dev/null 2>&1 && [ -r /persist/secrets/vault.key ]; then
      eval "$(vault unlock-env 2>/dev/null || true)"
    fi
  '';
}

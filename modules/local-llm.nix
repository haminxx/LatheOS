################################################################################
# LatheOS Local AI stack.
#
# All inference runs on the USB. Network is optional — if the device boots up
# with broken Wi-Fi, the agent can still reason about it because the model is
# already on-disk.
#
# Components
#   * Ollama            — model runtime with a simple HTTP API (localhost only).
#   * whisper.cpp       — offline speech-to-text for the voice path.
#   * piper-tts         — offline text-to-speech for the voice reply.
#
# Model layout
#   Model weights are BIG and upgrade often, so they are NOT placed in the
#   Nix store. They live on the exFAT partition (`/assets/models/ollama`) so
#   the user can swap / update them from Windows / macOS without nixos-rebuild.
#
# Two-model policy (answers the "fast talking / heavy thinking" split)
#   LATHEOS_VOICE_MODEL : quick conversational replies for the voice loop.
#   LATHEOS_HEAVY_MODEL : heavier coder / reasoning model dispatched when the
#                         user asks for code, repair patches, or analysis.
#
# Model defaults are Western-origin only (Meta, Mistral, Microsoft, IBM).
# Users can override them via /persist/secrets/llm.env without touching Nix.
################################################################################

{ config, pkgs, lib, ... }:

let
  # Single source of truth for the model defaults. Override in
  # /persist/secrets/llm.env if the user wants a different pairing.
  voiceModel = "llama3.2:3b";          # Meta — tiny, fast, conversational.
                                        # Doubles as the Hermes router's
                                        # classifier (LATHEOS_CLASSIFIER_MODEL).
  heavyModelBig = "gemma3:12b";        # Google — Engine A's headline local
                                        # model (the architecture's "Gemma 12B");
                                        # picked only where there's RAM headroom.
  heavyModelSmall = "llama3.1:8b";     # Meta — fits in ~6-8 GB (16 GB baseline).

  # Embeddings for Hermes "General" memory (vector RAG). Small + CPU-friendly,
  # served by the same Ollama via /api/embed. See daemon/cam_daemon/memory.py.
  embedModel = "nomic-embed-text";

  # Default Piper voice paths — primary (en) + alt (ko). Both live on the
  # exFAT partition so the user can swap them from Windows/macOS/Linux
  # without a nixos-rebuild. The voice file the greeter uses is chosen
  # by LATHEOS_LANG (below).
  piperVoiceEn = "/assets/models/piper/en_US-amy-medium.onnx";
  piperVoiceKo = "/assets/models/piper/ko_KR-kss-medium.onnx";
in
{
  ##############################################################################
  # 1. Ollama — LLM runtime
  ##############################################################################

  services.ollama = {
    enable = true;
    # Listen on loopback only. The embedded shell and cam-daemon both talk to
    # http://127.0.0.1:11434 — nothing else on the network ever sees it.
    host = "127.0.0.1";
    port = 11434;
    # Models live on the exFAT partition so they survive nixos-rebuild and
    # can be managed from a non-LatheOS host when the stick is plugged in
    # somewhere else.
    home = "/assets/models/ollama";
    # Let Ollama pick CPU / CUDA / ROCm per hardware. The USB may move
    # between machines, so we do NOT pin an accelerator here.
    acceleration = false;
  };

  # Ollama writes into /assets/models/ollama; make sure the directory exists
  # even on a fresh stick before Ollama first starts.
  systemd.tmpfiles.rules = [
    "d /assets/models              0755 dev users - -"
    "d /assets/models/ollama       0755 ollama ollama - -"
    "d /assets/models/whisper      0755 dev users - -"
    "d /assets/models/piper        0755 dev users - -"
    "d /assets/models/openwakeword 0755 dev users - -"
    "d /persist/cache/llm          0755 dev users - -"
    "d /persist/state              0755 dev users - -"
  ];

  ##############################################################################
  # 2. Speech I/O — Whisper (STT) + Piper (TTS)
  ##############################################################################

  environment.systemPackages = with pkgs; [
    # Offline speech-to-text. The daemon streams mic PCM into whisper-cpp.
    openai-whisper-cpp

    # Offline text-to-speech. Replaces Cartesia when we are in local-only
    # mode; the daemon writes synthesized WAV frames to the audio sink.
    piper-tts

    # Small HTTP client the daemon / embedded shell use to talk to Ollama.
    jq curl
  ];

  ##############################################################################
  # 3. Runtime configuration (model choice + feature flags)
  ##############################################################################

  # Baked defaults. Overridden per-drive by /persist/secrets/llm.env,
  # which is where users change the model pairing without a rebuild.
  #
  # The `cam-firstrun-apply` service (below) may ALSO overwrite the derived
  # bits of this file at boot, based on the firstrun.json the Windows
  # installer wrote onto /assets. Users never edit this file by hand unless
  # they also disable that service.
  environment.etc."latheos/llm.env".text = ''
    LATHEOS_LLM_URL=http://127.0.0.1:11434
    LATHEOS_VOICE_MODEL=${voiceModel}
    # Heavy model is auto-selected at boot by modules/local-llm.nix's
    # cam-llm-autoselect service based on free RAM. Defaults below are
    # only used if the autoselect step fails.
    LATHEOS_HEAVY_MODEL=${heavyModelSmall}

    # Language: "en" (default) or "ko" today. Extra languages are added by
    # dropping a Piper voice file into /assets/models/piper and pointing
    # LATHEOS_PIPER_VOICE at it.
    LATHEOS_LANG=en
    LATHEOS_LANG_FALLBACK=ko

    # Paths used by the daemon + embedded shell + greeter. All on exFAT
    # so the user can add models from Windows/macOS/Linux without rebuild.
    LATHEOS_WHISPER_MODEL=/assets/models/whisper/ggml-base.en.bin
    LATHEOS_PIPER_VOICE=${piperVoiceEn}
    LATHEOS_PIPER_VOICE_EN=${piperVoiceEn}
    LATHEOS_PIPER_VOICE_KO=${piperVoiceKo}

    # Agent pool: max parallel worker LLM calls from daemon/agents.py.
    # Tune down on low-RAM boxes.
    LATHEOS_MAX_AGENTS=4

    # Where openWakeWord finds its ONNX weights. Populated by
    # scripts/prefetch-models.sh at image-build time.
    LATHEOS_OWW_MODELS_DIR=/assets/models/openwakeword

    ##########################################################################
    # Hermes — hybrid cognitive orchestrator (daemon/cam_daemon/hermes.py)
    ##########################################################################
    # The router decides local (Engine A, above) vs cloud (Engine B, below).
    # It classifies with the small voice model — never the heavy one.
    LATHEOS_ROUTER_ENABLE=1
    LATHEOS_CLASSIFIER_MODEL=${voiceModel}

    # Heavy *offline* reasoning via the legacy 5-role fan-out (agents.py).
    # Off by default; Hermes does a single heavy call instead. Set to 1 to
    # trade latency for more thorough local answers.
    LATHEOS_DEEP_LOCAL=0

    ##########################################################################
    # 3-tier memory (daemon/cam_daemon/memory.py)
    ##########################################################################
    LATHEOS_MEMORY_ENABLE=1
    LATHEOS_EMBED_MODEL=${embedModel}
    LATHEOS_CORE_MEMORY=/persist/state/core.yml
    LATHEOS_GENERAL_DB=/persist/cache/llm/general.db
    LATHEOS_TREND_EVENTS=/run/cam-daemon/events.jsonl
    LATHEOS_SESSION_FILE=/persist/state/session.json

    ##########################################################################
    # Cloud frontier model (Engine B). PRIVACY: OFF until you opt in, and
    # even then nothing is sent without a per-task confirm (LATHEOS_CLOUD_
    # CONFIRM=1). Store the key with `vault set <NAME>` then set ENABLE=1 in
    # /persist/secrets/llm.env (the first-run wizard does this for you).
    # OpenAI-compatible: works with OpenRouter or NVIDIA NIM — set URL/MODEL
    # to your provider's exact values.
    ##########################################################################
    LATHEOS_CLOUD_ENABLE=0
    LATHEOS_CLOUD_CONFIRM=1
    LATHEOS_CLOUD_URL=https://openrouter.ai/api/v1
    LATHEOS_CLOUD_MODEL=nvidia/nemotron-3-ultra
    LATHEOS_CLOUD_API_KEY_NAME=OPENROUTER_API_KEY
  '';

  ##############################################################################
  # 4. Auto-select heavy model based on available RAM
  ##############################################################################
  # Writes a drop-in env file that overrides LATHEOS_HEAVY_MODEL depending on
  # what the host machine can actually run. Runs on every boot so moving the
  # stick from a laptop to a workstation upgrades the model for free.
  systemd.services.cam-llm-autoselect = {
    description = "LatheOS — pick heavy LLM based on host RAM";
    wantedBy = [ "multi-user.target" ];
    before   = [ "ollama.service" "cam-daemon.service" ];
    serviceConfig.Type = "oneshot";
    serviceConfig.RemainAfterExit = true;
    script = ''
      set -eu
      TOTAL_MB=$(${pkgs.gawk}/bin/awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
      # Gemma 12B (q4 ≈8 GB) is Engine A's headline model — we pick it once the
      # host has ≥16 GB total, leaving room for the 3B voice model + the OS.
      # Smaller / low-RAM machines fall back to the 8B model (≈6 GB) so the
      # same USB still boots and reasons on a thin laptop.
      if [ "$TOTAL_MB" -ge 16000 ]; then
        PICK=${heavyModelBig}
      else
        PICK=${heavyModelSmall}
      fi
      mkdir -p /run/latheos
      printf 'LATHEOS_HEAVY_MODEL=%s\n' "$PICK" > /run/latheos/heavy-model.env
      echo "cam-llm-autoselect: RAM=$TOTAL_MB MB -> heavy=$PICK"
    '';
  };

  # Make the auto-selected value visible to every LatheOS service.
  environment.etc."profile.d/latheos-llm.sh".text = ''
    # Load the auto-selected heavy model if present.
    [ -r /run/latheos/heavy-model.env ] && . /run/latheos/heavy-model.env || true
  '';

  ##############################################################################
  # 5. First-boot bootstrap — pull models once, non-fatal on failure.
  ##############################################################################

  systemd.services.latheos-llm-bootstrap = {
    description = "LatheOS — pull default local LLMs on first boot (idempotent)";
    after  = [ "ollama.service" "network-online.target" "cam-llm-autoselect.service" ];
    wants  = [ "ollama.service" "cam-llm-autoselect.service" ];
    wantedBy = [ "multi-user.target" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "ollama";
      SuccessExitStatus = "0 1";
    };

    script = ''
      set -eu
      MARKER=/assets/models/ollama/.latheos-bootstrapped
      PREFETCH_MARKER=/assets/models/.prefetched

      # If the USB was built with `scripts/prefetch-models.sh`, every
      # model is already on /assets/models/ollama — Ollama will find them
      # automatically because its OLLAMA_MODELS env points there. No pull,
      # no network. Mark bootstrap done and bail.
      if [ -f "$PREFETCH_MARKER" ]; then
        echo "LatheOS LLMs pre-baked at build time — skipping pull."
        date -u +%FT%TZ > "$MARKER" || true
        exit 0
      fi

      if [ -f "$MARKER" ]; then
        echo "LatheOS LLMs already bootstrapped — skipping."
        exit 0
      fi

      HEAVY="${heavyModelSmall}"
      [ -r /run/latheos/heavy-model.env ] && . /run/latheos/heavy-model.env && HEAVY="$LATHEOS_HEAVY_MODEL"

      echo "Pulling voice model: ${voiceModel}"
      ${pkgs.ollama}/bin/ollama pull "${voiceModel}" || echo "voice model pull failed (offline?)"

      echo "Pulling heavy model: $HEAVY"
      ${pkgs.ollama}/bin/ollama pull "$HEAVY" || echo "heavy model pull failed (offline?)"

      # Embeddings for Hermes "General" memory (vector RAG). Small (~270 MB);
      # non-fatal if offline — memory just stays empty until it can be pulled.
      echo "Pulling embeddings model: ${embedModel}"
      ${pkgs.ollama}/bin/ollama pull "${embedModel}" || echo "embeddings pull failed (offline?)"

      date -u +%FT%TZ > "$MARKER" || true
    '';
  };

  ##############################################################################
  # 5b. Seed Hermes "Core" memory — immutable identity + OS directives.
  ##############################################################################
  # Core memory is a plain-text file injected verbatim into every Hermes system
  # prompt (local AND cloud). We seed a sensible template once; the user edits
  # it to teach the assistant who they are and the rules it must always follow.
  # It lives on /persist (ext4) so other host OSes can't read it off the stick.
  systemd.services.latheos-core-memory-init = {
    description = "LatheOS — seed Hermes Core memory (core.yml) on first boot";
    wantedBy = [ "multi-user.target" ];
    before   = [ "cam-daemon.service" ];
    serviceConfig.Type = "oneshot";
    serviceConfig.RemainAfterExit = true;
    path = [ pkgs.coreutils ];
    script = ''
      set -eu
      CORE=/persist/state/core.yml
      mkdir -p /persist/state
      if [ -e "$CORE" ]; then
        exit 0
      fi
      cat > "$CORE" <<'YAML'
      # LatheOS Core Memory — Hermes injects this verbatim into every prompt.
      # This is the assistant's most authoritative context. Edit it to taste;
      # keep it short and factual. Lines starting with '#' are comments.

      identity:
        # Who the assistant is talking to. Fill these in.
        user_name: ""
        pronouns: ""
        timezone: ""
        languages: ["en"]

      preferences:
        # How you like answers. The assistant should honor these.
        tone: "calm, concise, no fluff"
        verbosity: "short by default; expand only when asked"
        code_style: "minimal, well-structured, NixOS/Linux-aware"

      directives:
        # Hard rules the assistant must always follow.
        - "Privacy-first: prefer the local engine; only use the cloud when the user confirms."
        - "Never run destructive commands without an explicit 'yes, do it'."
        - "Never fabricate hardware specs or system state."
        - "Keep secrets on-device; never echo API keys or vault contents."
      YAML
      chown dev:users "$CORE" || true
      chmod 0644 "$CORE" || true
      echo "seeded Core memory at $CORE"
    '';
  };

  ##############################################################################
  # 5c. Expose the cloud API key to the daemon (opt-in, confirm-gated upstream).
  ##############################################################################
  # The daemon runs as `dev` and CANNOT read the age private key (root-only),
  # so it can't unseal the vault itself. This root oneshot decrypts ONLY the
  # configured cloud key and drops it in /run (tmpfs, wiped each boot), group
  # `audio` so the daemon can read it. Runs only when cloud is enabled AND the
  # key exists — otherwise nothing is decrypted or written.
  systemd.services.latheos-cloud-key = {
    description = "LatheOS — expose the cloud API key from the vault to the daemon (opt-in)";
    after    = [ "assets.mount" "cam-vault-init.service" ];
    wants    = [ "cam-vault-init.service" ];
    wantedBy = [ "multi-user.target" ];
    before   = [ "cam-daemon.service" ];
    serviceConfig.Type = "oneshot";
    serviceConfig.RemainAfterExit = true;
    path = [ pkgs.age pkgs.jq pkgs.gnugrep pkgs.coreutils ];
    script = ''
      set -eu
      OUT=/run/latheos/cloud.env
      mkdir -p /run/latheos
      rm -f "$OUT"

      # Read effective config from the env-file chain (later files win).
      ENABLE=0
      KEYNAME=OPENROUTER_API_KEY
      for f in /etc/latheos/llm.env /persist/secrets/llm.env; do
        [ -r "$f" ] || continue
        v=$(grep -E '^LATHEOS_CLOUD_ENABLE=' "$f" | tail -1 | cut -d= -f2- || true)
        [ -n "$v" ] && ENABLE="$v"
        v=$(grep -E '^LATHEOS_CLOUD_API_KEY_NAME=' "$f" | tail -1 | cut -d= -f2- || true)
        [ -n "$v" ] && KEYNAME="$v"
      done

      if [ "$ENABLE" != "1" ]; then
        echo "cloud disabled — not exposing any key"; exit 0
      fi

      KEY=/persist/secrets/vault.key
      BLOB=/assets/vault/secrets.age
      if [ ! -r "$KEY" ] || [ ! -r "$BLOB" ]; then
        echo "vault not initialised — skipping"; exit 0
      fi

      VAL=$(age --decrypt -i "$KEY" "$BLOB" 2>/dev/null \
        | jq -r --arg k "$KEYNAME" '.[$k].value // empty' || true)
      if [ -z "$VAL" ]; then
        echo "no '$KEYNAME' in vault — skipping"; exit 0
      fi

      umask 027
      printf 'LATHEOS_CLOUD_API_KEY=%s\n' "$VAL" > "$OUT"
      chgrp audio "$OUT" 2>/dev/null || true
      chmod 0640 "$OUT"
      echo "cloud key '$KEYNAME' exposed to the daemon"
    '';
  };

  ##############################################################################
  # 6. Apply the Windows installer's first-run profile (language, PV key)
  ##############################################################################
  # The Windows PowerShell installer drops /assets/latheos/firstrun.json and,
  # optionally, /assets/latheos/secrets/cam.env. On first boot we move those
  # into /persist (the canonical writable root) and set language preferences.
  systemd.services.cam-firstrun-apply = {
    description = "LatheOS — apply first-run profile written by the Windows installer";
    after    = [ "assets.mount" ];           # requires the exFAT partition
    requires = [ "assets.mount" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig.Type = "oneshot";
    serviceConfig.RemainAfterExit = true;
    path = [ pkgs.jq pkgs.coreutils ];
    script = ''
      set -eu
      PROFILE=/assets/latheos/firstrun.json
      MARKER=/persist/state/.firstrun.applied
      if [ -f "$MARKER" ] || [ ! -r "$PROFILE" ]; then
        exit 0
      fi

      mkdir -p /persist/state /persist/secrets
      LANG_CODE=$(jq -r '.language // "en"' "$PROFILE")
      TZ=$(jq -r '.timezone // empty' "$PROFILE")
      WAKE_BACKEND=$(jq -r '.wake_backend // "oww"' "$PROFILE")

      # Update /etc/latheos/llm.env in place with the chosen language and
      # voice. We touch only the two relevant lines.
      VOICE=${piperVoiceEn}
      [ "$LANG_CODE" = "ko" ] && VOICE=${piperVoiceKo}
      sed -i \
        -e "s|^LATHEOS_LANG=.*|LATHEOS_LANG=$LANG_CODE|" \
        -e "s|^LATHEOS_PIPER_VOICE=.*|LATHEOS_PIPER_VOICE=$VOICE|" \
        /etc/latheos/llm.env || true

      # Propagate wake backend choice to /etc/latheos/cam.env if the user
      # picked something other than the default. The daemon re-reads it on
      # next restart, so this takes effect after the next boot or
      # `systemctl restart cam-daemon`.
      if [ -f /etc/latheos/cam.env ]; then
        sed -i "s|^LATHEOS_WAKE_BACKEND=.*|LATHEOS_WAKE_BACKEND=$WAKE_BACKEND|" \
          /etc/latheos/cam.env || true
      fi

      if [ -n "$TZ" ]; then
        echo "$TZ" > /persist/state/timezone
      fi

      # Move any pre-staged cam.env from exFAT (visible to Windows) into
      # /persist/secrets (ext4, restricted) and delete the staging copy so no
      # other host OS can read it later. This is only used by drives that opt
      # into the Porcupine wake backend (it holds PICOVOICE_ACCESS_KEY); the
      # default openWakeWord backend needs no secrets at all.
      if [ -r /assets/latheos/secrets/cam.env ]; then
        install -m 0600 /assets/latheos/secrets/cam.env /persist/secrets/cam.env
        rm -f /assets/latheos/secrets/cam.env
      fi

      echo "firstrun applied: lang=$LANG_CODE tz=$TZ wake=$WAKE_BACKEND"
      date -u +%FT%TZ > "$MARKER"
    '';
  };
}

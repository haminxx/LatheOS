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
  # Single source of truth for the two-model defaults. Override in
  # /persist/secrets/llm.env if the user wants a different pairing.
  voiceModel = "llama3.2:3b";          # Meta — tiny, fast, conversational.
  heavyModelBig = "codestral:22b";     # Mistral (France) — strong at code;
                                        # needs ~16 GB free RAM.
  heavyModelSmall = "llama3.1:8b";     # Meta — fits in ~6-8 GB.

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

    # Cloud proxy is OPT-IN. Leave empty to stay fully local.
    # Set to e.g. wss://cam.example.com/ws/cam to enable the "bigger brain"
    # upgrade path documented in docs/LATHEOS_VIBE_PLATFORM.md.
    LATHEOS_CLOUD_PROXY_URL=
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
      # Codestral 22B q4 wants ~16 GB; require 20 GB headroom to leave room
      # for the voice model, editor, and host services.
      if [ "$TOTAL_MB" -ge 20000 ]; then
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

      date -u +%FT%TZ > "$MARKER" || true
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

      # Move pre-staged cam.env from exFAT (visible to Windows) into
      # /persist/secrets (ext4, restricted). Delete the staging copy so
      # no other host OS can read a stored Picovoice key later.
      if [ -r /assets/latheos/secrets/cam.env ]; then
        install -m 0600 /assets/latheos/secrets/cam.env /persist/secrets/cam.env
        rm -f /assets/latheos/secrets/cam.env
      fi

      echo "firstrun applied: lang=$LANG_CODE tz=$TZ wake=$WAKE_BACKEND"
      date -u +%FT%TZ > "$MARKER"
    '';
  };
}

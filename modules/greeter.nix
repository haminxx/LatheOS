################################################################################
# CAM Greeter — "Jarvis-style" boot welcome.
#
# When a LatheOS session reaches the graphical target, the greeter runs once
# per login and:
#
#   1. Performs a quick system status check
#        - battery, disk, memory, /assets mount, network, Ollama health.
#   2. Loads the previous session state from /persist/state/session.json
#        - last task, open to-do list, last-seen timestamp.
#   3. Asks the local voice model (via Ollama) to compose ONE short spoken
#      briefing, grounded on the status + last session.
#   4. Speaks it through Piper (offline TTS) and also shows it as a desktop
#      notification so the user can read what CAM just said.
#
# Everything here runs fully offline. The cloud proxy is NOT required.
#
# The heavy LLM (Codestral / Llama 3.1 8B) is deliberately NOT used for the
# greeting; we stay on the small voice model so boot-to-speech stays snappy.
################################################################################

{ config, pkgs, lib, ... }:

let
  sessionStatePath = "/persist/state/session.json";

  # The greeter script. Written as a Nix-provisioned bash file so Nix controls
  # the package closure (jq, curl, piper, libnotify) deterministically.
  greeterScript = pkgs.writeShellApplication {
    name = "cam-greeter";
    runtimeInputs = with pkgs; [
      coreutils util-linux procps
      jq curl
      libnotify              # notify-send for the on-screen card
      piper-tts              # offline text-to-speech
      alsa-utils             # aplay for WAV playback (works with PipeWire-pulse)
      iproute2 iw            # network status
    ];
    text = ''
      set -euo pipefail

      # --- 0. Load environment (model names, endpoints, voice paths) -------
      # shellcheck disable=SC1091
      [ -r /etc/latheos/llm.env ] && . /etc/latheos/llm.env
      [ -r /persist/secrets/llm.env ] && . /persist/secrets/llm.env || true

      LLM_URL="''${LATHEOS_LLM_URL:-http://127.0.0.1:11434}"
      VOICE_MODEL="''${LATHEOS_VOICE_MODEL:-llama3.2:3b}"
      PIPER_VOICE="''${LATHEOS_PIPER_VOICE:-/assets/models/piper/en_US-amy-medium.onnx}"

      STATE_FILE="${sessionStatePath}"
      mkdir -p "$(dirname "$STATE_FILE")"
      if [ ! -f "$STATE_FILE" ]; then
        echo '{"last_task":null,"todos":[],"last_boot":null}' > "$STATE_FILE"
      fi

      # --- 1. System status check -----------------------------------------
      NOW="$(date -Is)"
      DISK_FREE="$(df -h /assets 2>/dev/null | awk 'NR==2 {print $4" free of "$2}' || echo 'unknown')"
      MEM_FREE="$(free -h | awk '/^Mem:/ {print $7" available"}')"
      NET="offline"
      if ip route get 1.1.1.1 >/dev/null 2>&1; then NET="online"; fi

      OLLAMA_OK="no"
      if curl -fsS --max-time 1 "''${LLM_URL}/api/tags" >/dev/null 2>&1; then
        OLLAMA_OK="yes"
      fi

      BATTERY="n/a"
      if [ -r /sys/class/power_supply/BAT0/capacity ]; then
        BATTERY="$(cat /sys/class/power_supply/BAT0/capacity)%"
      fi

      LAST_TASK="$(jq -r '.last_task // "nothing recorded"' "$STATE_FILE")"
      TODO_COUNT="$(jq -r '.todos | length' "$STATE_FILE")"
      TODO_PREVIEW="$(jq -r '.todos[:3] | join("; ")' "$STATE_FILE")"

      STATUS_JSON=$(jq -n \
        --arg now         "$NOW" \
        --arg disk        "$DISK_FREE" \
        --arg mem         "$MEM_FREE" \
        --arg net         "$NET" \
        --arg ollama      "$OLLAMA_OK" \
        --arg battery     "$BATTERY" \
        --arg last_task   "$LAST_TASK" \
        --argjson todos   "$TODO_COUNT" \
        --arg todo_prev   "$TODO_PREVIEW" \
        '{now:$now,disk:$disk,memory:$mem,network:$net,ollama:$ollama,
          battery:$battery,last_task:$last_task,todos:$todos,
          todo_preview:$todo_prev}')

      # --- 2. Compose briefing via local voice model -----------------------
      LANG_CODE="''${LATHEOS_LANG:-en}"
      if [ "$LANG_CODE" = "ko" ]; then
        PROMPT="당신은 LatheOS의 온디바이스 비서 CAM입니다. 한국어로 최대 3문장, \
약 40단어 내외로 간결히 브리핑하세요. 시스템 상태, 마지막 작업, 최상위 1-2개 할 \
일을 언급하세요. 침착하고 사실에 기반해서, 이모지는 쓰지 마세요. 상태 JSON: $STATUS_JSON"
      else
        PROMPT="You are CAM, the on-device assistant for LatheOS. Greet the user \
in one short paragraph (max 3 sentences, about 40 words). Mention: whether \
systems are healthy, what they were last doing, and the top 1-2 open tasks. \
Be calm and concise, no emojis. Status JSON follows: $STATUS_JSON"
      fi

      BRIEFING=""
      if [ "$OLLAMA_OK" = "yes" ]; then
        REQ=$(jq -n --arg m "$VOICE_MODEL" --arg p "$PROMPT" \
          '{model:$m, prompt:$p, stream:false,
            options:{temperature:0.4, num_predict:160}}')
        BRIEFING=$(curl -fsS --max-time 20 "''${LLM_URL}/api/generate" \
          -H 'content-type: application/json' -d "$REQ" \
          | jq -r '.response // empty' || true)
      fi

      # Fallback if the model is still downloading on first boot.
      if [ -z "$BRIEFING" ]; then
        if [ "$LANG_CODE" = "ko" ]; then
          BRIEFING="다시 오신 것을 환영합니다. 시스템 ''${NET}; 디스크 ''${DISK_FREE}; \
배터리 ''${BATTERY}. 마지막 작업: ''${LAST_TASK}. 할 일 ''${TODO_COUNT}건."
        else
          BRIEFING="Welcome back. Systems ''${NET}; disk ''${DISK_FREE}; battery ''${BATTERY}. \
Last task: ''${LAST_TASK}. ''${TODO_COUNT} open items."
        fi
      fi

      # --- 3. Show the on-screen card --------------------------------------
      notify-send -a "CAM" -u normal -t 15000 \
        "CAM — morning briefing" "$BRIEFING" || true

      # --- 4. Speak it (only if the voice file is present) -----------------
      if [ -r "$PIPER_VOICE" ]; then
        WAV="$(mktemp --suffix=.wav)"
        printf '%s' "$BRIEFING" \
          | piper --model "$PIPER_VOICE" --output_file "$WAV" >/dev/null 2>&1 || true
        [ -s "$WAV" ] && aplay -q "$WAV" || true
        rm -f "$WAV"
      fi

      # --- 5. Write back last_boot so next login knows when we were here ---
      TMP="$(mktemp)"
      jq --arg t "$NOW" '.last_boot = $t' "$STATE_FILE" > "$TMP" && mv "$TMP" "$STATE_FILE"
    '';
  };
in
{
  environment.systemPackages = [ greeterScript ];

  # Seed the session-state file so the greeter on a fresh stick has something
  # to read instead of a "nothing recorded" on every login.
  environment.etc."latheos/session.default.json".text = ''
    {
      "last_task": "Welcome to LatheOS. Say 'Hey CAM' any time.",
      "todos": [
        "Pair your Picovoice key (put it in /persist/secrets/cam.env)",
        "Try: open the embedded shell from the menu",
        "Try: ask CAM to check the system flake with 'nix flake check'"
      ],
      "last_boot": null
    }
  '';

  systemd.tmpfiles.rules = [
    "d /persist/state 0755 dev users - -"
    # Copy the default seed in on first boot only (the 'C!' copies only if
    # the target does not exist yet — it never overwrites real state).
    "C! /persist/state/session.json 0644 dev users - /etc/latheos/session.default.json"
  ];

  # --- user-level service: runs once per graphical login. Using a user unit
  # keeps the DISPLAY / WAYLAND_DISPLAY / PIPEWIRE sockets in scope so both
  # notify-send and aplay actually reach the user's session.
  systemd.user.services.cam-greeter = {
    description = "CAM — login briefing (status check + voice greeting)";
    after   = [ "graphical-session.target" "pipewire.service" ];
    wants   = [ "graphical-session.target" ];
    wantedBy = [ "graphical-session.target" ];

    serviceConfig = {
      Type = "oneshot";
      ExecStartPre = "${pkgs.coreutils}/bin/sleep 2";  # let wofi/sway settle
      ExecStart = "${greeterScript}/bin/cam-greeter";
      # Never block login if the greeter hits an error; it is non-essential.
      SuccessExitStatus = "0 1";
    };
  };
}

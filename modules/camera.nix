################################################################################
# LatheOS Camera — "see through the camera", fully local.
#
# Gives the assistant eyes without any cloud: a single webcam frame is grabbed
# on demand and routed to one of two local brains (see daemon/cam_daemon/
# vision.py):
#
#   * "what do you see / describe this" -> a vision-capable OLLAMA model you
#     pull yourself (e.g. `ollama pull llama3.2-vision`). General scene
#     understanding, privacy-friendly, CPU/GPU.
#   * "where is X / find the button"    -> the opt-in LocateAnything grounding
#     service (modules/vision-grounding.nix). Pixel boxes / points.
#
# This module only provides the *capture* plumbing (v4l2 + ffmpeg, the `video`
# group, a `cam-capture` helper and an env file). The routing lives in the
# daemon. Frames are written to a tmpfile and never leave the machine.
################################################################################

{ config, pkgs, lib, ... }:

let
  # A tiny, dependency-pinned single-frame grabber. `cam-capture [out.jpg]`
  # grabs one frame from $LATHEOS_CAMERA_DEVICE (default /dev/video0). Used by
  # the daemon and available to the user / shell for quick tests.
  camCapture = pkgs.writeShellApplication {
    name = "cam-capture";
    runtimeInputs = [ pkgs.ffmpeg pkgs.coreutils ];
    text = ''
      set -euo pipefail
      DEV="''${LATHEOS_CAMERA_DEVICE:-/dev/video0}"
      OUT="''${1:-}"
      if [ -z "$OUT" ]; then OUT="$(mktemp --suffix=.jpg)"; fi
      if [ ! -e "$DEV" ]; then
        echo "cam-capture: no camera at $DEV" >&2
        exit 1
      fi
      # -frames:v 1 grabs a single frame; -loglevel error keeps it quiet.
      ffmpeg -y -loglevel error -f v4l2 -i "$DEV" -frames:v 1 "$OUT"
      echo "$OUT"
    '';
  };
in
{
  # Webcam tooling + the capture helper.
  environment.systemPackages = [ pkgs.v4l-utils pkgs.ffmpeg camCapture ];

  # The interactive user needs the `video` group to open /dev/video*. This is
  # additive — it merges with any extraGroups set elsewhere for `dev`.
  users.users.dev.extraGroups = [ "video" ];

  # Runtime contract for the daemon + shell. Override per-drive in
  # /persist/secrets/camera.env (e.g. a different /dev/videoN).
  environment.etc."latheos/camera.env".text = ''
    # --- LatheOS camera vision (fully local) ----------------------------
    LATHEOS_CAMERA_ENABLE=1
    LATHEOS_CAMERA_DEVICE=/dev/video0
    # Scene description model (pull it yourself with `ollama pull`). Leave
    # blank to disable "what do you see". "Where is X" uses the separate
    # LocateAnything service (LATHEOS_VISION_URL) when latheos.vision.enable.
    LATHEOS_VLM_MODEL=llama3.2-vision
  '';
}

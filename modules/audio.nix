################################################################################
# PipeWire — tuned for sub-10ms capture latency.
#
# The CAM daemon needs consistent low-latency PCM to hit the "wake → WS open"
# budget. We disable PulseAudio, let PipeWire own the graph, and pin the
# capture quantum small enough that Porcupine sees fresh frames in-order.
################################################################################

{ config, pkgs, lib, ... }:

{
  security.rtkit.enable = true;

  services.pulseaudio.enable = false;

  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;
    jack.enable = true;
    wireplumber.enable = true;

    extraConfig.pipewire."99-latheos-lowlatency" = {
      "context.properties" = {
        "default.clock.rate" = 48000;
        "default.clock.quantum" = 256;
        "default.clock.min-quantum" = 64;
        "default.clock.max-quantum" = 1024;
      };
    };
  };

  # Grant the dev user realtime scheduling headroom without full root.
  security.pam.loginLimits = [
    { domain = "@audio"; type = "-"; item = "rtprio";    value = "95"; }
    { domain = "@audio"; type = "-"; item = "memlock";   value = "unlimited"; }
    { domain = "@audio"; type = "-"; item = "nice";      value = "-19"; }
  ];
}

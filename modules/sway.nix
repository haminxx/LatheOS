################################################################################
# Sway — stark monochrome tiling.
#
# The aesthetic rule: zero gradients, zero translucency, one accent grey.
# Rendering cost on emulated GPUs collapses when we disable blur/shadows and
# pin everything to an 8-bit monochrome palette.
################################################################################

{ config, pkgs, lib, ... }:

let
  # Cursor IDE ships only as an x86_64 AppImage today. Gate it so aarch64
  # builds still evaluate cleanly; the $mod+c keybind stays defined but
  # points at a stub `cursor-unavailable` that logs and exits.
  isX86 = pkgs.stdenv.hostPlatform.isx86_64;
  cursorBin =
    if isX86
    then "${pkgs.code-cursor}/bin/cursor"
    else "${pkgs.writeShellScript "cursor-unavailable" ''
      notify-send "Cursor not available on $(uname -m)" || true
      exit 1
    ''}";

  # Single source of truth for the palette. Every themed component reads from
  # here so a one-line change restyles the whole OS.
  palette = {
    bg        = "#0a0a0a";
    surface   = "#141414";
    border    = "#1f1f1f";
    text      = "#e8e8e8";
    textDim   = "#8a8a8a";
    accent    = "#f2f2f2";  # near-white; the *only* contrast note
    urgent    = "#5c5c5c";  # muted on purpose — no red alarms in the aesthetic
  };

  swayConfig = pkgs.writeText "sway-config" ''
    # ---- LatheOS monochrome ----
    set $mod Mod4
    set $term foot
    set $menu wofi --show drun --style /etc/latheos/wofi.css

    # Typography: geometric, monospaced, monochrome.
    font pango:JetBrains Mono 10

    # Gaps create the minimalist grid; no titlebars, no extras.
    default_border pixel 1
    default_floating_border pixel 1
    titlebar_padding 1
    hide_edge_borders smart
    gaps inner 8
    gaps outer 0
    smart_gaps on

    # Palette ─ border / background / text / indicator / child_border
    client.focused          ${palette.accent} ${palette.surface} ${palette.text}    ${palette.accent} ${palette.accent}
    client.focused_inactive ${palette.border} ${palette.bg}      ${palette.textDim} ${palette.border} ${palette.border}
    client.unfocused        ${palette.border} ${palette.bg}      ${palette.textDim} ${palette.border} ${palette.border}
    client.urgent           ${palette.urgent} ${palette.bg}      ${palette.text}    ${palette.urgent} ${palette.urgent}
    client.background       ${palette.bg}

    output * bg ${palette.bg} solid_color

    # Input — tuned for laptop trackpads and mech keyboards alike.
    input "type:keyboard" {
        xkb_options caps:escape
        repeat_delay 250
        repeat_rate 45
    }
    input "type:touchpad" {
        tap enabled
        natural_scroll enabled
        accel_profile adaptive
    }

    # Launch the CAM daemon indicator (purely informational; the daemon itself
    # is a systemd service, this is just a Waybar-free status line).
    exec_always --no-startup-id ${pkgs.mako}/bin/mako

    # Core keybinds — curated, not exhaustive.
    bindsym $mod+Return exec $term
    bindsym $mod+d exec $menu
    bindsym $mod+q kill
    bindsym $mod+Shift+e exec swaymsg exit

    bindsym $mod+h focus left
    bindsym $mod+j focus down
    bindsym $mod+k focus up
    bindsym $mod+l focus right
    bindsym $mod+Shift+h move left
    bindsym $mod+Shift+j move down
    bindsym $mod+Shift+k move up
    bindsym $mod+Shift+l move right

    bindsym $mod+b splith
    bindsym $mod+v splitv
    bindsym $mod+f fullscreen toggle

    # Cursor IDE — the primary dev surface on LatheOS. $mod+c opens it on
    # whatever the current workspace is; CAM can also issue
    #   {"action":"open_app","command":"cursor <path>"}
    # via the daemon executor to open a specific project (see
    # daemon/cam_daemon/executor.py — "cursor" is on the allowlist).
    bindsym $mod+c exec ${cursorBin}
    # Keep Cursor borders minimal to match the LatheOS monochrome aesthetic.
    for_window [app_id="Cursor"] border pixel 1
    for_window [class="Cursor"]  border pixel 1

    # Embedded LatheOS shell (`lathe` — Jarvis HUD + chat + terminal). This
    # is the offline-first dev surface; Cursor is the heavyweight option.
    bindsym $mod+o exec foot -T "LatheOS Shell" lathe --color
    for_window [title="LatheOS Shell"] border pixel 1

    # Push-to-talk — lets us live without Picovoice while the key is pending.
    # Tap the bind, camctl synthesises a wake_word activation; the daemon
    # opens a session exactly as it would for "Hey CAM".
    bindsym $mod+space exec camctl activate --kind wake_word

    # Workspaces 1–9.
    bindsym $mod+1 workspace number 1
    bindsym $mod+2 workspace number 2
    bindsym $mod+3 workspace number 3
    bindsym $mod+4 workspace number 4
    bindsym $mod+5 workspace number 5
    bindsym $mod+Shift+1 move container to workspace number 1
    bindsym $mod+Shift+2 move container to workspace number 2
    bindsym $mod+Shift+3 move container to workspace number 3
    bindsym $mod+Shift+4 move container to workspace number 4
    bindsym $mod+Shift+5 move container to workspace number 5

    # Screen dump — press-to-pin for CAM-assisted workflows.
    bindsym Print exec grim -g "$(slurp)" - | swappy -f -

    # Minimal bar — white-on-black, no widgets, system time only.
    bar {
        position top
        status_command while date +'%H:%M  ·  %Y-%m-%d'; do sleep 15; done
        colors {
            background ${palette.bg}
            statusline ${palette.text}
            separator  ${palette.border}
            focused_workspace  ${palette.accent} ${palette.surface} ${palette.text}
            active_workspace   ${palette.border} ${palette.bg}      ${palette.textDim}
            inactive_workspace ${palette.border} ${palette.bg}      ${palette.textDim}
            urgent_workspace   ${palette.urgent} ${palette.bg}      ${palette.text}
        }
    }

    include /etc/sway/config.d/*
  '';

in {
  # Cursor is proprietary; accept that on x86_64 hosts only.
  nixpkgs.config.allowUnfreePredicate = pkg:
    isX86 && builtins.elem (lib.getName pkg) [ "cursor" "code-cursor" ];

  programs.sway = {
    enable = true;
    wrapperFeatures.gtk = true;
    extraPackages = with pkgs; [
      foot swaylock swayidle wofi mako grim slurp swappy wl-clipboard
      jetbrains-mono libnotify
    ] ++ lib.optionals isX86 [ pkgs.code-cursor ];
  };

  # Publish the generated config at a stable path and symlink it into the
  # dev user's home. Keeping the *source of truth* in /etc means rebuilding
  # the system rebuilds the UI — no stale dotfiles.
  environment.etc."sway/config".source = swayConfig;

  environment.etc."latheos/wofi.css".text = ''
    window { background-color: ${palette.bg}; color: ${palette.text};
             border: 1px solid ${palette.border}; font-family: "JetBrains Mono"; }
    #input { background-color: ${palette.surface}; color: ${palette.text};
             border: none; padding: 8px; }
    #entry { padding: 6px 10px; }
    #entry:selected { background-color: ${palette.surface}; color: ${palette.accent}; }
  '';

  # Auto-launch Sway on tty1 for the dev user. No display manager — greeters
  # add boot latency and clash with the minimalist spec.
  services.greetd = {
    enable = true;
    settings.default_session = {
      command = "${pkgs.greetd.tuigreet}/bin/tuigreet --time --remember --cmd sway";
      user = "greeter";
    };
  };

  fonts.packages = with pkgs; [ jetbrains-mono inter ];

  environment.sessionVariables = {
    MOZ_ENABLE_WAYLAND = "1";
    QT_QPA_PLATFORM = "wayland";
    SDL_VIDEODRIVER = "wayland";
    XDG_CURRENT_DESKTOP = "sway";
    XDG_SESSION_TYPE = "wayland";
  };
}

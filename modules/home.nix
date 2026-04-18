################################################################################
# Home-Manager integration.
#
# System-wide Sway config at /etc/sway/config is the source of truth. This
# module adds per-user polish: shell, editor, foot, and a Sway user-level
# include that extends the system config without duplicating it.
################################################################################

{ config, pkgs, lib, ... }:

{
  home-manager.useGlobalPkgs = true;
  home-manager.useUserPackages = true;
  home-manager.backupFileExtension = "hm-backup";

  home-manager.users.dev = { pkgs, ... }: {
    home.stateVersion = "24.11";

    home.packages = with pkgs; [
      fzf zoxide direnv delta lazygit
    ];

    programs.zsh = {
      enable = true;
      enableCompletion = true;
      autosuggestion.enable = true;
      syntaxHighlighting.enable = true;
      initExtra = ''
        # LatheOS prompt — minimal, monochrome, one line.
        PROMPT='%F{244}%~%f %# '
        eval "$(zoxide init zsh)"
      '';
    };

    programs.git = {
      enable = true;
      userName = lib.mkDefault "LatheOS Dev";
      userEmail = lib.mkDefault "dev@latheos.local";
      delta.enable = true;
      extraConfig.init.defaultBranch = "main";
    };

    programs.neovim = {
      enable = true;
      defaultEditor = true;
      viAlias = true;
      vimAlias = true;
    };

    programs.foot = {
      enable = true;
      settings = {
        main = {
          font = "JetBrains Mono:size=11";
          pad = "8x8";
          dpi-aware = "yes";
        };
        colors = {
          background = "0a0a0a";
          foreground = "e8e8e8";
          regular0 = "141414";
          regular7 = "e8e8e8";
          bright0  = "1f1f1f";
          bright7  = "f2f2f2";
        };
      };
    };

    # Per-user Sway overlay — sourced after /etc/sway/config via `include`.
    xdg.configFile."sway/config.d/10-user.conf".text = ''
      for_window [window_role="pop-up"] floating enable
      for_window [window_role="task_dialog"] floating enable
      for_window [app_id="cam-overlay"] move container to workspace number 2
    '';
  };
}

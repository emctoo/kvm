{ pkgs, ... }:
{
  projectRootFile = "flake.nix";

  # Nix — RFC 166 style (same style used by nixpkgs itself)
  programs.nixfmt = {
    enable = true;
    package = pkgs.nixfmt-rfc-style;
  };

  # Python — ruff formatter (replaces black)
  programs.ruff-format.enable = true;
}

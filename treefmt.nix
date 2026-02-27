{ pkgs, ... }:
{
  projectRootFile = "flake.nix";

  # Nix — RFC 166 style (pkgs.nixfmt is now the RFC-style formatter)
  programs.nixfmt.enable = true;

  # Python — ruff formatter (replaces black)
  programs.ruff-format.enable = true;
}

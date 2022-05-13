{ system ? builtins.currentSystem, sources ? import ./nix/sources.nix, pkgs ? import sources.nixpkgs { inherit system; overlays = [ ]; } }:

let
  myAppEnv = pkgs.poetry2nix.mkPoetryEnv {
    projectDir = ./.;
    editablePackageSources = {
      my-app = ./.;
    };
  };
in myAppEnv.env

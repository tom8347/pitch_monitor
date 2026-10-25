{
  description = "Vocal pitch monitor: see the pitch you sing on a scrolling piano roll, or the note on a staff";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
  let
    system = "x86_64-linux";
    pkgs = import nixpkgs { inherit system; };
    package = pkgs.callPackage ./nix/package.nix { };
    py = pkgs.python3.withPackages (ps: with ps; [
      pynvim
      python-lsp-server
      pylsp-mypy
      pylsp-rope
      ipython
      # project
      numpy
      pyqt6
    ]);
    # in the dev shell the commands run the working tree, not the packaged copy
    dev = name: script: pkgs.writeShellScriptBin name ''exec python "$PWD/${script}" "$@"'';
  in
  {
    packages.${system}.default = package;

    apps.${system} = {
      default = { type = "app"; program = "${package}/bin/pitch-monitor"; };
      pitch-staff = { type = "app"; program = "${package}/bin/pitch-staff"; };
    };

    devShells.${system}.default = pkgs.mkShell {
      packages = [
        py
        pkgs.pipewire       # pw-record
        pkgs.qt6.qtwayland
        (dev "pitch-monitor" "pitch_monitor.py")
        (dev "pitch-staff" "pitch_staff.py")
      ];

      shellHook = ''
        export QT_QPA_PLATFORM=wayland
        export PITCH_STAFF_FONT=${package.bravura}
        echo "Pitch monitor environment"
        echo "Python: $(python --version | cut -d' ' -f2)   numpy: $(python -c 'import numpy; print(numpy.__version__)')   PyQt6: $(python -c 'from PyQt6.QtCore import PYQT_VERSION_STR; print(PYQT_VERSION_STR)')"
        echo "Run: pitch-monitor, pitch-staff (the working tree)"
      '';
    };
  };
}

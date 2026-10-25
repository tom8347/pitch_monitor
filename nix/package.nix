# Both apps, on the same pitch detection, for the pitch you sing into the
# default PipeWire source (PyQt6 + numpy, audio from pw-record):
#   pitch-monitor (pitch_monitor.py): a scrolling piano roll, like the Android
#     app Vocal Pitch Monitor
#   pitch-staff (pitch_staff.py): the note on a tenor (treble 8vb) staff in a
#     chosen key, green or red for its tuning; notation in the Bravura font
{ lib, writeShellScriptBin, makeDesktopItem, symlinkJoin, fetchurl, python3, pipewire, qt6 }:

let
  python = python3.withPackages (ps: [ ps.numpy ps.pyqt6 ]);
  # pitch_staff.py imports pitch_monitor.py, so they share a directory
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [ ../pitch_monitor.py ../pitch_staff.py ];
  };
  bravura = fetchurl {
    url = "https://raw.githubusercontent.com/steinbergmedia/bravura/37b194378b710cc40e406ab6c4b07608bb9548ae/redist/otf/Bravura.otf";
    hash = "sha256-zfD4k+4f22S39nE9ce4Nz8NJwKwBQpqORRsBqeefXzs=";
  };
  app = name: script: writeShellScriptBin name ''
    export PATH=${lib.makeBinPath [ pipewire ]}:$PATH
    export QT_PLUGIN_PATH=${qt6.qtwayland}/${qt6.qtbase.qtPluginPrefix}''${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}
    export QT_QPA_PLATFORM=''${QT_QPA_PLATFORM:-wayland;xcb}
    export PITCH_STAFF_FONT=${bravura}
    exec ${python}/bin/python3 ${src}/${script} "$@"
  '';
  desktop = name: desktopName: comment: makeDesktopItem {
    inherit name desktopName comment;
    exec = name;
    icon = "audio-input-microphone";
    categories = [ "AudioVideo" "Audio" "Music" ];
  };
in
symlinkJoin {
  name = "pitch-monitor";
  paths = [
    (app "pitch-monitor" "pitch_monitor.py")
    (desktop "pitch-monitor" "Pitch Monitor" "See the pitch you sing on a piano roll")
    (app "pitch-staff" "pitch_staff.py")
    (desktop "pitch-staff" "Pitch Staff" "See the note you sing on a staff, and its tuning")
  ];
  passthru = { inherit bravura; };
  meta = {
    description = "Vocal pitch monitor: a scrolling piano roll, and the note on a staff";
    homepage = "https://github.com/tom8347/pitch_monitor";
    platforms = lib.platforms.linux;
    mainProgram = "pitch-monitor";
  };
}

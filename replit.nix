{ pkgs }: {
  deps = [
    pkgs.python313
    pkgs.python313Packages.pip
    pkgs.python313Packages.virtualenv
    pkgs.postgresql_16
    pkgs.openssl
    pkgs.gcc
    pkgs.libffi
  ];

  env = {
    PYTHONPATH = "$PYTHONPATH:$REPL_HOME";
    LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
      pkgs.openssl
      pkgs.libffi
    ];
  };
}

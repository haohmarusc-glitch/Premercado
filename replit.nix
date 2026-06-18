{ pkgs }: {
  deps = [
    pkgs.python311Packages.psycopg2
    pkgs.python311Packages.openpyxl
  ];
}

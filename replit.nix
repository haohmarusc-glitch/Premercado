{ pkgs }: {
  deps = [
    pkgs.mailutils
    pkgs.python311Packages.psycopg2
    pkgs.python311Packages.openpyxl
  ];
}

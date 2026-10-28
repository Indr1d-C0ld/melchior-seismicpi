"""Comandi di amministrazione da terminale.

    python3 -m seismicpi.admin set-password [--user admin]   # chiede la password (o la legge da stdin)
    python3 -m seismicpi.admin show-config
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys

from . import config


def main():
    ap = argparse.ArgumentParser(prog="seismicpi.admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("set-password", help="imposta utente e password della dashboard")
    sp.add_argument("--user", default=None)
    sp.add_argument("--stdin", action="store_true", help="legge la password da stdin (per gli script)")
    sub.add_parser("show-config", help="stampa la configurazione senza segreti")
    a = ap.parse_args()
    cfg = config.load()
    if a.cmd == "show-config":
        print(json.dumps(config.redacted(cfg), indent=2, ensure_ascii=False))
        return
    from .web.server import hash_password
    if a.stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("Nuova password della dashboard: ")
        if pw != getpass.getpass("Ripeti: "):
            sys.exit("le password non coincidono")
    if len(pw) < 6:
        sys.exit("la password deve avere almeno 6 caratteri")
    cfg["web"]["password_hash"] = hash_password(pw)
    if a.user:
        cfg["web"]["username"] = a.user
    config.save(cfg)
    print(f"password impostata per l'utente '{cfg['web']['username']}'")


if __name__ == "__main__":
    main()

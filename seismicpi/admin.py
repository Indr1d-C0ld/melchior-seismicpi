"""Comandi di amministrazione da terminale.

    python3 -m seismicpi.admin set-password [--user admin]   # chiede la password (o la legge da stdin)
    python3 -m seismicpi.admin set-ap-password [--random | --stdin] [--if-default]
    python3 -m seismicpi.admin show-config
"""
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys

from . import config


def main():
    ap = argparse.ArgumentParser(prog="seismicpi.admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("set-password", help="imposta utente e password della dashboard")
    sp.add_argument("--user", default=None)
    sp.add_argument("--stdin", action="store_true", help="legge la password da stdin (per gli script)")
    sa = sub.add_parser("set-ap-password", help="password dell'Access Point di emergenza")
    sa.add_argument("--random", action="store_true", help="genera una password casuale e la stampa")
    sa.add_argument("--stdin", action="store_true", help="legge la password da stdin")
    sa.add_argument("--if-default", action="store_true", help="agisce solo se è ancora quella predefinita")
    sub.add_parser("show-config", help="stampa la configurazione senza segreti")
    a = ap.parse_args()
    cfg = config.load()
    if a.cmd == "show-config":
        print(json.dumps(config.redacted(cfg), indent=2, ensure_ascii=False))
        return
    if a.cmd == "set-ap-password":
        if a.if_default and cfg["network"]["ap_password"] != config.DEFAULTS["network"]["ap_password"]:
            print(cfg["network"]["ap_password"])
            return
        if a.random:
            alphabet = "abcdefghjkmnpqrstuvwxyz23456789"        # niente caratteri ambigui (l/1, o/0)
            pw = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
        elif a.stdin:
            pw = sys.stdin.readline().rstrip("\n")
        else:
            pw = getpass.getpass("Password dell'Access Point (8-63 caratteri): ")
        cfg["network"]["ap_password"] = pw
        try:
            config.save(cfg)      # valida la lunghezza
        except config.ConfigError as e:
            sys.exit(str(e))
        print(pw)
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

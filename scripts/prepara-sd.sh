#!/bin/bash
# SeismicPi – prepara una SD di Raspberry Pi OS già scritta: utente, password, Wi-Fi, SSH, nome host.
#
#   ./scripts/prepara-sd.sh [PARTIZIONE_DI_BOOT]      (default: /media/$USER/bootfs)
#
# Serve quando la personalizzazione di Raspberry Pi Imager non è stata applicata (il Pi non si
# collega al Wi-Fi, l'utente "pi" non esiste o non può entrare). Lo script usa lo stesso meccanismo
# di primo avvio di Imager: crea firstrun.sh nella partizione di boot e lo fa eseguire al prossimo
# avvio, dopo il quale il Pi si riavvia da solo e lo script si cancella.
#
# Le password vengono chieste a video senza eco. Quella dell'utente finisce sulla SD solo come
# hash SHA-512; quella del Wi-Fi resta in chiaro nella partizione di boot fino al primo avvio.
set -euo pipefail

BOOT=${1:-/media/$USER/bootfs}
die() { echo "errore: $*" >&2; exit 1; }

[ -f "$BOOT/cmdline.txt" ] && [ -f "$BOOT/config.txt" ] || die "$BOOT non sembra la partizione di boot di un Raspberry (manca cmdline.txt)"
command -v openssl >/dev/null || die "serve openssl per l'hash della password"
grep -q "systemd.run=" "$BOOT/cmdline.txt" && die "c'è già un primo avvio in sospeso in cmdline.txt: avvia il Pi oppure rimuovilo a mano"

ask() { local v; read -r -p "$1 [$2]: " v; echo "${v:-$2}"; }
asksecret() {
  local a b
  while true; do
    read -r -s -p "$1: " a; echo >&2
    [ -n "${2:-}" ] || { echo "$a"; return; }
    read -r -s -p "Ripeti: " b; echo >&2
    [ "$a" = "$b" ] && { echo "$a"; return; }
    echo "  non coincidono, riprova" >&2
  done
}

echo "Preparazione della SD in $BOOT"
HOST=$(ask "Nome host" "seismicpi")
USERNAME=$(ask "Utente del sistema" "pi")
echo "$USERNAME" | grep -q '^[a-z][a-z0-9-]*$' || die "nome utente non valido"
PASS=$(asksecret "Password per l'utente $USERNAME" ripeti)
[ ${#PASS} -ge 6 ] || die "password troppo corta"
SSID=$(ask "Nome della rete Wi-Fi (SSID, solo 2,4 GHz per molte chiavette)" "")
[ -n "$SSID" ] || die "SSID obbligatorio"
WPASS=$(asksecret "Password del Wi-Fi \"$SSID\"" ripeti)
COUNTRY=$(ask "Paese Wi-Fi" "IT")
TZ_=$(ask "Fuso orario" "Europe/Rome")
KEYMAP=$(ask "Tastiera" "it")

HASH=$(printf '%s' "$PASS" | openssl passwd -6 -stdin)
unset PASS

q() { printf '%q' "$1"; }
cat > "$BOOT/firstrun.sh" <<EOF
#!/bin/bash
# primo avvio generato da SeismicPi scripts/prepara-sd.sh – si cancella da solo
set +e
FWLOC=\$(/usr/lib/raspberrypi-sys-mods/get_fw_loc 2>/dev/null || echo /boot)
/usr/lib/raspberrypi-sys-mods/imager_custom set_hostname $(q "$HOST")
/usr/lib/raspberrypi-sys-mods/imager_custom enable_ssh
/usr/lib/userconf-pi/userconf $(q "$USERNAME") $(q "$HASH")
/usr/lib/raspberrypi-sys-mods/imager_custom set_wlan --plain $(q "$SSID") $(q "$WPASS") $(q "$COUNTRY")
/usr/lib/raspberrypi-sys-mods/imager_custom set_keymap $(q "$KEYMAP")
/usr/lib/raspberrypi-sys-mods/imager_custom set_timezone $(q "$TZ_")
rm -f "\$FWLOC/firstrun.sh"
sed -i 's| systemd.run.*||g' "\$FWLOC/cmdline.txt"
exit 0
EOF
unset WPASS

# una sola riga in cmdline.txt: si aggiunge l'esecuzione al primo avvio, seguita da un riavvio
cp "$BOOT/cmdline.txt" "$BOOT/cmdline.txt.bak"
tr -d '\n' < "$BOOT/cmdline.txt.bak" | sed 's/[[:space:]]*$//' > "$BOOT/cmdline.txt"
printf ' systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target\n' >> "$BOOT/cmdline.txt"
sync

echo
echo "Fatto. Copia di sicurezza: $BOOT/cmdline.txt.bak"
echo "Smonta la SD, inseriscila nel Pi e accendilo: al primo avvio applica le impostazioni e si"
echo "riavvia da solo (sul Pi Zero ci vogliono 2-3 minuti). Poi: ssh $USERNAME@$HOST.local"

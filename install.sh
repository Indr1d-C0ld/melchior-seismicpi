#!/bin/bash
# SeismicPi – installazione completa e idempotente su Raspberry Pi OS Lite (32 bit, Bookworm o Trixie).
#
#   sudo ./install.sh [--dest /opt/seismicpi] [--user admin] [--hostname seismicpi] [--country IT]
#                     [--rtc] [--sim] [--no-sd-protect] [--no-password]
#
# Passi:
#   1. pacchetti Debian (python3-aiohttp, smbus2, i2c-tools, NetworkManager, …)
#   2. bus I2C abilitato (400 kHz) ed eventuale modulo RTC DS3231 (--rtc)
#   3. utente di servizio "seismic", codice in /opt/seismicpi, dati in /var/lib/seismicpi
#   4. permessi (polkit) per Wi-Fi, ora, riavvio e servizi propri
#   5. protezione della SD (journal in RAM, /tmp in RAM, niente swap su SD, noatime, timer apt off)
#   6. servizi systemd: acquisizione, dashboard (porta 80), guardiano Wi-Fi/Access Point
#   7. utente e password della dashboard (chiesti a video, salvati solo come hash)
#
# Rilanciarlo aggiorna il codice e riavvia i servizi senza toccare dati e configurazione.
set -euo pipefail

ROOT=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
DEST=/opt/seismicpi
DATA=/var/lib/seismicpi
ADMIN_USER=admin
HOSTNAME_NEW=""
COUNTRY=IT
DO_RTC=0 DO_SIM=0 DO_SD=1 DO_PW=1

while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST=$2; shift ;;
    --user) ADMIN_USER=$2; shift ;;
    --hostname) HOSTNAME_NEW=$2; shift ;;
    --country) COUNTRY=$2; shift ;;
    --rtc) DO_RTC=1 ;;
    --sim) DO_SIM=1 ;;
    --no-sd-protect) DO_SD=0 ;;
    --no-password) DO_PW=0 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "opzione sconosciuta: $1" >&2; exit 2 ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || { echo "Esegui con sudo: sudo $0 $*" >&2; exit 1; }
step() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }

BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
IS_PI=0
grep -qi raspberry /proc/device-tree/model 2>/dev/null && IS_PI=1
[ $IS_PI -eq 1 ] && note "Scheda: $(tr -d '\0' < /proc/device-tree/model)" || note "ATTENZIONE: non sembra un Raspberry Pi (continuo comunque)"

# ---------------------------------------------------------------- 1. pacchetti
step "Pacchetti"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-aiohttp python3-smbus2 i2c-tools network-manager dnsmasq-base iw \
  avahi-daemon usbutils rsync
apt-get install -y -q python3-paho-mqtt || note "python3-paho-mqtt non disponibile: MQTT disattivato"

# ---------------------------------------------------------------- 2. I2C / RTC
step "Bus I2C"
if [ $IS_PI -eq 1 ]; then
  if command -v raspi-config >/dev/null; then raspi-config nonint do_i2c 0 || true; fi
  grep -q '^dtparam=i2c_arm=on' "$BOOTCFG" || echo 'dtparam=i2c_arm=on' >> "$BOOTCFG"
  grep -q '^dtparam=i2c_arm_baudrate=' "$BOOTCFG" || echo 'dtparam=i2c_arm_baudrate=400000' >> "$BOOTCFG"
  grep -q '^i2c-dev' /etc/modules 2>/dev/null || echo 'i2c-dev' >> /etc/modules
  modprobe i2c-dev 2>/dev/null || true
  if [ $DO_RTC -eq 1 ]; then
    grep -q 'dtoverlay=i2c-rtc,ds3231' "$BOOTCFG" || echo 'dtoverlay=i2c-rtc,ds3231' >> "$BOOTCFG"
    note "RTC DS3231 abilitato (attivo dopo il riavvio)"
  fi
  if command -v raspi-config >/dev/null; then raspi-config nonint do_wifi_country "$COUNTRY" || true; fi
fi
getent group i2c >/dev/null || groupadd -r i2c
getent group gpio >/dev/null || groupadd -r gpio

# ---------------------------------------------------------------- 3. utente, codice, dati
step "Utente di servizio e file"
id seismic >/dev/null 2>&1 || useradd -r -d "$DATA" -s /usr/sbin/nologin seismic
for g in i2c gpio netdev systemd-journal; do getent group $g >/dev/null && usermod -aG $g seismic; done
mkdir -p "$DEST" "$DATA"
rsync -a --delete --exclude '__pycache__' "$ROOT/seismicpi" "$DEST/"
cp "$ROOT/install.sh" "$ROOT/README.md" "$DEST/" 2>/dev/null || true
chown -R root:root "$DEST"
chmod -R a+rX "$DEST"
chown seismic:seismic "$DATA"
chmod 750 "$DATA"
if [ ! -f "$DATA/config.json" ] && [ $DO_SIM -eq 1 ]; then
  echo '{"sensor": {"driver": "sim"}}' > "$DATA/config.json"
  chown seismic:seismic "$DATA/config.json"; chmod 600 "$DATA/config.json"
  note "configurazione iniziale con sensore simulato"
fi
install -m 644 "$ROOT/system/seismicpi.tmpfiles" /etc/tmpfiles.d/seismicpi.conf
systemd-tmpfiles --create /etc/tmpfiles.d/seismicpi.conf

# ---------------------------------------------------------------- 4. permessi
step "Permessi (polkit)"
mkdir -p /etc/polkit-1/rules.d
install -m 644 "$ROOT/system/seismicpi.polkit.rules" /etc/polkit-1/rules.d/50-seismicpi.rules
systemctl try-restart polkit 2>/dev/null || true

# ---------------------------------------------------------------- 5. protezione SD
if [ $DO_SD -eq 1 ]; then
  step "Protezione della scheda SD"
  mkdir -p /etc/systemd/journald.conf.d
  install -m 644 "$ROOT/system/journald-seismicpi.conf" /etc/systemd/journald.conf.d/seismicpi.conf
  systemctl restart systemd-journald
  note "journal solo in RAM (16 MB)"
  if ! findmnt -n /tmp | grep -q tmpfs; then
    if [ -f /usr/share/systemd/tmp.mount ] && [ ! -f /etc/systemd/system/tmp.mount ]; then
      cp /usr/share/systemd/tmp.mount /etc/systemd/system/tmp.mount
    fi
    systemctl enable tmp.mount 2>/dev/null && note "/tmp in RAM dal prossimo avvio" || note "/tmp in RAM non configurabile qui"
  else
    note "/tmp è già in RAM"
  fi
  if systemctl list-unit-files dphys-swapfile.service >/dev/null 2>&1 && systemctl is-enabled dphys-swapfile >/dev/null 2>&1; then
    dphys-swapfile swapoff 2>/dev/null || true
    systemctl disable --now dphys-swapfile 2>/dev/null || true
    note "swap su file disattivato (dphys-swapfile)"
  fi
  if [ -f /etc/rpi/swap.conf ] || [ -d /etc/rpi/swap.conf.d ]; then
    mkdir -p /etc/rpi/swap.conf.d
    printf '[Main]\nMechanism=zram\n' > /etc/rpi/swap.conf.d/90-seismicpi.conf
    note "rpi-swap: solo zram (compresso in RAM), niente file di swap su SD"
  fi
  # noatime + commit=120 sulla partizione di root (con copia di sicurezza di fstab)
  if awk '$2=="/" && $3=="ext4"' /etc/fstab | grep -q .; then
    cp -n /etc/fstab /etc/fstab.seismicpi.bak
    awk 'BEGIN{OFS="\t"} $2=="/" && $3=="ext4" {
           n=split($4, o, ","); has_na=0; has_c=0
           for (i=1;i<=n;i++) { if (o[i]=="noatime") has_na=1; if (o[i] ~ /^commit=/) has_c=1 }
           if (!has_na) $4=$4",noatime"; if (!has_c) $4=$4",commit=120" } {print}' /etc/fstab > /etc/fstab.new
    if cmp -s /etc/fstab /etc/fstab.new; then
      rm /etc/fstab.new; note "fstab già a posto (noatime, commit)"
    elif findmnt --verify --tab-file /etc/fstab.new >/dev/null 2>&1; then
      mv /etc/fstab.new /etc/fstab
      note "root montata con noatime,commit=120 dal prossimo avvio (copia in /etc/fstab.seismicpi.bak)"
    else
      rm /etc/fstab.new; note "fstab non modificato: la verifica di findmnt non è passata"
    fi
  fi
  for t in apt-daily.timer apt-daily-upgrade.timer man-db.timer; do
    systemctl disable --now "$t" 2>/dev/null && note "disattivato $t" || true
  done
fi

# ---------------------------------------------------------------- 6. servizi
step "Servizi"
for s in seismicpi-acq seismicpi-web seismicpi-netwatch; do
  install -m 644 "$ROOT/systemd/$s.service" /etc/systemd/system/
  sed -i "s#/opt/seismicpi#$DEST#g" "/etc/systemd/system/$s.service"
done
systemctl daemon-reload
systemctl enable seismicpi-acq seismicpi-web seismicpi-netwatch
systemctl restart seismicpi-acq
sleep 2
systemctl restart seismicpi-web seismicpi-netwatch

if [ -n "$HOSTNAME_NEW" ] && [ "$(hostname)" != "$HOSTNAME_NEW" ]; then
  hostnamectl set-hostname "$HOSTNAME_NEW"
  sed -i "s/127.0.1.1.*/127.0.1.1\t$HOSTNAME_NEW/" /etc/hosts
  note "nome host: $HOSTNAME_NEW (raggiungibile come $HOSTNAME_NEW.local)"
fi

# ---------------------------------------------------------------- 7. accesso alla dashboard
if [ $DO_PW -eq 1 ]; then
  step "Accesso alla dashboard"
  HAS_PW=$(sudo -u seismic env SEISMICPI_DATA="$DATA" python3 -c "
import sys; sys.path.insert(0, '$DEST')
from seismicpi import config; print(1 if config.load()['web']['password_hash'] else 0)")
  if [ "$HAS_PW" = "1" ]; then
    note "password già impostata (per cambiarla: dashboard → Sistema → Accesso)"
  elif [ -n "${SEISMICPI_ADMIN_PASSWORD:-}" ]; then
    printf '%s\n' "$SEISMICPI_ADMIN_PASSWORD" | sudo -u seismic env SEISMICPI_DATA="$DATA" PYTHONPATH="$DEST" \
      python3 -m seismicpi.admin set-password --user "$ADMIN_USER" --stdin
  else
    while true; do
      read -r -s -p "    Password per l'utente '$ADMIN_USER' della dashboard: " P1; echo
      read -r -s -p "    Ripeti: " P2; echo
      [ "$P1" = "$P2" ] && [ ${#P1} -ge 6 ] && break
      echo "    le password non coincidono o sono più corte di 6 caratteri"
    done
    printf '%s\n' "$P1" | sudo -u seismic env SEISMICPI_DATA="$DATA" PYTHONPATH="$DEST" \
      python3 -m seismicpi.admin set-password --user "$ADMIN_USER" --stdin
    unset P1 P2
  fi
  systemctl restart seismicpi-web
fi

# ---------------------------------------------------------------- riepilogo
step "Verifiche"
if command -v i2cdetect >/dev/null && [ -e /dev/i2c-1 ]; then
  if i2cdetect -y 1 2>/dev/null | grep -qE ' 68 | 69 '; then note "MPU-6050 trovato sul bus I2C"; else note "MPU-6050 NON trovato su i2c-1: controlla il cablaggio (vedi Guida)"; fi
else
  note "bus I2C non ancora disponibile: riavvia il Pi (sudo reboot)"
fi
if command -v iw >/dev/null; then
  if iw list 2>/dev/null | sed -n '/Supported interface modes/,/^\t[A-Z]/p' | grep -q '\* AP'; then
    note "adattatore Wi-Fi: modalità Access Point supportata"
  else
    note "ATTENZIONE: l'adattatore Wi-Fi non dichiara la modalità AP: il fallback potrebbe non funzionare"
  fi
fi
lsusb 2>/dev/null | grep -iv 'hub' | sed 's/^/    USB: /' || true
for s in seismicpi-acq seismicpi-web seismicpi-netwatch; do note "$s: $(systemctl is-active $s)"; done
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Dashboard: http://$(hostname).local  oppure  http://${IP:-<indirizzo-del-pi>}"
echo "Se il Pi non trova reti note crea il Wi-Fi 'SeismicPi' (password: sismografo) → http://192.168.4.1"
[ $IS_PI -eq 1 ] && ! [ -e /dev/i2c-1 ] && echo "Riavvia il Pi per attivare I2C: sudo reboot"
exit 0

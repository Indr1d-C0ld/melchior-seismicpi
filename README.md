# Melchior SeismicPi

Sismografo casalingo su **Raspberry Pi Zero** (non-W, Wi-Fi tramite dongle USB TP-Link) con
accelerometro **MPU-6050** (modulo GY-521). Il progetto è stato ricostruito da zero nel 2026,
dopo che la SD originale si è rotta.

Funzioni principali:

- acquisizione continua a 100 Hz dalla FIFO del sensore;
- trigger STA/LTA e classificazione automatica degli eventi;
- **registro eventi** con PGA, PGV, intensità MMI, Arias, CAV e frequenza dominante;
- **rullo digitale** (helicorder) come quello dei sismografi a carta;
- confronto con i cataloghi **INGV / EMSC / USGS**;
- **allerte Telegram** (con sismogramma e comandi), webhook, MQTT/Home Assistant, uscita GPIO;
- **dashboard** amministrativa completa, che funziona anche senza Internet;
- **Access Point di emergenza** se il Pi non trova reti note;
- telemetria hardware del Raspberry;
- archiviazione pensata per **non consumare la SD**.

![vecchio montaggio](pre_assets/photo_2026-10-28_11-11-16.jpg)

## Componenti

| Cartella | Cosa |
|---|---|
| `install.sh` | installazione completa e idempotente su Raspberry Pi OS Lite |
| `seismicpi/acquisition.py` | servizio di acquisizione: sensore, filtri, STA/LTA, eventi, archivio, socket IPC |
| `seismicpi/sensor/` | driver MPU-6050 (FIFO, autotest di fabbrica) e sensore **simulato** per le prove |
| `seismicpi/dsp.py`, `analysis.py` | filtri IIR, STA/LTA, velocità, intensità (Worden 2012), classificazione |
| `seismicpi/storage.py`, `miniseed.py` | archivio a blocchi, rullo, forme d'onda degli eventi, export miniSEED |
| `seismicpi/web/` | dashboard (aiohttp + JavaScript senza dipendenze esterne) e API |
| `seismicpi/notify.py`, `pngplot.py` | Telegram (bot con comandi), webhook, MQTT, GPIO, grafici PNG in puro Python |
| `seismicpi/catalogs.py` | cataloghi FDSN, distanze, arrivi P/S teorici, associazione degli eventi |
| `seismicpi/netwatch.py`, `netmgr.py` | guardiano Wi-Fi con Access Point di emergenza (NetworkManager) |
| `systemd/`, `system/` | servizi, polkit, tmpfiles, journald in RAM |
| `docs/` | [hardware e cablaggio](docs/HARDWARE.md) |
| `tests/` | test automatici (`python3 -m unittest discover -s tests`) |

Processi e flusso dei dati:

```
MPU-6050 ──I2C/FIFO──▶ seismicpi-acq ──socket UNIX (/run/seismicpi)──▶ seismicpi-web ──WebSocket──▶ browser
                          │  raw continui → RAM (/dev/shm)                │  Telegram / webhook / MQTT
                          │  rullo, eventi → SD (a blocchi)               │  cataloghi INGV/EMSC/USGS
                          └  SQLite (WAL, scritture raggruppate) ◀────────┘
seismicpi-netwatch: Wi-Fi nota oppure Access Point "SeismicPi" (192.168.4.1)
```

Sul Pi Zero tutto ciò che serve solo a guardare i dati (spettri, spettrogrammi, filtri, rullo,
moto della particella) viene calcolato dal browser. Il Pi si occupa solo di acquisire, rilevare e
archiviare.

## Installazione (Raspberry Pi OS Lite 32 bit)

1. Scrivi la SD con **Raspberry Pi Imager**: *Raspberry Pi OS Lite (32-bit)*. Nella
   personalizzazione imposta:
   - hostname `seismicpi`;
   - utente `pi` con la sua password;
   - la rete Wi-Fi di casa;
   - SSH attivo.
2. Collega il sensore come nella [guida al cablaggio](docs/HARDWARE.md), poi avvia il Pi.
3. Copia il progetto sul Pi e installa:

```bash
rsync -a --exclude pre_assets ./ pi@seismicpi.local:seismicpi/
ssh pi@seismicpi.local 'cd seismicpi && sudo ./install.sh'
```

L'installatore chiede la password della dashboard (utente `admin`) e la salva solo come hash.
Per un'installazione non interattiva usa `SEISMICPI_ADMIN_PASSWORD=... sudo -E ./install.sh`.
Alla fine riavvia il Pi (`sudo reboot`) per attivare I2C e le protezioni della SD.

Opzioni:

| Opzione | Effetto |
|---|---|
| `--rtc` | abilita un modulo RTC DS3231 sul bus I2C |
| `--sim` | usa il sensore simulato |
| `--hostname NOME` | imposta il nome host |
| `--country IT` | paese per le regole Wi-Fi |
| `--no-sd-protect` | non applica le protezioni della SD |
| `--no-password` | non chiede la password della dashboard |

Rilanciando `install.sh` aggiorni il codice; dati e configurazione restano intatti.

**Dashboard:** `http://seismicpi.local`. Se il Pi non trova reti note crea il Wi-Fi **SeismicPi**
(password `sismografo`, modificabile): collegati e apri `http://192.168.4.1`.

## Protezione della scheda SD

| Cosa | Dove | Scritture sulla SD |
|---|---|---|
| Dati grezzi continui (52 MB/giorno a 100 Hz) | **RAM** (`/dev/shm`, ultime 6 ore, configurabile; oppure SD o disattivati) | 0 |
| Rullo (inviluppi) | SD, ogni 5 minuti a blocchi | ~2,8 MB/giorno |
| Statistiche al minuto e telemetria | SQLite WAL, scritte a gruppi di 10 minuti | ~0,5 MB/giorno |
| Forme d'onda degli eventi | SD | 60-400 KB per evento |
| Journal di sistema | RAM (`Storage=volatile`, 16 MB) | 0 |
| Stato della rete, socket | `/run` (RAM) | 0 |
| `/tmp` | tmpfs | 0 |
| Swap | solo zram; `dphys-swapfile` disattivato | 0 |
| Root | `noatime,commit=120`; timer `apt-daily` e `man-db` disattivati | ridotte |

Le scritture effettive (byte scritti sulla SD al minuto) sono visibili nella dashboard, in
**Sistema → Hardware** e in **Statistiche → Sistema**.

## Sviluppo senza Raspberry

Il sensore simulato genera rumore realistico del MEMS, rumore domestico, urti, passi e terremoti
sintetici con fasi P e S:

```bash
export SEISMICPI_DATA=/tmp/spi SEISMICPI_RUN=/tmp/spi/run SEISMICPI_RAM=/tmp/spi/ram SEISMICPI_SIM_RATE=5
mkdir -p $SEISMICPI_DATA && echo '{"sensor":{"driver":"sim"}}' > $SEISMICPI_DATA/config.json
python3 -m seismicpi.acquisition &
SEISMICPI_PORT=8090 python3 -m seismicpi.web
```

Poi apri `http://localhost:8090`. Al primo accesso scegli utente e password. In
**Segnale live → Simula sisma** puoi generare un terremoto di prova.

Comandi utili:

```bash
python3 -m unittest discover -s tests            # test
python3 -m seismicpi.admin set-password --user admin   # cambia la password da terminale
python3 -m seismicpi.admin show-config            # configurazione senza segreti
```

## Limiti del sensore

L'MPU-6050 è un MEMS da droni e smartphone. Il suo rumore è di circa 2-4 mg RMS nella banda
sismica.

- **Rileva:** scosse locali percepibili (intensità III e oltre), forti terremoti regionali e tutto
  il rumore domestico. Il rumore domestico viene classificato ed etichettato.
- **Non rileva:** il microsisma e i terremoti lontani di magnitudo moderata.

Per confronto, un geofono (come quello di un Raspberry Shake) è circa 100 volte più sensibile.
La regola usata per decidere se un terremoto di catalogo era rilevabile è prudente: M2 a 10 km,
M4 a 100 km, M6 a 1000 km.

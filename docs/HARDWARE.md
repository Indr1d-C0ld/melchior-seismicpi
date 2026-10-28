# Hardware e cablaggio

## Componenti

| Pezzo | Note |
|---|---|
| Raspberry Pi con header a 40 pin | Zero, Zero W, Zero 2 W, 3, 4, 5. Su Zero e 1 (ARMv6) serve Raspberry Pi OS **32 bit** |
| Wi-Fi | integrato (Zero W e successivi) oppure chiavetta USB. Per l'Access Point di emergenza il driver deve supportare la modalità AP |
| GY-521 (MPU-6050) | accelerometro e giroscopio a 3 assi, I2C, 16 bit; si usa solo l'accelerometro |
| Alimentatore 5 V stabile, almeno 2,5 A | la sottotensione corrompe la SD e aggiunge rumore |
| microSD da 8 GB in su | meglio se di tipo "endurance" |
| (facoltativo) RTC DS3231 | ora esatta anche senza Internet; si collega allo stesso bus I2C (`install.sh --rtc`) |
| (facoltativo) cicalino, LED o relè | sul GPIO17 (pin 11), attivato dalle allerte forti |

## Collegamento GY-521 → Raspberry Pi

| GY-521 | Pin del Pi | Funzione |
|---|---|---|
| VCC | **1** | 3,3 V. Il modulo ha un regolatore proprio, ma col 3,3 V le linee I2C restano a 3,3 V come vuole il Pi |
| GND | **6** | massa |
| SCL | **5** | GPIO3 / SCL1 |
| SDA | **3** | GPIO2 / SDA1 |
| INT | 7 (facoltativo) | GPIO4: il software legge la FIFO a intervalli e non lo usa |
| XDA, XCL | — | **non collegare**: sono il bus ausiliario del chip |
| AD0 | — o GND | indirizzo 0x68 (a 3,3 V diventa 0x69) |

Il pin 1 è quello quadrato. Sui Pi Zero si trova all'estremità dell'header vicina alla microSD;
sugli altri modelli, all'angolo vicino ai fori di montaggio sul lato della microSD.

Verifica con `i2cdetect -y 1`, che deve mostrare `68`, oppure dalla dashboard con *Sensore →
Scansione bus I2C*.

> **Errore frequente:** con un connettore Dupont a più vie è facile infilarlo spostato di una
> posizione, per esempio VCC, GND e SCL collegati, SDA libero e un filo su XCL. Il sensore
> allora non risponde: controlla filo per filo con la serigrafia del modulo.

Il prototipo del progetto aveva proprio questo problema:
[foto del montaggio originale](../pre_assets/photo_2026-10-28_11-11-16.jpg) (Pi Zero, GY-521 e
cavi Dupont in un box).

## Montaggio meccanico

La qualità dei dati dipende più dal montaggio che dal software.

1. **Sensore rigido sul box.** Usa viti M2 con distanziali metallici o colla epossidica. La colla
   a caldo è elastica: smorza le alte frequenze e lascia "suonare" la scheda.
2. **Box rigido sul pavimento.** Scegli il piano terra o la cantina, su cemento o piastrelle, mai
   su legno o moquette. Se possibile fissa il box con un tassello o del biadesivo strutturale.
3. **Lontano dal rumore:** lavatrici, frigoriferi, caldaie, pompe, porte e corridoi.
4. **Cavi corti e fermati.** Un cavo che oscilla trasmette vibrazioni. Usa connettori Dupont ben
   fermi o, meglio, salda i fili.
5. **Temperatura stabile.** Il MEMS deriva con la temperatura: evita sole diretto e termosifoni.
6. **In bolla e orientato.** Allinea un lato del box al nord. Poi, nella dashboard, esegui
   *Sensore → Orientamento automatico* e *Calibra*, e indica in *Stazione* l'eventuale rotazione
   rispetto al nord.

## Wi-Fi con chiavetta USB (Pi Zero v1.3 e simili)

- Il Pi Zero ha una sola porta micro-USB dati (quella interna, "USB"): la chiavetta va lì, con
  un adattatore OTG.
- Dopo l'installazione, la dashboard (*Rete → Adattatore Wi-Fi*) mostra il driver, l'ID USB e se
  la modalità Access Point è supportata. Puoi verificarlo anche con `iw list`, cercando
  `* AP` sotto "Supported interface modes".
- Molte chiavette economiche (es. con chip RTL8188EUS) supportano la modalità AP solo con alcuni
  driver o versioni del kernel. Se non la supportano, il fallback non può partire e il Pi resta
  raggiungibile solo dalle reti note. Chip con buon supporto AP nei driver del kernel: RT5370
  (`rt2800usb`), MT7610U / MT7612U (`mt76`).

## Prestazioni sul Pi Zero

- La FIFO dell'MPU-6050 contiene 1,7 s di dati a 100 Hz. Il servizio di acquisizione gira con
  priorità alta e non fa nulla di lento nel ciclo principale.
- L'uso di CPU del servizio di acquisizione è visibile in *Statistiche → Sistema e telemetria*.
  A 200 Hz il carico è circa il doppio che a 100 Hz.
- Spettri, spettrogrammi, filtri di visualizzazione e rullo sono calcolati dal browser, non
  dal Pi.

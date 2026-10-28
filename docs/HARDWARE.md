# Hardware e cablaggio

## Componenti

| Pezzo | Note |
|---|---|
| Raspberry Pi Zero (v1.3, **non W**) | ARMv6, 512 MB di RAM. Serve Raspberry Pi OS **32 bit** |
| Dongle Wi-Fi USB TP-Link + adattatore OTG micro-USB | per l'Access Point di emergenza il driver deve supportare la modalità AP (verifica con `iw list`) |
| GY-521 (MPU-6050) | accelerometro e giroscopio a 3 assi, I2C, 16 bit; si usa solo l'accelerometro |
| Alimentatore 5 V, almeno 2,5 A | la sottotensione corrompe la SD e aggiunge rumore |
| (facoltativo) RTC DS3231 | ora esatta anche senza Internet; si collega allo stesso bus I2C (`install.sh --rtc`) |
| (facoltativo) cicalino o LED | sul GPIO17 (pin 11), attivato dalle allerte forti |

## Collegamento GY-521 → Raspberry Pi

| GY-521 | Pin del Pi | Funzione |
|---|---|---|
| VCC | **1** | 3,3 V (il modulo ha un suo regolatore, ma col 3,3 V gli ingressi I2C restano a livello logico) |
| GND | **6** | massa |
| SCL | **5** | GPIO3 / SCL1 |
| SDA | **3** | GPIO2 / SDA1 |
| INT | 7 (facoltativo) | GPIO4: il software legge la FIFO a intervalli e non lo usa |
| XDA, XCL | — | **non collegare**: sono il bus ausiliario del chip |
| AD0 | — o GND | indirizzo 0x68 (a 3,3 V diventa 0x69) |

Il pin 1 del Pi è quello quadrato, all'estremità dell'header vicina alla microSD.

> Nella foto del vecchio montaggio (`pre_assets/`) il connettore a 4 vie sul GY-521 sembra
> spostato di una posizione: VCC, GND e SCL coperti, **SDA libero**, e un filo singolo su
> **XCL**. Con questo cablaggio il sensore non può funzionare. Ricollega seguendo la tabella e
> verifica con `i2cdetect -y 1`, che deve mostrare `68`, oppure con *Sensore → Scansione bus I2C*
> nella dashboard.

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

## Note sul Pi Zero non-W

- Ha una sola porta micro-USB dati (quella interna, "USB"): il dongle Wi-Fi va lì, con un
  adattatore OTG.
- Dopo l'installazione, la dashboard (Rete → Adattatore Wi-Fi) mostra il driver e il supporto
  alla modalità AP. Molti TP-Link TL-WN725N usano il chip RTL8188EUS: con i driver recenti del
  kernel (`r8188eu` / `rtl8xxxu`) la modalità AP **dipende dalla versione**. Se non è supportata,
  il fallback non può partire e il Pi resta raggiungibile solo dalle reti note.
- L'MPU-6050 legge 1,7 s di dati dalla FIFO interna: i processi del sismografo girano con
  priorità alta, e il resto (grafici, spettri) è calcolato dal browser.

# solaredge-pvoutput

Docker-Container, der einen SolarEdge-Wechselrichter (z.B. SE10K) lokal per
**Modbus TCP** ausliest und die Werte alle 5 Minuten an **PVOutput**
(https://pvoutput.org) sendet. Keine Cloud-API, keine Rate-Limits, funktioniert
auch ohne Internetverbindung zu SolarEdge.

Gesendet werden:

- `v1` - Lifetime-Energieertrag in Wh (Zaehlerstand, `c1=1`) - PVOutput
  berechnet daraus selbst die genaue Tagesenergie
- `v2` - aktuelle AC-Leistung in W
- `v5` - Wechselrichter-Temperatur (optional, abschaltbar)
- `v6` - Netzspannung (optional, abschaltbar)

## 1. Modbus TCP am Wechselrichter aktivieren

Am SE10K-Display oder in der SetApp:

`Communication -> Modbus TCP -> Enable`

Danach den Wechselrichter neu starten. Der Wechselrichter muss aus dem
Docker-Netzwerk heraus per IP erreichbar sein (Port standardmaessig **1502**).

> Hinweis: Wenn der Wechselrichter gleichzeitig an das SolarEdge-Monitoring-
> Portal meldet, ist das kein Problem - Modbus TCP laeuft parallel dazu.

## 2. PVOutput vorbereiten

1. Account auf https://pvoutput.org anlegen, System unter "Add Output"
   registrieren.
2. API-Key erzeugen: https://pvoutput.org/account.jsp -> "API Settings" ->
   Access Key aktivieren.
3. Die System-Id findest du in der URL deines Systems bzw. unter
   "Settings" des jeweiligen Outputs.

## 3. Konfigurieren

```bash
cp .env.example .env
```

`.env` bearbeiten und mindestens setzen:

```
SOLAREDGE_HOST=192.168.1.50   # IP des Wechselrichters
PVOUTPUT_API_KEY=...
PVOUTPUT_SYSTEM_ID=...
```

## 4. Starten

```bash
docker compose up -d --build
docker compose logs -f
```

Ein erfolgreicher Durchlauf sieht in den Logs so aus:

```
INFO  Wechselrichter-Status=Producing  Leistung=3400W  Zaehlerstand=12345678Wh  Temp=41.2C  U=235.2V
```

Zum Testen ohne tatsaechlich Daten an PVOutput zu senden: `DRY_RUN=true` in
der `.env` setzen und neu starten - die Werte werden dann nur geloggt.

## Konfigurationsoptionen (`.env`)

| Variable | Standard | Beschreibung |
|---|---|---|
| `SOLAREDGE_HOST` | - (Pflicht) | IP/Hostname des Wechselrichters |
| `SOLAREDGE_PORT` | `1502` | Modbus-TCP-Port |
| `SOLAREDGE_UNIT_ID` | `1` | Modbus Unit/Slave-ID |
| `SOLAREDGE_TIMEOUT` | `10` | Timeout pro Leseversuch (Sek.) |
| `PVOUTPUT_API_KEY` | - (Pflicht) | PVOutput API-Key |
| `PVOUTPUT_SYSTEM_ID` | - (Pflicht) | PVOutput System-Id |
| `INTERVAL_SECONDS` | `300` | Sekunden zwischen Uploads (PVOutput-Minimum ohne Donation: 300) |
| `PVOUTPUT_INCLUDE_TEMPERATURE` | `true` | Temperatur mitsenden (`v5`) |
| `PVOUTPUT_INCLUDE_VOLTAGE` | `true` | Netzspannung mitsenden (`v6`) |
| `TZ` | `Europe/Berlin` | Zeitzone fuer Zeitstempel und Docker-Log-Zeiten |
| `LOG_LEVEL` | `INFO` | `DEBUG` fuer ausfuehrliche Modbus-Logs |
| `DRY_RUN` | `false` | Werte nur loggen, nichts an PVOutput senden |

## Troubleshooting

- **"Modbus-Verbindung zum Wechselrichter fehlgeschlagen"**: IP/Port prüfen,
  Modbus TCP am Wechselrichter aktiviert? Firewall zwischen Docker-Host und
  Wechselrichter offen? Manche Router isolieren IoT-/Gast-VLANs.
- **"Unvollstaendige Daten vom Wechselrichter"**: Wechselrichter antwortet,
  liefert aber unvollstaendige Register - meist ein Uebergangszustand direkt
  nach dem Einschalten. Wird im naechsten Zyklus automatisch erneut versucht.
- **"PVOutput-Upload fehlgeschlagen: HTTP 403"**: API-Key oder System-Id
  falsch, oder Rate-Limit erreicht (Uploads < 5 Min. Abstand ohne Donation).
- Nachts liefert der Wechselrichter meist Status "Off"/"Sleeping" und keine
  verwertbaren Werte mehr - der Container laeuft einfach weiter und meldet
  sich, sobald wieder Produktion erkannt wird.

## Eigenes Image bauen und in Gitea-Registry veroeffentlichen

Passend zu deinem bestehenden Setup (z.B. wie bei `go-e-solar-charger`) kannst
du das Image auch in deine Gitea-Registry pushen:

```bash
docker build -t gitea.wiederhol.de/twiederh/solaredge-pvoutput:latest .
docker push gitea.wiederhol.de/twiederh/solaredge-pvoutput:latest
```

und in der `docker-compose.yml` dann `image:` statt `build:` verwenden.

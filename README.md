# solaredge-pvoutput

Docker container that reads a SolarEdge inverter (e.g. SE10K) locally via
**Modbus TCP** and uploads the values to **PVOutput**
(https://pvoutput.org) every 5 minutes. No cloud API, no rate limits, and it
keeps working even without an internet connection to SolarEdge.

Values sent:

- `v1` - lifetime energy yield in Wh (meter reading, `c1=1`) - PVOutput
  calculates the accurate daily energy from this itself
- `v2` - current AC power in W
- `v5` - inverter temperature (optional, can be disabled)
- `v6` - grid voltage (optional, can be disabled)

## 1. Enable Modbus TCP on the inverter

On the SE10K display or in the SetApp:

`Communication -> Modbus TCP -> Enable`

Then restart the inverter. The inverter must be reachable by IP from the
Docker network (default port **1502**).

> Note: If the inverter also reports to the SolarEdge monitoring portal at
> the same time, that's fine - Modbus TCP runs in parallel to that.

## 2. Set up PVOutput

1. Create an account at https://pvoutput.org, register your system under
   "Add Output".
2. Generate an API key: https://pvoutput.org/account.jsp -> "API Settings" ->
   enable Access Key.
3. You'll find the System Id in your system's URL, or under "Settings" for
   that output.

## 3. Configure

```bash
cp .env.example .env
```

Edit `.env` and set at least:

```
SOLAREDGE_HOST=192.168.1.50   # inverter IP address
PVOUTPUT_API_KEY=...
PVOUTPUT_SYSTEM_ID=...
```

## 4. Run

```bash
docker compose up -d --build
docker compose logs -f
```

A successful cycle looks like this in the logs:

```
INFO  Wechselrichter-Status=Producing  Leistung=3400W  Zaehlerstand=12345678Wh  Temp=41.2C  U=235.2V
```

To test without actually sending data to PVOutput: set `DRY_RUN=true` in
`.env` and restart - values will only be logged.

## Configuration options (`.env`)

| Variable | Default | Description |
|---|---|---|
| `SOLAREDGE_HOST` | - (required) | IP/hostname of the inverter |
| `SOLAREDGE_PORT` | `1502` | Modbus TCP port |
| `SOLAREDGE_UNIT_ID` | `1` | Modbus unit/slave ID |
| `SOLAREDGE_TIMEOUT` | `10` | Timeout per read attempt (seconds) |
| `PVOUTPUT_API_KEY` | - (required) | PVOutput API key |
| `PVOUTPUT_SYSTEM_ID` | - (required) | PVOutput system ID |
| `INTERVAL_SECONDS` | `300` | Seconds between uploads (PVOutput minimum without donation: 300) |
| `PVOUTPUT_INCLUDE_TEMPERATURE` | `true` | Include temperature (`v5`) |
| `PVOUTPUT_INCLUDE_VOLTAGE` | `true` | Include grid voltage (`v6`) |
| `TZ` | `Europe/Berlin` | Timezone for timestamps and Docker log times |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose Modbus logs |
| `DRY_RUN` | `false` | Only log values, don't send anything to PVOutput |

## Troubleshooting

- **"Modbus connection to the inverter failed"**: Check IP/port. Is Modbus
  TCP enabled on the inverter? Is there a firewall between the Docker host
  and the inverter? Some routers isolate IoT/guest VLANs.
- **"Incomplete data from the inverter"**: The inverter responds but returns
  incomplete registers - usually a transient state right after power-on.
  Automatically retried on the next cycle.
- **"PVOutput upload failed: HTTP 403"**: Wrong API key or system ID, or
  rate limit reached (uploads faster than 5 minutes apart without a
  donation account).
- At night the inverter usually reports status "Off"/"Sleeping" with no
  usable values - the container just keeps running and resumes reporting
  once production is detected again.

## Building your own image and publishing it to your Gitea registry

Matching your existing setup (e.g. as with `go-e-solar-charger`), you can
also push the image to your Gitea registry:

```bash
docker build -t gitea.wiederhol.de/twiederh/solaredge-pvoutput:latest .
docker push gitea.wiederhol.de/twiederh/solaredge-pvoutput:latest
```

and then use `image:` instead of `build:` in `docker-compose.yml`.

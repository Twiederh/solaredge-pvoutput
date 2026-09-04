# solaredge-pvoutput

Docker container that reads power and energy data from a SolarEdge inverter
(e.g. SE10K) and uploads it to **PVOutput** (https://pvoutput.org) every 5
minutes.

Two data sources are supported (`DATA_SOURCE`):

- **`modbus`** (default) - connects directly to the inverter via local
  **Modbus TCP**. No cloud API, no rate limits, keeps working even without
  an internet connection to SolarEdge.
- **`homeassistant`** - reads existing sensor entities from Home Assistant
  via its REST API instead of talking to the inverter directly. Use this if
  Home Assistant already polls the inverter (e.g. via its own Modbus
  integration) - see [Choosing a data source](#choosing-a-data-source)
  below for why this matters.

Values sent to PVOutput:

- `v1` - lifetime energy yield in Wh (meter reading, `c1=1`) - PVOutput
  calculates the accurate daily energy from this itself
- `v2` - current AC power in W
- `v5` - inverter temperature (optional, can be disabled)
- `v6` - grid voltage (optional, can be disabled)

## Choosing a data source

Most SolarEdge inverters only accept **one active Modbus TCP connection at
a time**. If Home Assistant (or anything else) is already polling the
inverter via Modbus, running this container with `DATA_SOURCE=modbus` at
the same time means two clients competing for that single connection -
this can cause intermittent read failures on either side, or disrupt HA's
own polling.

If that's your situation, set `DATA_SOURCE=homeassistant` instead: the
container then reads the values HA has already fetched from its own
sensor entities via the HA REST API, so there's only ever one Modbus
client (Home Assistant).

## Option A: Modbus TCP (direct)

### 1. Enable Modbus TCP on the inverter

On the SE10K display or in the SetApp:

`Communication -> Modbus TCP -> Enable`

Then restart the inverter. The inverter must be reachable by IP from the
Docker network (default port **1502**).

> Note: If the inverter also reports to the SolarEdge monitoring portal at
> the same time, that's fine - Modbus TCP runs in parallel to that.

### 2. Configure

```
DATA_SOURCE=modbus
SOLAREDGE_HOST=192.168.1.50   # inverter IP address
```

## Option B: Home Assistant

### 1. Create a Long-Lived Access Token

In Home Assistant: your profile (bottom left) -> scroll down to
"Long-Lived Access Tokens" -> create a token.

### 2. Find your entity IDs

Settings -> Devices & Services -> Entities (or Developer Tools -> States),
and note the entity IDs of your SolarEdge power, lifetime energy,
temperature and voltage sensors. Units are auto-detected from each
entity's `unit_of_measurement` and converted as needed (kW -> W,
kWh -> Wh, °F -> °C).

### 3. Configure

```
DATA_SOURCE=homeassistant
HOMEASSISTANT_URL=http://homeassistant.local:8123
HOMEASSISTANT_TOKEN=...
HA_ENTITY_POWER=sensor.solaredge_ac_power
HA_ENTITY_ENERGY_TOTAL=sensor.solaredge_lifetime_energy
HA_ENTITY_TEMPERATURE=sensor.solaredge_temperature
HA_ENTITY_VOLTAGE=sensor.solaredge_voltage_l1
```

`HA_ENTITY_ENERGY_TOTAL` is required; the other three are optional (leave
blank to omit that value from the PVOutput upload).

## Set up PVOutput

1. Create an account at https://pvoutput.org, register your system under
   "Add Output".
2. Generate an API key: https://pvoutput.org/account.jsp -> "API Settings" ->
   enable Access Key.
3. You'll find the System Id in your system's URL, or under "Settings" for
   that output.

## Run with Docker Compose (CLI)

```bash
cp .env.example .env
```

Edit `.env`, set your data source (see above) plus:

```
PVOUTPUT_API_KEY=...
PVOUTPUT_SYSTEM_ID=...
```

Then:

```bash
docker compose up -d --build
docker compose logs -f
```

`docker-compose.yml` reads its values via `${VAR}` interpolation, which
`docker compose` automatically fills in from the `.env` file in the same
directory - no extra steps needed.

## Run via Portainer (Git repository stack)

If you deploy this repo as a Portainer "Git repository" stack, there is no
`.env` file (it's intentionally excluded from the repo via `.gitignore`,
since it would otherwise contain your PVOutput key and Home Assistant
token). Instead, add the same variables under **Environment variables** in
the stack's configuration in Portainer - `docker-compose.yml` picks them up
the same way it would from a local `.env` file. At minimum, set
`PVOUTPUT_API_KEY` and `PVOUTPUT_SYSTEM_ID` there, plus whichever data
source variables from `.env.example` apply to your setup.

## Example log output

German by default; set `LOG_LANGUAGE=en` for English log messages.

```
INFO  Quelle=modbus  Status=Producing  Leistung=3400W  Zaehlerstand=12345678Wh  Temp=41.2C  U=235.2V
```

With `LOG_LANGUAGE=en`:

```
INFO  Source=modbus  Status=Producing  Power=3400W  Meter reading=12345678Wh  Temp=41.2C  U=235.2V
```

To test without actually sending data to PVOutput: set `DRY_RUN=true` and
restart - values will only be logged.

By default, no update is sent to PVOutput while the inverter reports status
**Sleeping** (`DATA_SOURCE=modbus` only - typically at night, no point
uploading 0 W). Configure which statuses get skipped via `SKIP_STATUSES`
(comma-separated, e.g. `SKIP_STATUSES=Sleeping,Off`); set it to an empty
value to always upload regardless of status.

## Configuration options (`.env`)

| Variable | Default | Description |
|---|---|---|
| `DATA_SOURCE` | `modbus` | `modbus` (direct) or `homeassistant` |
| **Modbus** (`DATA_SOURCE=modbus`) | | |
| `SOLAREDGE_HOST` | - (required) | IP/hostname of the inverter |
| `SOLAREDGE_PORT` | `1502` | Modbus TCP port |
| `SOLAREDGE_UNIT_ID` | `1` | Modbus unit/slave ID |
| `SOLAREDGE_TIMEOUT` | `10` | Timeout per read attempt (seconds) |
| **Home Assistant** (`DATA_SOURCE=homeassistant`) | | |
| `HOMEASSISTANT_URL` | - (required) | Base URL of your HA instance |
| `HOMEASSISTANT_TOKEN` | - (required) | Long-Lived Access Token |
| `HOMEASSISTANT_TIMEOUT` | `10` | Timeout per HA API request (seconds) |
| `HA_ENTITY_POWER` | - (optional) | Entity ID for current AC power |
| `HA_ENTITY_ENERGY_TOTAL` | - (required) | Entity ID for lifetime energy |
| `HA_ENTITY_TEMPERATURE` | - (optional) | Entity ID for inverter temperature |
| `HA_ENTITY_VOLTAGE` | - (optional) | Entity ID for grid voltage |
| **PVOutput** | | |
| `PVOUTPUT_API_KEY` | - (required) | PVOutput API key |
| `PVOUTPUT_SYSTEM_ID` | - (required) | PVOutput system ID |
| **General** | | |
| `INTERVAL_SECONDS` | `300` | Seconds between uploads (PVOutput minimum without donation: 300) |
| `PVOUTPUT_INCLUDE_TEMPERATURE` | `true` | Include temperature (`v5`) |
| `PVOUTPUT_INCLUDE_VOLTAGE` | `true` | Include grid voltage (`v6`) |
| `SKIP_STATUSES` | `Sleeping` | Comma-separated inverter statuses (Modbus only) to skip uploading for |
| `TZ` | `Europe/Berlin` | Timezone for timestamps and Docker log times |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose Modbus logs |
| `LOG_LANGUAGE` | `de` | Language of log messages: `de` (German) or `en` (English) |
| `DRY_RUN` | `false` | Only log values, don't send anything to PVOutput |

## Troubleshooting

- **"Modbus connection to the inverter failed"**: Check IP/port. Is Modbus
  TCP enabled on the inverter? Is there a firewall between the Docker host
  and the inverter? Some routers isolate IoT/guest VLANs. If something else
  (e.g. Home Assistant) already holds a Modbus connection to the inverter,
  consider switching to `DATA_SOURCE=homeassistant` instead.
- **Home Assistant entity errors** (`not found` / `unavailable` /
  `did not return a numeric value`): double-check the entity ID under
  Developer Tools -> States in Home Assistant, and that the token has
  access to it.
- **"Incomplete data from the data source"**: energy reading missing -
  usually a transient state right after power-on. Automatically retried on
  the next cycle.
- **"PVOutput upload failed: HTTP 403"**: Wrong API key or system ID, or
  rate limit reached (uploads faster than 5 minutes apart without a
  donation account).
- **Portainer: "env file ... not found"**: See
  [Run via Portainer](#run-via-portainer-git-repository-stack) above - use
  the stack's Environment variables instead of a `.env` file.
- At night the inverter usually reports status "Off"/"Sleeping" with no
  usable values - by default, uploads are skipped entirely while status is
  "Sleeping" (see `SKIP_STATUSES` above); the container just keeps running
  and resumes uploading once production is detected again.

## Building your own image and publishing it to your Gitea registry

Matching your existing setup (e.g. as with `go-e-solar-charger`), you can
also push the image to your Gitea registry:

```bash
docker build -t gitea.wiederhol.de/twiederh/solaredge-pvoutput:latest .
docker push gitea.wiederhol.de/twiederh/solaredge-pvoutput:latest
```

and then use `image:` instead of `build:` in `docker-compose.yml`.

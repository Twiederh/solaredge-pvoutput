#!/usr/bin/env python3
"""
solaredge-pvoutput
-------------------
Reads power and energy data from a SolarEdge inverter (e.g. SE10K) and
periodically uploads it to PVOutput (https://pvoutput.org) via the
addstatus.jsp API.

Two data sources are supported (DATA_SOURCE env var):

- "modbus" (default): connects directly to the inverter via Modbus TCP.
  Requires "Modbus TCP" to be enabled on the inverter (SetApp / display
  menu: Communication -> Modbus TCP -> Enable). Note: most SolarEdge
  inverters only accept one active Modbus TCP connection at a time - if
  something else (e.g. Home Assistant's Modbus integration) is already
  polling the inverter, use "homeassistant" instead.
- "homeassistant": reads the values from existing Home Assistant sensor
  entities via the HA REST API instead of talking to the inverter
  directly. Use this if Home Assistant already has a Modbus (or any
  other) integration polling the inverter, to avoid two clients
  competing for the same connection.

Log messages can be switched between German and English via the
LOG_LANGUAGE environment variable (values: "de" or "en", default "de").
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from pymodbus.exceptions import ConnectionException, ModbusIOException
from solaredge_modbus import Inverter

# --------------------------------------------------------------------------
# Konfiguration (ueber Umgebungsvariablen, siehe .env.example)
# --------------------------------------------------------------------------

def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return int(val)


class Config:
    DATA_SOURCE = os.environ.get("DATA_SOURCE", "modbus").strip().lower()

    SOLAREDGE_HOST = os.environ.get("SOLAREDGE_HOST", "").strip()
    SOLAREDGE_PORT = _env_int("SOLAREDGE_PORT", 1502)
    SOLAREDGE_UNIT_ID = _env_int("SOLAREDGE_UNIT_ID", 1)
    SOLAREDGE_TIMEOUT = _env_int("SOLAREDGE_TIMEOUT", 10)

    HOMEASSISTANT_URL = os.environ.get("HOMEASSISTANT_URL", "").strip().rstrip("/")
    HOMEASSISTANT_TOKEN = os.environ.get("HOMEASSISTANT_TOKEN", "").strip()
    HOMEASSISTANT_TIMEOUT = _env_int("HOMEASSISTANT_TIMEOUT", 10)
    HA_ENTITY_POWER = os.environ.get("HA_ENTITY_POWER", "").strip()
    HA_ENTITY_ENERGY_TOTAL = os.environ.get("HA_ENTITY_ENERGY_TOTAL", "").strip()
    HA_ENTITY_TEMPERATURE = os.environ.get("HA_ENTITY_TEMPERATURE", "").strip()
    HA_ENTITY_VOLTAGE = os.environ.get("HA_ENTITY_VOLTAGE", "").strip()

    PVOUTPUT_API_KEY = os.environ.get("PVOUTPUT_API_KEY", "").strip()
    PVOUTPUT_SYSTEM_ID = os.environ.get("PVOUTPUT_SYSTEM_ID", "").strip()
    PVOUTPUT_URL = os.environ.get(
        "PVOUTPUT_URL", "https://pvoutput.org/service/r2/addstatus.jsp"
    )

    INTERVAL_SECONDS = _env_int("INTERVAL_SECONDS", 300)  # 5 Minuten
    INCLUDE_TEMPERATURE = _env_bool("PVOUTPUT_INCLUDE_TEMPERATURE", True)
    INCLUDE_VOLTAGE = _env_bool("PVOUTPUT_INCLUDE_VOLTAGE", True)

    TIMEZONE = os.environ.get("TZ", "Europe/Berlin")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
    LOG_LANGUAGE = os.environ.get("LOG_LANGUAGE", "de").strip().lower()
    DRY_RUN = _env_bool("DRY_RUN", False)


class DataSourceError(Exception):
    """Einheitlicher Fehler fuer beide Datenquellen (Modbus & Home Assistant)."""


# --------------------------------------------------------------------------
# Log-Texte / log strings (Deutsch + Englisch, siehe LOG_LANGUAGE)
# --------------------------------------------------------------------------

MESSAGES = {
    "de": {
        "invalid_interval": (
            "INTERVAL_SECONDS=%s ist sehr niedrig - PVOutput erlaubt ohne "
            "Donation-Account maximal ein Update alle 5 Minuten."
        ),
        "missing_env": "Fehlende Pflicht-Umgebungsvariablen: %s. Bitte .env pruefen.",
        "invalid_data_source": (
            "DATA_SOURCE=%r ist ungueltig - erlaubt sind 'modbus' oder 'homeassistant'."
        ),
        "unknown_tz": "Unbekannte Zeitzone %r, verwende UTC",
        "unknown_log_language": (
            "Unbekannte LOG_LANGUAGE=%r, verwende 'de' (gueltig: 'de', 'en')"
        ),
        "modbus_read_empty": "Keine Daten vom Wechselrichter erhalten (leere Antwort)",
        "energy_missing": "Der Energie-Zaehlerstand konnte nicht gelesen werden",
        "status_unknown": "unbekannt",
        "status_log": "Quelle=%s  Status=%s  Leistung=%sW  Zaehlerstand=%.0fWh%s%s",
        "temp_suffix": "  Temp=%sC",
        "voltage_suffix": "  U=%sV",
        "dry_run": "DRY_RUN aktiv - wuerde an PVOutput senden: %s",
        "pvoutput_http_error": "PVOutput antwortete mit HTTP %s: %s",
        "pvoutput_ok": "PVOutput OK: %s",
        "signal_received": "Signal %s empfangen, beende nach aktuellem Zyklus...",
        "startup_modbus": (
            "Starte solaredge-pvoutput: Quelle=Modbus TCP %s:%s (Unit %s), "
            "Intervall=%ss, TZ=%s"
        ),
        "startup_ha": (
            "Starte solaredge-pvoutput: Quelle=Home Assistant %s, "
            "Intervall=%ss, TZ=%s"
        ),
        "modbus_conn_failed": "Modbus-Verbindung zum Wechselrichter fehlgeschlagen: %s",
        "ha_entity_not_found": "Home-Assistant-Entity %r nicht gefunden (HTTP 404)",
        "ha_http_error": "Home-Assistant-Anfrage fuer %r fehlgeschlagen: HTTP %s",
        "ha_entity_unavailable": "Home-Assistant-Entity %r ist aktuell 'unavailable'/'unknown'",
        "ha_entity_not_numeric": "Home-Assistant-Entity %r liefert keinen Zahlenwert (%r)",
        "ha_request_failed": "Verbindung zu Home Assistant fehlgeschlagen: %s",
        "incomplete_data": "Unvollstaendige Daten von der Datenquelle: %s",
        "pvoutput_upload_failed": "PVOutput-Upload fehlgeschlagen: %s",
        "unexpected_error": "Unerwarteter Fehler im Update-Zyklus",
        "shutdown": "Beendet.",
    },
    "en": {
        "invalid_interval": (
            "INTERVAL_SECONDS=%s is very low - PVOutput allows a minimum of "
            "one update every 5 minutes without a donation account."
        ),
        "missing_env": "Missing required environment variables: %s. Please check your .env file.",
        "invalid_data_source": (
            "DATA_SOURCE=%r is invalid - allowed values are 'modbus' or 'homeassistant'."
        ),
        "unknown_tz": "Unknown timezone %r, using UTC",
        "unknown_log_language": (
            "Unknown LOG_LANGUAGE=%r, using 'de' (valid: 'de', 'en')"
        ),
        "modbus_read_empty": "No data received from the inverter (empty response)",
        "energy_missing": "The energy meter reading could not be read",
        "status_unknown": "unknown",
        "status_log": "Source=%s  Status=%s  Power=%sW  Meter reading=%.0fWh%s%s",
        "temp_suffix": "  Temp=%sC",
        "voltage_suffix": "  U=%sV",
        "dry_run": "DRY_RUN active - would send to PVOutput: %s",
        "pvoutput_http_error": "PVOutput responded with HTTP %s: %s",
        "pvoutput_ok": "PVOutput OK: %s",
        "signal_received": "Received signal %s, shutting down after current cycle...",
        "startup_modbus": (
            "Starting solaredge-pvoutput: Source=Modbus TCP %s:%s (Unit %s), "
            "Interval=%ss, TZ=%s"
        ),
        "startup_ha": (
            "Starting solaredge-pvoutput: Source=Home Assistant %s, "
            "Interval=%ss, TZ=%s"
        ),
        "modbus_conn_failed": "Modbus connection to the inverter failed: %s",
        "ha_entity_not_found": "Home Assistant entity %r not found (HTTP 404)",
        "ha_http_error": "Home Assistant request for %r failed: HTTP %s",
        "ha_entity_unavailable": "Home Assistant entity %r is currently 'unavailable'/'unknown'",
        "ha_entity_not_numeric": "Home Assistant entity %r did not return a numeric value (%r)",
        "ha_request_failed": "Connection to Home Assistant failed: %s",
        "incomplete_data": "Incomplete data from the data source: %s",
        "pvoutput_upload_failed": "PVOutput upload failed: %s",
        "unexpected_error": "Unexpected error during update cycle",
        "shutdown": "Stopped.",
    },
}

_raw_language = Config.LOG_LANGUAGE
_log_language = _raw_language if _raw_language in MESSAGES else "de"
MSG = MESSAGES[_log_language]

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(message)s",
)
log = logging.getLogger("solaredge-pvoutput")

if _raw_language not in MESSAGES:
    log.warning(MSG["unknown_log_language"], _raw_language)

# pymodbus logs every single connection attempt (including internal
# retries) as ERROR - expected behaviour when the inverter is briefly
# unreachable, and it would flood the logs. We emit our own compact warning
# per cycle instead (see below) and dampen pymodbus unless LOG_LEVEL=DEBUG.
if Config.LOG_LEVEL != "DEBUG":
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)

try:
    TZ = ZoneInfo(Config.TIMEZONE)
except Exception:
    log.warning(MSG["unknown_tz"], Config.TIMEZONE)
    TZ = ZoneInfo("UTC")

INVERTER_STATUS_LABELS = {
    1: "Off",
    2: "Sleeping",
    3: "Starting",
    4: "Producing",
    5: "Producing (Throttled)",
    6: "Shutting Down",
    7: "Fault",
    8: "Standby",
}


def validate_config() -> None:
    if Config.DATA_SOURCE not in ("modbus", "homeassistant"):
        log.error(MSG["invalid_data_source"], Config.DATA_SOURCE)
        sys.exit(1)

    missing = []
    if not Config.PVOUTPUT_API_KEY:
        missing.append("PVOUTPUT_API_KEY")
    if not Config.PVOUTPUT_SYSTEM_ID:
        missing.append("PVOUTPUT_SYSTEM_ID")

    if Config.DATA_SOURCE == "modbus":
        if not Config.SOLAREDGE_HOST:
            missing.append("SOLAREDGE_HOST")
    else:  # homeassistant
        if not Config.HOMEASSISTANT_URL:
            missing.append("HOMEASSISTANT_URL")
        if not Config.HOMEASSISTANT_TOKEN:
            missing.append("HOMEASSISTANT_TOKEN")
        if not Config.HA_ENTITY_ENERGY_TOTAL:
            missing.append("HA_ENTITY_ENERGY_TOTAL")

    if missing:
        log.error(MSG["missing_env"], ", ".join(missing))
        sys.exit(1)
    if Config.INTERVAL_SECONDS < 60:
        log.warning(MSG["invalid_interval"], Config.INTERVAL_SECONDS)


# --------------------------------------------------------------------------
# Datenquelle 1: Modbus TCP direkt am Wechselrichter
# --------------------------------------------------------------------------

def _modbus_scaled(values: dict, key: str) -> float:
    """Wendet den SunSpec-Skalierungsfaktor auf einen rohen Registerwert an."""
    raw = values.get(key)
    scale = values.get(f"{key}_scale")
    if raw is None:
        return None
    if not scale:
        return float(raw)
    return float(raw) * (10 ** scale)


def read_values_modbus() -> dict:
    """Baut eine frische Modbus-Verbindung auf, liest alle Register und
    schliesst die Verbindung wieder."""
    inverter = Inverter(
        host=Config.SOLAREDGE_HOST,
        port=Config.SOLAREDGE_PORT,
        timeout=Config.SOLAREDGE_TIMEOUT,
        unit=Config.SOLAREDGE_UNIT_ID,
    )
    try:
        values = inverter.read_all()
    except (ConnectionException, ModbusIOException, OSError) as exc:
        raise DataSourceError(MSG["modbus_conn_failed"] % exc) from exc
    finally:
        try:
            inverter.disconnect()
        except Exception:
            pass

    if not values:
        raise DataSourceError(MSG["modbus_read_empty"])

    power_w = _modbus_scaled(values, "power_ac")
    energy_wh = _modbus_scaled(values, "energy_total")
    temperature_c = (
        _modbus_scaled(values, "temperature") if Config.INCLUDE_TEMPERATURE else None
    )

    voltage_v = None
    if Config.INCLUDE_VOLTAGE:
        for key in ("l1_voltage", "l1n_voltage"):
            v = _modbus_scaled(values, key)
            if v:
                voltage_v = v
                break

    status_raw = values.get("status")
    status_label = INVERTER_STATUS_LABELS.get(status_raw, f"unknown ({status_raw})")

    return {
        "power_w": power_w,
        "energy_wh": energy_wh,
        "temperature_c": temperature_c,
        "voltage_v": voltage_v,
        "status_label": status_label,
    }


# --------------------------------------------------------------------------
# Datenquelle 2: Home Assistant (liest bestehende Sensor-Entities aus)
# --------------------------------------------------------------------------

def _ha_get_state(entity_id: str) -> tuple:
    """Liest eine einzelne Entity aus der Home-Assistant-REST-API.
    Gibt (Wert als float, unit_of_measurement) zurueck."""
    url = f"{Config.HOMEASSISTANT_URL}/api/states/{entity_id}"
    headers = {
        "Authorization": f"Bearer {Config.HOMEASSISTANT_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=Config.HOMEASSISTANT_TIMEOUT)
    except requests.RequestException as exc:
        raise DataSourceError(MSG["ha_request_failed"] % exc) from exc

    if resp.status_code == 404:
        raise DataSourceError(MSG["ha_entity_not_found"] % entity_id)
    if resp.status_code != 200:
        raise DataSourceError(MSG["ha_http_error"] % (entity_id, resp.status_code))

    data = resp.json()
    state = data.get("state")
    if state in (None, "unknown", "unavailable"):
        raise DataSourceError(MSG["ha_entity_unavailable"] % entity_id)
    try:
        value = float(state)
    except (TypeError, ValueError) as exc:
        raise DataSourceError(MSG["ha_entity_not_numeric"] % (entity_id, state)) from exc

    unit = (data.get("attributes") or {}).get("unit_of_measurement", "") or ""
    return value, unit


def _ha_to_watts(value: float, unit: str) -> float:
    u = unit.strip().lower()
    if u == "kw":
        return value * 1_000
    if u == "mw":
        return value * 1_000_000
    return value  # bereits W, oder keine Einheit angegeben


def _ha_to_watthours(value: float, unit: str) -> float:
    u = unit.strip().lower()
    if u == "kwh":
        return value * 1_000
    if u == "mwh":
        return value * 1_000_000
    return value  # bereits Wh


def _ha_to_celsius(value: float, unit: str) -> float:
    u = unit.strip().lower()
    if u in ("°f", "f"):
        return (value - 32) * 5 / 9
    return value  # bereits Grad Celsius, oder keine Einheit angegeben


def read_values_homeassistant() -> dict:
    power_w = None
    if Config.HA_ENTITY_POWER:
        value, unit = _ha_get_state(Config.HA_ENTITY_POWER)
        power_w = _ha_to_watts(value, unit)

    energy_value, energy_unit = _ha_get_state(Config.HA_ENTITY_ENERGY_TOTAL)
    energy_wh = _ha_to_watthours(energy_value, energy_unit)

    temperature_c = None
    if Config.INCLUDE_TEMPERATURE and Config.HA_ENTITY_TEMPERATURE:
        value, unit = _ha_get_state(Config.HA_ENTITY_TEMPERATURE)
        temperature_c = _ha_to_celsius(value, unit)

    voltage_v = None
    if Config.INCLUDE_VOLTAGE and Config.HA_ENTITY_VOLTAGE:
        value, _unit = _ha_get_state(Config.HA_ENTITY_VOLTAGE)
        voltage_v = value

    return {
        "power_w": power_w,
        "energy_wh": energy_wh,
        "temperature_c": temperature_c,
        "voltage_v": voltage_v,
        "status_label": None,
    }


def read_values() -> dict:
    if Config.DATA_SOURCE == "homeassistant":
        return read_values_homeassistant()
    return read_values_modbus()


# --------------------------------------------------------------------------
# PVOutput
# --------------------------------------------------------------------------

def build_pvoutput_payload(values: dict) -> dict:
    power_w = values.get("power_w")
    energy_wh = values.get("energy_wh")
    temperature_c = values.get("temperature_c")
    voltage_v = values.get("voltage_v")
    status_label = values.get("status_label") or MSG["status_unknown"]

    if energy_wh is None:
        raise ValueError(MSG["energy_missing"])

    # Negative Momentanleistung (z.B. minimaler Nachtverbrauch des
    # Wechselrichters) ist fuer PVOutput nicht sinnvoll -> auf 0 clampen.
    if power_w is not None and power_w < 0:
        power_w = 0

    now = datetime.now(TZ)
    payload = {
        "d": now.strftime("%Y%m%d"),
        "t": now.strftime("%H:%M"),
        "v1": int(round(energy_wh)),  # Lifetime-Energie in Wh
        "c1": 1,  # v1 ist ein Zaehlerstand -> PVOutput berechnet die Differenz
    }
    if power_w is not None:
        payload["v2"] = int(round(power_w))
    if temperature_c:
        payload["v5"] = round(temperature_c, 1)
    if voltage_v:
        payload["v6"] = round(voltage_v, 1)

    log.info(
        MSG["status_log"],
        Config.DATA_SOURCE,
        status_label,
        payload.get("v2", "?"),
        energy_wh,
        (MSG["temp_suffix"] % payload["v5"]) if "v5" in payload else "",
        (MSG["voltage_suffix"] % payload["v6"]) if "v6" in payload else "",
    )
    return payload


def send_to_pvoutput(payload: dict) -> None:
    if Config.DRY_RUN:
        log.info(MSG["dry_run"], payload)
        return

    headers = {
        "X-Pvoutput-Apikey": Config.PVOUTPUT_API_KEY,
        "X-Pvoutput-SystemId": Config.PVOUTPUT_SYSTEM_ID,
    }
    resp = requests.post(
        Config.PVOUTPUT_URL, headers=headers, data=payload, timeout=15
    )
    if resp.status_code != 200:
        raise RuntimeError(MSG["pvoutput_http_error"] % (resp.status_code, resp.text.strip()))
    log.debug(MSG["pvoutput_ok"], resp.text.strip())


class GracefulShutdown:
    stop = False

    def __init__(self):
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum, frame):
        log.info(MSG["signal_received"], signum)
        self.stop = True


def main() -> None:
    validate_config()
    if Config.DATA_SOURCE == "homeassistant":
        log.info(
            MSG["startup_ha"],
            Config.HOMEASSISTANT_URL,
            Config.INTERVAL_SECONDS,
            Config.TIMEZONE,
        )
    else:
        log.info(
            MSG["startup_modbus"],
            Config.SOLAREDGE_HOST,
            Config.SOLAREDGE_PORT,
            Config.SOLAREDGE_UNIT_ID,
            Config.INTERVAL_SECONDS,
            Config.TIMEZONE,
        )
    shutdown = GracefulShutdown()

    while not shutdown.stop:
        cycle_start = time.monotonic()
        try:
            values = read_values()
            payload = build_pvoutput_payload(values)
            send_to_pvoutput(payload)
        except DataSourceError as exc:
            log.warning(str(exc))
        except ValueError as exc:
            log.warning(MSG["incomplete_data"], exc)
        except RuntimeError as exc:
            log.error(MSG["pvoutput_upload_failed"], exc)
        except Exception:
            log.exception(MSG["unexpected_error"])

        elapsed = time.monotonic() - cycle_start
        remaining = max(Config.INTERVAL_SECONDS - elapsed, 1)
        # In kurzen Schlafintervallen warten, damit SIGTERM zuegig reagiert
        slept = 0.0
        while slept < remaining and not shutdown.stop:
            step = min(1.0, remaining - slept)
            time.sleep(step)
            slept += step

    log.info(MSG["shutdown"])


if __name__ == "__main__":
    main()

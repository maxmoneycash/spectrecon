"""RF gadget / audit-rig fingerprints.

Passive identification of well-known radios that show up in WiGLE CSVs and
BLE scans: Flipper Zero, Hak5 Pineapple, ESP32 Marauder, Pwnagotchi, mesh
nodes, etc. Signatures are public advertised names, setup SSIDs, BLE service
UUIDs, and distinctive OUIs — not exploits, not pairing, not payloads.

Chip-only hits (Espressif / Raspberry Pi OUI with a boring SSID) are tagged
as family ``chip`` so a smart plug does not look like a pentest rig.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bluetooth SIG 16-bit UUID expanded into the base UUID.
_BASE = "-0000-1000-8000-00805F9B34FB"


def _u16(n: int) -> str:
    return f"0000{n:04X}{_BASE}"


def _norm_uuid(u: str) -> str:
    return u.replace("-", "").upper()


def _norm_oui(bssid: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", bssid)[:6].upper()


@dataclass(frozen=True)
class Fingerprint:
    id: str
    label: str
    family: str  # gadget | rig | mesh | chip
    ssid: tuple[str, ...] = ()
    name: tuple[str, ...] = ()
    uuids: tuple[str, ...] = ()
    oui: tuple[str, ...] = ()
    auth: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hit:
    id: str
    label: str
    family: str
    via: str


# Order is first-match-wins. Specific gadgets before generic chips.
CATALOG: tuple[Fingerprint, ...] = (
    Fingerprint(
        id="flipper-zero", label="Flipper Zero", family="gadget",
        ssid=(r"flipper",),
        name=(r"flipper",),
        uuids=(
            "8FE5B3D5-2E7F-4A98-2A48-7ACC60FE0000",  # Flipper BLE serial
            _u16(0x3081), _u16(0x3082), _u16(0x3083),  # black / white / clear
        ),
        oui=("0CFA22", "80E126", "80E127"),
        auth=(r"\[GADGET:flipper",),
    ),
    Fingerprint(
        id="wifi-pineapple", label="Hak5 WiFi Pineapple", family="gadget",
        ssid=(r"^pineapple_[0-9a-f]{4}$", r"pineap", r"pineapple"),
        name=(r"pineapple",),
        auth=(r"\[GADGET:pineapple",),
    ),
    Fingerprint(
        id="omg-cable", label="O.MG cable / plug", family="gadget",
        ssid=(r"^omg[-_ ]", r"omg cable"),
        name=(r"^omg[-_ ]", r"omg cable"),
        auth=(r"\[GADGET:omg",),
    ),
    Fingerprint(
        id="pwnagotchi", label="Pwnagotchi", family="gadget",
        ssid=(r"pwnagotchi",),
        name=(r"pwnagotchi",),
        auth=(r"\[GADGET:pwnagotchi",),
    ),
    Fingerprint(
        id="wifi-deauther", label="ESP8266/ESP32 Deauther", family="gadget",
        ssid=(r"deauther", r"^pwned$", r"dstike", r"spacehuhn"),
        name=(r"deauther", r"dstike", r"spacehuhn"),
        auth=(r"\[GADGET:deauther",),
    ),
    Fingerprint(
        id="esp32-marauder", label="ESP32 Marauder", family="rig",
        ssid=(r"marauder",),
        name=(r"marauder",),
        auth=(r"\[RIG:marauder",),
    ),
    Fingerprint(
        id="ghostesp", label="GhostESP", family="rig",
        ssid=(r"ghostesp", r"ghost.?esp"),
        name=(r"ghostesp", r"ghost.?esp"),
        auth=(r"\[RIG:ghostesp",),
    ),
    Fingerprint(
        id="piglet", label="Piglet wardrive", family="rig",
        ssid=(r"piglet",),
        name=(r"piglet",),
        auth=(r"\[RIG:piglet",),
    ),
    Fingerprint(
        id="hash-monster", label="ESP32 WiFi Hash Monster", family="rig",
        ssid=(r"hash.?monster",),
        name=(r"hash.?monster",),
        auth=(r"\[RIG:hashmonster",),
    ),
    Fingerprint(
        id="biscuit", label="Biscuit wardrive rig", family="rig",
        ssid=(r"^biscuit$",),
        name=(r"^biscuit$",),
        uuids=("4FAFC201-1FB5-459E-8FCC-C5C9C331914B",),
        auth=(r"\[RIG:biscuit",),
    ),
    Fingerprint(
        # LilyGO T-Deck running lilyshark.com firmware. Advertises the
        # Meshtastic GATT service so the official app can pair; the local
        # name is "Lilyshark <short>" (tdeck_ble.cpp / sim_main.cpp).
        # Must sit above the generic Meshtastic fingerprint. The LSK
        # analyzer GATT UUID is not advertised on current T-Deck firmware
        # — do not use it as detection.
        id="lilyshark-tdeck", label="Lilyshark T-Deck", family="rig",
        ssid=(r"^lilyshark",),
        name=(r"^lilyshark",),
        auth=(r"\[RIG:lilyshark",),
    ),
    Fingerprint(
        id="meshtastic", label="Meshtastic node", family="mesh",
        ssid=(r"meshtastic",),
        name=(r"meshtastic",),
        uuids=("6BA1B218-15A8-461F-9FA8-5DCAE273EAFD",),
        auth=(r"\[MESH:meshtastic",),
    ),
    Fingerprint(
        id="meshcore", label="MeshCore node", family="mesh",
        ssid=(r"meshcore",),
        name=(r"^meshcore",),
        auth=(r"\[MESH:meshcore",),
    ),
    Fingerprint(
        id="rnode", label="Reticulum RNode", family="mesh",
        ssid=(r"^rnode", r"rnode.?lora"),
        name=(r"^rnode", r"rnode.?lora"),
        auth=(r"\[MESH:rnode",),
    ),
    Fingerprint(
        id="nordic-uart", label="Nordic UART (ESP32/nRF serial)", family="rig",
        uuids=("6E400001-B5A3-F393-E0A9-E50E24DCCA9E",),
        auth=(r"\[BLE:UART",),
    ),
    Fingerprint(
        id="alfa-adapter", label="Alfa USB Wi-Fi adapter", family="chip",
        oui=("00C0CA",),
    ),
    Fingerprint(
        id="raspberry-pi", label="Raspberry Pi", family="chip",
        oui=("B827EB", "DCA632", "E45F01", "28CDC1", "D83ADD", "2CCF67"),
    ),
    Fingerprint(
        id="espressif-esp", label="Espressif ESP32/ESP8266", family="chip",
        ssid=(r"^esp[-_]?[0-9a-f]{4,8}$", r"^esp32", r"^espressif"),
        name=(r"^esp[-_]?[0-9a-f]{4,8}$", r"^esp32", r"^espressif"),
        oui=(
            "240AC4", "246F28", "24B2DE", "30AEA4", "3C6105", "4CEBD6",
            "58BF25", "84CCA8", "84F3EB", "8CAAB5", "94B97E", "A020A6",
            "AC6784", "B4E62D", "C44F33", "C8C9A3", "CC50E3", "D8A01D",
            "DC4F22", "E09806", "EC94CB", "FCF5C4",
        ),
    ),
)

_COMPILED: list[tuple[Fingerprint, dict[str, list[re.Pattern]]]] = []
for _fp in CATALOG:
    _COMPILED.append((_fp, {
        "ssid": [re.compile(p, re.I) for p in _fp.ssid],
        "name": [re.compile(p, re.I) for p in _fp.name],
        "auth": [re.compile(p, re.I) for p in _fp.auth],
    }))


def identify(
    *,
    ssid: str | None = None,
    name: str | None = None,
    bssid: str | None = None,
    auth_mode: str | None = None,
    service_uuids: list[str] | tuple[str, ...] | None = None,
) -> Hit | None:
    """Return the most specific fingerprint that matches, or None."""
    ssid = ssid or ""
    name = name or ""
    auth_mode = auth_mode or ""
    oui = _norm_oui(bssid or "")
    uuids = {_norm_uuid(u) for u in (service_uuids or ())}
    for fp, rx in _COMPILED:
        if any(p.search(ssid) for p in rx["ssid"]):
            return Hit(fp.id, fp.label, fp.family, "ssid")
        if any(p.search(name) for p in rx["name"]):
            return Hit(fp.id, fp.label, fp.family, "name")
        if any(p.search(auth_mode) for p in rx["auth"]):
            return Hit(fp.id, fp.label, fp.family, "auth")
        if uuids and {_norm_uuid(u) for u in fp.uuids} & uuids:
            return Hit(fp.id, fp.label, fp.family, "uuid")
        if oui and oui in fp.oui:
            return Hit(fp.id, fp.label, fp.family, "oui")
    return None


def catalog_rows() -> list[dict]:
    return [
        {
            "id": fp.id, "label": fp.label, "family": fp.family,
            "ssid": ", ".join(fp.ssid) or None,
            "ble_name": ", ".join(fp.name) or None,
            "uuids": ", ".join(fp.uuids) or None,
            "oui": ", ".join(fp.oui) or None,
        }
        for fp in CATALOG
    ]


def apply(devices: list[dict]) -> None:
    """Stamp gadget_* fields onto debrief device dicts in place."""
    from .lscap import parse_lilyshark_name

    for d in devices:
        hit = identify(
            ssid=d.get("ssid"),
            name=d.get("ssid"),
            bssid=d.get("bssid"),
            auth_mode=d.get("auth_mode"),
        )
        d["gadget"] = hit.label if hit else None
        d["gadget_id"] = hit.id if hit else None
        d["gadget_family"] = hit.family if hit else None
        d["gadget_via"] = hit.via if hit else None
        parsed = parse_lilyshark_name(d.get("ssid") or d.get("name"))
        if parsed and parsed.get("short"):
            d["lilyshark_short"] = parsed["short"]
            d["bang_mask"] = parsed.get("bang_mask")
            if hit is None:
                d["gadget"] = "Lilyshark T-Deck"
                d["gadget_id"] = "lilyshark-tdeck"
                d["gadget_family"] = "rig"
                d["gadget_via"] = "name"

"""RF gadget fingerprint tests — offline, no DuckDB."""

from spectrecon.gadgets import identify, catalog_rows, apply


def test_flipper_uuid_and_name():
    white = identify(service_uuids=["00003082-0000-1000-8000-00805f9b34fb"])
    assert white is not None and white.id == "flipper-zero"
    named = identify(name="Flipper White")
    assert named is not None and named.family == "gadget"
    oui = identify(bssid="0C:FA:22:11:22:33")
    assert oui is not None and oui.via == "oui"


def test_pineapple_setup_ssid():
    hit = identify(ssid="Pineapple_A1B2")
    assert hit is not None and hit.id == "wifi-pineapple"


def test_marauder_beats_espressif_oui():
    hit = identify(ssid="ESP32 Marauder", bssid="24:6F:28:AA:BB:01")
    assert hit is not None and hit.id == "esp32-marauder"
    chip = identify(bssid="24:6F:28:AA:BB:01")
    assert chip is not None and chip.family == "chip" and chip.id == "espressif-esp"


def test_lilyshark_tdeck_beats_meshtastic_uuid():
    """Firmware advertises Meshtastic's service UUID under a Lilyshark name."""
    hit = identify(
        name="Lilyshark 4B01",
        service_uuids=["6ba1b218-15a8-461f-9fa8-5dcae273eafd"],
    )
    assert hit is not None and hit.id == "lilyshark-tdeck"
    lsk = identify(service_uuids=["6c736b00-9c1d-4b7a-b3f2-1d0e5a7c4e10"])
    assert lsk is None  # LSK GATT is unimplemented on current T-Deck firmware
    stock = identify(name="Meshtastic_ab12")
    assert stock is not None and stock.id == "meshtastic"


def test_mesh_and_biscuit_auth_tags():
    assert identify(auth_mode="[MESH:meshtastic]").id == "meshtastic"
    assert identify(name="Biscuit").id == "biscuit"
    assert identify(name="MeshCore-cabin").id == "meshcore"


def test_deauther_and_pwnagotchi():
    assert identify(ssid="pwned").id == "wifi-deauther"
    assert identify(ssid="pwnagotchi").id == "pwnagotchi"


def test_catalog_covers_core_ids():
    ids = {r["id"] for r in catalog_rows()}
    assert {"flipper-zero", "wifi-pineapple", "esp32-marauder",
            "lilyshark-tdeck", "meshtastic", "biscuit"} <= ids


def test_apply_stamps_device_dict():
    devices = [{"bssid": "AA:BB:CC:DD:EE:FF", "ssid": "Pineapple_FFFF",
                "auth_mode": ""}]
    apply(devices)
    assert devices[0]["gadget"] == "Hak5 WiFi Pineapple"
    assert devices[0]["gadget_family"] == "gadget"


def test_apply_lilyshark_short_name():
    devices = [{"bssid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
                "ssid": "Lilyshark 4B01", "auth_mode": "[RIG:lilyshark]"}]
    apply(devices)
    assert devices[0]["gadget_id"] == "lilyshark-tdeck"
    assert devices[0]["lilyshark_short"] == "4B01"
    assert devices[0]["bang_mask"] == "!****4B01"
    assert devices[0].get("from_bang") is None

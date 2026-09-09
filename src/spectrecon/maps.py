"""Self-contained HTML maps (folium/Leaflet) for debrief and gaps output.

These are the shareable artifacts — wardrivers post map screenshots.
"""

from pathlib import Path


def _base_map(points: list[tuple[float, float]]):
    import folium

    if points:
        lat = sum(p[0] for p in points) / len(points)
        lon = sum(p[1] for p in points) / len(points)
    else:
        lat, lon = 39.8, -98.6
    return folium.Map(location=[lat, lon], zoom_start=12, tiles="OpenStreetMap")


def _located(rows: list[dict]) -> list[dict]:
    return [r for r in rows
            if r.get("lat") is not None and r.get("lon") is not None]


def _deck_key(row: dict) -> tuple:
    return (
        round(float(row["lat"]), 6),
        round(float(row["lon"]), 6),
        row.get("identity") or row.get("from_bang") or row.get("short"),
    )


def debrief_map(result: dict, out: Path) -> None:
    """Devices colored by flag: red=anomaly, green=attributed, blue=other."""
    import folium

    anomaly_bssids = {a["bssid"] for a in result["anomalies"]}
    attributed = {a["bssid"]: a for a in result["attributions"]}
    devices = result["devices"]
    lora = _located(result.get("lora") or [])
    extra_decks = []
    seen = {_deck_key(n) for n in lora if n.get("capturing_deck")}
    for deck in _located(result.get("capturing_decks") or []):
        key = _deck_key(deck)
        if key not in seen:
            extra_decks.append(deck)
            seen.add(key)
    m = _base_map(
        [(d["lat"], d["lon"]) for d in devices]
        + [(n["lat"], n["lon"]) for n in lora]
        + [(d["lat"], d["lon"]) for d in extra_decks]
    )
    for d in devices:
        bssid = d["bssid"]
        color = ("darkred" if d.get("gadget_family") in ("gadget", "rig")
                 else "orange" if d.get("gadget_family") == "mesh"
                 else "red" if bssid in anomaly_bssids
                 else "green" if bssid in attributed else "blue")
        lines = [f"<b>{d['ssid'] or '(hidden)'}</b>", bssid]
        if d.get("gadget"):
            lines.append(f"gadget: {d['gadget']} ({d.get('gadget_via')})")
        if d.get("vendor"):
            lines.append(d["vendor"])
        if d.get("randomized_mac"):
            lines.append("<i>randomized MAC</i>")
        if bssid in attributed:
            lines.append(f"attributed: {attributed[bssid]['entity_name']}")
        if d.get("nearest_tower_owner"):
            lines.append(f"tower {d['nearest_tower_km']} km: "
                         f"{d['nearest_tower_owner']}")
        lines.append(f"rssi {d['rssi']}")
        folium.CircleMarker(
            location=[d["lat"], d["lon"]], radius=7,
            color=color, fill=True, fill_opacity=0.8,
            popup=folium.Popup("<br>".join(lines), max_width=300),
        ).add_to(m)
    for node in lora:
        if node.get("lat") is None or node.get("lon") is None:
            continue
        color = "black" if node.get("capturing_deck") else "purple"
        lines = [
            f"<b>{node.get('identity') or node.get('from_bang') or 'lora'}</b>",
            node.get("protocol") or "lora",
        ]
        if node.get("gadget"):
            lines.append(node["gadget"])
        if node.get("lilyshark_short"):
            lines.append(f"BLE short {node['lilyshark_short']}")
        if node.get("ble_name"):
            lines.append(f"BLE {node['ble_name']}")
        if node.get("role"):
            lines.append(f"role {node['role']}")
        if node.get("position_via"):
            lines.append(f"via {node['position_via']}")
        if node.get("witnesses"):
            lines.append(f"witnessed by {node['witnesses']} other capture(s)")
        folium.CircleMarker(
            location=[node["lat"], node["lon"]], radius=8,
            color=color, fill=True, fill_opacity=0.85,
            popup=folium.Popup("<br>".join(lines), max_width=300),
        ).add_to(m)
    for deck in extra_decks:
        lines = [
            f"<b>{deck.get('identity') or deck.get('bang_mask') or 'T-Deck'}</b>",
            "capturing T-Deck",
        ]
        if deck.get("short"):
            lines.append(f"BLE short {deck['short']}")
        if deck.get("ble_name"):
            lines.append(f"BLE {deck['ble_name']}")
        if deck.get("position_via"):
            lines.append(f"via {deck['position_via']}")
        if deck.get("fw"):
            lines.append(f"fw {deck['fw']}")
        folium.CircleMarker(
            location=[deck["lat"], deck["lon"]], radius=9,
            color="black", fill=True, fill_opacity=0.9,
            popup=folium.Popup("<br>".join(lines), max_width=300),
        ).add_to(m)
    m.save(str(out))


# mesh.nodes source -> marker color
MESH_SOURCE_COLORS = {
    "meshtastic": "green",
    "meshcore": "orange",
    "ttn_gateway": "blue",
    "aredn": "red",
    "reticulum": "cadetblue",
}


def mesh_map(nodes: list[dict], out: Path,
             center: tuple[float, float] | None = None) -> None:
    """Mesh-network nodes colored by source (see MESH_SOURCE_COLORS)."""
    import folium

    pts = [(n["lat"], n["lon"]) for n in nodes]
    m = _base_map([center] if center else pts)
    for n in nodes:
        lines = [f"<b>{n.get('name') or n['node_id']}</b>",
                 f"{n['source']} - {n.get('node_type') or 'node'}"]
        if n.get("hw_or_radio"):
            lines.append(n["hw_or_radio"])
        if n.get("last_seen"):
            lines.append(f"last seen {n['last_seen']}")
        if n.get("dist_km") is not None:
            lines.append(f"{round(n['dist_km'], 1)} km")
        folium.CircleMarker(
            location=[n["lat"], n["lon"]], radius=5,
            color=MESH_SOURCE_COLORS.get(n["source"], "gray"),
            fill=True, fill_opacity=0.7,
            popup=folium.Popup("<br>".join(lines), max_width=300),
        ).add_to(m)
    m.save(str(out))


def gaps_map(center: tuple[float, float], gaps: dict, out: Path) -> None:
    """Coverage-gap targets: orange=licensed site, purple=ASR tower."""
    import folium

    m = _base_map([center])
    folium.Circle(
        location=list(center), radius=250, color="blue", weight=1,
        fill=False, dash_array="5",
    ).add_to(m)
    for s in gaps["sites"]:
        folium.CircleMarker(
            location=[s["lat"], s["lon"]], radius=6,
            color="orange", fill=True, fill_opacity=0.8,
            popup=folium.Popup(
                f"<b>{s['call_sign']}</b><br>{s['entity_name']}<br>"
                f"{s['radio_service_code']} - {s['location_city']}, "
                f"{s['location_state']}<br>{round(s['dist_km'], 1)} km",
                max_width=300),
        ).add_to(m)
    for t in gaps["towers"]:
        folium.CircleMarker(
            location=[t["lat"], t["lon"]], radius=6,
            color="purple", fill=True, fill_opacity=0.8,
            popup=folium.Popup(
                f"<b>{t['registration_number']}</b><br>{t['owner_name']}<br>"
                f"{t['structure_type']} {t['height_overall_m']} m<br>"
                f"{round(t['dist_km'], 1)} km",
                max_width=300),
        ).add_to(m)
    m.save(str(out))

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


def debrief_map(result: dict, out: Path) -> None:
    """Devices colored by flag: red=anomaly, green=attributed, blue=other."""
    import folium

    anomaly_bssids = {a["bssid"] for a in result["anomalies"]}
    attributed = {a["bssid"]: a for a in result["attributions"]}
    devices = result["devices"]
    m = _base_map([(d["lat"], d["lon"]) for d in devices])
    for d in devices:
        bssid = d["bssid"]
        color = ("red" if bssid in anomaly_bssids
                 else "green" if bssid in attributed else "blue")
        lines = [f"<b>{d['ssid'] or '(hidden)'}</b>", bssid]
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

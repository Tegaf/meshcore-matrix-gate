"""Matrix delivery reports for MeshCore DM sends (ACK-based)."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from meshcore import EventType

from mcmgate.log_utils import get_logger

logger = get_logger(name="DeliveryReport")


def delivery_config(cfg: dict | None) -> dict:
    defaults = {
        "enabled": False,
        "dm_only": True,
        "max_attempts": 3,
        "max_flood_attempts": 2,
        "flood_after": 2,
        "min_timeout_sec": 3,
        "destination": "same_room",
        "status_room_id": "",
        "include_trip_time": True,
        "include_route": True,
        "only_contact_pubkeys": [],
        "templates": {
            "delivered": (
                "MeshCore: doručeno (ACK) → {contact} | {mode} | {hops} | {nodes} | RTT {trip_ms} ms"
            ),
            "failed": (
                "MeshCore: nedoručeno → {contact} po {attempts} pokusech (bez ACK; zpráva může stejně dojít floodem)"
            ),
        },
    }
    raw = (cfg or {}).get("delivery_reports") or {}
    out = {**defaults, **raw}
    templates = {**defaults["templates"], **(raw.get("templates") or {})}
    out["templates"] = templates
    return out


def _contact_display_name(pubkey: str, contacts: dict) -> str:
    pk = pubkey.lower()
    for cpk, c in contacts.items():
        full = (c.get("public_key") or cpk).lower()
        if full == pk:
            return (c.get("adv_name") or "").strip() or pubkey[:16] + "…"
    return pubkey[:16] + "…"


def _resolve_path_labels(contact: dict, all_contacts: dict) -> tuple[str, str]:
    """Return (hops_label, nodes_label) from contact out_path fields."""
    path_len = contact.get("out_path_len", -1)
    if path_len == -1 or path_len == 255:
        return "flood", "—"
    if path_len < 0:
        return "?", "—"
    hops = path_len & 0x3F
    hash_mode = contact.get("out_path_hash_mode", 0)
    if hash_mode is None or hash_mode < 0:
        hash_mode = 0
    hash_size = hash_mode + 1
    path_hex = (contact.get("out_path") or "").strip()
    if not path_hex or hops == 0:
        return "direct (0 hop)", "—"
    labels = []
    for i in range(hops):
        chunk = path_hex[i * hash_size * 2 : (i + 1) * hash_size * 2]
        if not chunk:
            break
        label = chunk
        for _cpk, c in all_contacts.items():
            full = (c.get("public_key") or _cpk).lower()
            if full.startswith(chunk.lower()):
                label = (c.get("adv_name") or chunk).strip()
                break
        labels.append(label)
    nodes = " → ".join(labels) if labels else path_hex[:24] + "…"
    return f"{hops} hop", nodes


async def _fetch_contact(mc, pubkey: str) -> dict | None:
    res = await mc.commands.get_contacts(timeout=8)
    if not res or res.type != EventType.CONTACTS or not res.payload:
        return None
    pk = pubkey.lower()
    for cpk, c in res.payload.items():
        full = (c.get("public_key") or cpk).lower()
        if full == pk:
            return c
    return None


async def send_dm_with_retry(
    mc,
    pubkey: str,
    message: str,
    dr_cfg: dict,
) -> tuple[Optional[Any], Optional[dict], int]:
    """
    Send DM with retry; return (msg_sent_event|None, ack_payload|None, attempts_used).
    """
    ack_by_code: dict[str, dict] = {}

    def on_ack(event):
        code = event.attributes.get("code") or ""
        if code:
            ack_by_code[code] = dict(event.payload or {})

    sub = mc.dispatcher.subscribe(EventType.ACK, on_ack)
    attempts_used = dr_cfg.get("max_attempts", 3)
    try:
        result = await mc.commands.send_msg_with_retry(
            pubkey,
            message,
            max_attempts=dr_cfg.get("max_attempts", 3),
            max_flood_attempts=dr_cfg.get("max_flood_attempts", 2),
            flood_after=dr_cfg.get("flood_after", 2),
            min_timeout=dr_cfg.get("min_timeout_sec", 3),
        )
    finally:
        sub.unsubscribe()

    if result is None:
        return None, None, attempts_used

    exp = result.payload.get("expected_ack")
    if isinstance(exp, bytes):
        code = exp.hex()
    else:
        code = str(exp)
    ack_payload = ack_by_code.get(code)
    return result, ack_payload, attempts_used


def _format_report(
    dr_cfg: dict,
    *,
    delivered: bool,
    contact_label: str,
    msg_sent_event,
    ack_payload: dict | None,
    contact_entry: dict | None,
    all_contacts: dict,
    attempts: int,
) -> str:
    templates = dr_cfg.get("templates") or {}
    if delivered:
        mode = "flood" if msg_sent_event.payload.get("type") == 1 else "direct"
        trip_ms = "?"
        if dr_cfg.get("include_trip_time") and ack_payload and "trip_time" in ack_payload:
            trip_ms = str(ack_payload["trip_time"])
        hops, nodes = "?", "—"
        if dr_cfg.get("include_route") and contact_entry:
            hops, nodes = _resolve_path_labels(contact_entry, all_contacts)
        tpl = templates.get(
            "delivered",
            "MeshCore: doručeno (ACK) → {contact} | {mode} | {hops} | {nodes} | RTT {trip_ms} ms",
        )
        return tpl.format(
            contact=contact_label,
            mode=mode,
            hops=hops,
            nodes=nodes,
            trip_ms=trip_ms,
            attempts=attempts,
        )
    tpl = templates.get(
        "failed",
        "MeshCore: nedoručeno → {contact} po {attempts} pokusech",
    )
    return tpl.format(contact=contact_label, attempts=attempts, mode="", hops="", nodes="", trip_ms="")


async def post_delivery_report_text(room_ids: list[str], text: str) -> None:
    from mcmgate.matrix_utils import matrix_relay_status

    for rid in room_ids:
        if rid:
            await matrix_relay_status(rid, text)


def report_destination_room_ids(dr_cfg: dict, source_room_id: str) -> list[str]:
    dest = dr_cfg.get("destination", "same_room")
    status = (dr_cfg.get("status_room_id") or "").strip()
    rooms: list[str] = []
    if dest in ("same_room", "both") and source_room_id:
        rooms.append(source_room_id)
    if dest in ("status_room", "both") and status:
        if status not in rooms:
            rooms.append(status)
    return rooms


def delivery_reports_apply_to_pubkey(dr_cfg: dict, pubkey: str) -> bool:
    only = dr_cfg.get("only_contact_pubkeys") or []
    if not only:
        return True
    pk = pubkey.lower()
    return any(str(x).strip().lower() == pk for x in only)


async def send_dm_and_maybe_report(
    mc,
    pubkey: str,
    message: str,
    *,
    cfg: dict,
    source_room_id: str,
    contact_label: str | None = None,
) -> Any:
    """Send DM; optionally post Matrix delivery report. Returns mesh send result event or None."""
    dr = delivery_config(cfg)
    if not dr.get("enabled") or not delivery_reports_apply_to_pubkey(dr, pubkey):
        return await mc.commands.send_msg(pubkey, message)

    msg_sent, ack_payload, attempts = await send_dm_with_retry(mc, pubkey, message, dr)
    delivered = msg_sent is not None

    contact_entry = await _fetch_contact(mc, pubkey)
    all_contacts = {}
    res = await mc.commands.get_contacts(timeout=8)
    if res and res.type == EventType.CONTACTS and res.payload:
        all_contacts = res.payload

    label = contact_label or _contact_display_name(pubkey, all_contacts)
    text = _format_report(
        dr,
        delivered=delivered,
        contact_label=label,
        msg_sent_event=msg_sent,
        ack_payload=ack_payload,
        contact_entry=contact_entry,
        all_contacts=all_contacts,
        attempts=attempts,
    )
    room_ids = report_destination_room_ids(dr, source_room_id)
    if room_ids:
        await post_delivery_report_text(room_ids, text)
    else:
        logger.warning("delivery_reports: no destination room configured")

    return msg_sent

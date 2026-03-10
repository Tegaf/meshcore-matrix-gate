"""Matrix connection and message relay for MCMGate."""
import asyncio
import os
import re
import ssl
import time

import certifi

_DEBUG = os.environ.get("MCMGATE_DEBUG") == "1"
from nio import AsyncClient, AsyncClientConfig, MatrixRoom, ReactionEvent, RoomMessageEmote, RoomMessageNotice, RoomMessageText, WhoamiError
from nio.events.room_events import MegolmEvent, RoomMemberEvent
from nio.responses import RoomCreateError

from mcmgate.config import get_base_dir, load_credentials
from mcmgate.log_utils import get_logger

logger = get_logger(name="matrix_utils")
config = None
matrix_client = None

# Dedup: same (sender, text) from bridged rooms - avoid relaying twice
_matrix_recent_relay: dict[str, float] = {}
_MATRIX_RELAY_DEDUP_SEC = 8
matrix_homeserver = None
matrix_rooms = None
matrix_access_token = None
bot_user_id = None
bot_start_time = int(time.time() * 1000)

DEFAULT_MATRIX_PREFIX = "[{long}/{mesh}]: "


def get_matrix_prefix(cfg, longname, shortname, meshnet_name):
    """Generate prefix for messages relayed to Matrix."""
    if not cfg:
        return ""
    mc = cfg.get("matrix", {})
    if not mc.get("prefix_enabled", True):
        return ""
    fmt = mc.get("prefix_format", DEFAULT_MATRIX_PREFIX)
    try:
        return fmt.format(long=longname or "", short=shortname or "", mesh=meshnet_name or "")
    except (KeyError, ValueError):
        return DEFAULT_MATRIX_PREFIX.format(long=longname or "", mesh=meshnet_name or "")


async def connect_matrix(passed_config=None):
    global matrix_client, matrix_homeserver, matrix_rooms, matrix_access_token, bot_user_id, config
    if passed_config:
        config = passed_config
    if not config:
        logger.error("No config")
        return None
    matrix_rooms = config["matrix_rooms"]
    matrix_section = config.get("matrix", {})

    # Prefer credentials.json (from mcmgate auth login) for E2EE – mmrelay-style
    creds = load_credentials()
    device_id = ""
    if creds and creds.get("access_token"):
        h = creds.get("homeserver", "").strip()
        t = creds.get("access_token", "").strip()
        u = creds.get("user_id", "").strip()
        if h and t and u:
            matrix_homeserver = h
            matrix_access_token = t
            bot_user_id = u
            device_id = creds.get("device_id", "").strip()
            logger.info("Using Matrix credentials from credentials.json (E2EE)")
    if not matrix_access_token:
        matrix_homeserver = matrix_section.get("homeserver", "")
        matrix_access_token = matrix_section.get("access_token", "")
        bot_user_id = matrix_section.get("bot_user_id", "")

    if not matrix_access_token:
        logger.error("No Matrix access_token (config or credentials.json)")
        return None

    if matrix_client:
        return matrix_client
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    encryption_enabled = matrix_section.get("encryption_enabled", True)
    client_config = AsyncClientConfig(encryption_enabled=encryption_enabled)
    store_path = os.path.join(get_base_dir(), "store")
    os.makedirs(store_path, exist_ok=True)
    matrix_client = AsyncClient(
        homeserver=matrix_homeserver,
        user=bot_user_id,
        device_id=device_id or "",
        store_path=store_path,
        config=client_config,
        ssl=ssl_context,
    )
    matrix_client.access_token = matrix_access_token
    matrix_client.user_id = bot_user_id
    if not device_id:
        whoami = await matrix_client.whoami()
        if isinstance(whoami, WhoamiError):
            logger.error(f"Matrix whoami failed: {whoami}")
            return None
        matrix_client.device_id = whoami.device_id
    # restore_login loads E2EE store (decryption keys)
    if encryption_enabled:
        try:
            matrix_client.restore_login(
                bot_user_id, matrix_client.device_id, matrix_access_token
            )
            logger.info("Matrix E2EE store loaded")
        except Exception as e:
            logger.warning(f"Matrix E2EE store load failed (encrypted rooms may not work): {e}")
    return matrix_client


async def join_matrix_room(client, room_config):
    from nio.responses import JoinError
    room_id = room_config["id"]
    if room_id.startswith("#"):
        resp = await client.room_resolve_alias(room_id)
        if hasattr(resp, "room_id") and resp.room_id:
            room_id = resp.room_id
    resp = await client.join(room_id)
    if isinstance(resp, JoinError):
        logger.warning(f"Could not join {room_id}: {resp}")
        return False
    logger.info(f"Joined room {room_id}")
    return True


async def force_rejoin_room(client, room_id: str) -> str:
    """Leave and re-join room – recovery when room freezes. Returns resolved room_id."""
    if room_id.startswith("#"):
        resp = await client.room_resolve_alias(room_id)
        if hasattr(resp, "room_id") and resp.room_id:
            room_id = resp.room_id
    try:
        await client.room_leave(room_id)
        await asyncio.sleep(1.0)
    except Exception as e:
        logger.debug(f"Leave {room_id}: {e}")
    try:
        await client.join(room_id)
        logger.info(f"Force re-joined room {room_id}")
    except Exception as e:
        logger.warning(f"Could not re-join {room_id}: {e}")
    return room_id


async def matrix_relay(room_id, message, longname, shortname, meshnet_name, portnum,
                      meshtastic_id=None, meshtastic_replyId=None, meshtastic_text=None,
                      emote=False, emoji=False, reply_to_event_id=None):
    """Send message from MeshCore to Matrix room."""
    global config
    client = await connect_matrix()
    if not client or not config:
        return
    # Resolve room alias to canonical ID
    if room_id.startswith("#"):
        resp = await client.room_resolve_alias(room_id)
        if hasattr(resp, "room_id") and resp.room_id:
            room_id = resp.room_id
        else:
            logger.warning(f"Could not resolve room alias {room_id}")
            return
    # Conduit may use different server suffix - resolve from client.rooms (populated by sync)
    room_localpart = room_id.split(":")[0] if ":" in room_id else room_id
    resolved_id = None
    if client.rooms:
        for rid in client.rooms:
            if rid.split(":")[0] == room_localpart:
                resolved_id = rid
                break
    # Fallback: use config room_id if it exists in client.rooms (exact match)
    if not resolved_id and room_id in client.rooms:
        resolved_id = room_id
    # Workaround: Conduit sync may not populate client.rooms – add room manually and fetch members
    if not resolved_id:
        logger.info(f"Room {room_localpart} not in client.rooms, adding manually (Conduit workaround)")
        client.rooms[room_id] = MatrixRoom(room_id, client.user_id, encrypted=True)
        try:
            await client.joined_members(room_id)
        except Exception as e:
            logger.debug(f"joined_members for {room_localpart}: {e}")
        resolved_id = room_id
    room_id = resolved_id
    local_meshnet = config.get("meshcore", {}).get("meshnet_name", "MeshCore")
    content = {
        "msgtype": "m.emote" if emote else "m.text",
        "body": message,
        "meshtastic_longname": longname,
        "meshtastic_shortname": shortname,
        "meshtastic_meshnet": local_meshnet,
    }
    if meshtastic_id:
        content["meshtastic_id"] = meshtastic_id
    if meshtastic_text:
        content["meshtastic_text"] = meshtastic_text
    ignore_unverified = config.get("matrix", {}).get("ignore_unverified_devices", False)
    from nio.responses import RoomSendError

    async def _do_send(rid):
        return await asyncio.wait_for(
            client.room_send(
                room_id=rid,
                message_type="m.room.message",
                content=content,
                ignore_unverified_devices=ignore_unverified,
            ),
            timeout=10.0,
        )

    logger.info(f"Sending to room {room_localpart} (room_id={room_id})")
    resp = None
    try:
        resp = await _do_send(room_id)
        if isinstance(resp, RoomSendError):
            logger.warning(f"Matrix send failed for {room_localpart}, trying force re-join...")
            room_id = await force_rejoin_room(client, room_id)
            resp = await _do_send(room_id)
    except (asyncio.TimeoutError, OSError, ConnectionError) as e:
        logger.warning(f"Matrix send error for {room_localpart}: {e}, trying force re-join...")
        try:
            room_id = await force_rejoin_room(client, room_id)
            resp = await _do_send(room_id)
        except Exception as e2:
            logger.error(f"Error sending to Matrix room {room_localpart}: {e2}")
            return
    except Exception as e:
        logger.error(f"Error sending to Matrix room {room_localpart}: {e}")
        return

    if resp is None or isinstance(resp, RoomSendError):
        logger.error(f"Matrix room_send failed for {room_localpart} (after retry): {resp}")
    else:
        logger.info(f"Sent to Matrix room {room_localpart} (event_id={resp.event_id})")


async def get_or_create_dm_room(client, user_id: str) -> str | None:
    """Get existing DM room with user, or create one. Returns room_id or None."""
    try:
        resp = await client.list_direct_rooms()
        if hasattr(resp, "rooms") and resp.rooms and isinstance(resp.rooms, dict):
            room_ids = resp.rooms.get(user_id)
            if room_ids:
                for rid in room_ids:
                    if rid in client.rooms:
                        return rid
                return room_ids[0] if room_ids else None
    except Exception as e:
        logger.debug(f"list_direct_rooms failed: {e}")

    try:
        create_resp = await client.room_create(invite=[user_id], is_direct=True)
        if hasattr(create_resp, "room_id") and create_resp.room_id:
            logger.info(f"Created DM room with {user_id}: {create_resp.room_id}")
            return create_resp.room_id
        if isinstance(create_resp, RoomCreateError):
            logger.warning(f"Failed to create DM with {user_id}: {create_resp}")
    except Exception as e:
        logger.warning(f"room_create DM failed for {user_id}: {e}")
    return None


async def matrix_relay_dm(user_id: str, message: str, longname: str, shortname: str, meshnet_name: str,
                         meshtastic_text: str | None = None):
    """Send message from MeshCore to Matrix user via DM."""
    global config
    client = await connect_matrix()
    if not client or not config:
        return
    room_id = await get_or_create_dm_room(client, user_id)
    if not room_id:
        logger.warning(f"Cannot get/create DM room for {user_id}")
        return
    content = {
        "msgtype": "m.text",
        "body": message,
        "meshtastic_longname": longname,
        "meshtastic_shortname": shortname,
        "meshtastic_meshnet": meshnet_name,
    }
    if meshtastic_text:
        content["meshtastic_text"] = meshtastic_text
    ignore_unverified = config.get("matrix", {}).get("ignore_unverified_devices", False)
    from nio.responses import RoomSendError
    try:
        resp = await asyncio.wait_for(
            client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
                ignore_unverified_devices=ignore_unverified,
            ),
            timeout=10.0,
        )
        if isinstance(resp, RoomSendError):
            logger.warning(f"Matrix DM send failed for {user_id}: {resp}")
        else:
            logger.info(f"Sent DM to {user_id} (event_id={resp.event_id})")
    except Exception as e:
        logger.warning(f"Matrix DM send error for {user_id}: {e}")


async def on_invite(room, event):
    """Join room when bot is invited (enables DM support)."""
    if event.membership != "invite" or event.state_key != bot_user_id:
        return
    room_id = getattr(room, "room_id", None) or getattr(event, "room_id", None)
    if not room_id:
        return
    client = await connect_matrix()
    if not client:
        return
    try:
        await client.join(room_id)
        logger.info(f"Joined invited room {room_id} (DM or group)")
    except Exception as e:
        logger.warning(f"Could not join invited room {room_id}: {e}")


def _on_megolm_event(room, event):
    """Log undecrypted MegolmEvent - bot has no keys for room."""
    room_short = room.room_id.split(":")[0] if ":" in room.room_id else room.room_id
    logger.warning(
        f"Matrix: undecryptable message in {room_short} from {event.sender} - "
        "Matrix->MeshCore relay blocked (verify bot device in Element Security settings)"
    )


async def on_room_message(room, event):
    """Handle Matrix message - relay to MeshCore if broadcast enabled."""
    from mcmgate.meshcore_utils import meshcore_client, register_sent_to_meshcore, get_dm_reply_pubkeys_for_room
    from mcmgate.message_queue import queue_message, get_message_queue

    room_localpart = room.room_id.split(":")[0] if ":" in room.room_id else room.room_id
    logger.debug(f"Matrix message received in room {room_localpart} from {event.sender}")

    if not meshcore_client or event.sender == bot_user_id:
        return
    if hasattr(event, "server_timestamp") and event.server_timestamp and event.server_timestamp < bot_start_time:
        return
    # Skip reactions - we only relay text messages for now
    if isinstance(event, (ReactionEvent, RoomMessageEmote)):
        return

    text = event.body.strip() if hasattr(event, "body") else ""
    if not text:
        logger.debug(f"Matrix: empty body for event {type(event).__name__} in {room.room_id}")
        return

    # Match room by ID - Conduit may use different server suffix
    room_config = None
    for rc in matrix_rooms:
        rc_id = rc["id"]
        rc_localpart = rc_id.split(":")[0] if ":" in rc_id else rc_id
        if rc_id == room.room_id or rc_localpart == room_localpart:
            room_config = rc
            break

    # meshcore_dm room: Matrix messages relay to MeshCore as DM (or fallback to reply_channel)
    meshcore_dm_cfg = config.get("meshcore_dm", {})
    if not room_config and meshcore_dm_cfg.get("enabled"):
        # matrix_to_meshcore_only: room → contacts (Matrix→MeshCore only, no receive)
        for m2m_room, pubkeys in (meshcore_dm_cfg.get("matrix_to_meshcore_only", {}) or {}).items():
            m2m_local = m2m_room.split(":")[0] if ":" in m2m_room else m2m_room
            if m2m_room == room.room_id or m2m_local == room_localpart:
                pks = [x.get("pubkey", x) if isinstance(x, dict) else str(x) for x in (pubkeys if isinstance(pubkeys, list) else [pubkeys])]
                pks = [p.strip() for p in pks if isinstance(p, str) and len(p) == 64]
                if pks:
                    room_config = {"_meshcore_dm": True, "_dm_pubkeys": pks}
                    break
        if not room_config and (meshcore_dm_cfg.get("contact_rooms") or meshcore_dm_cfg.get("matrix_to_meshcore_only")):
            # Match room_id or any room from contact_rooms
            md_id = meshcore_dm_cfg.get("room_id")
            if md_id and (md_id == room.room_id or md_id.split(":")[0] == room_localpart):
                room_config = {"meshcore_channel": meshcore_dm_cfg.get("reply_channel", 0), "_meshcore_dm": True}
            else:
                for rid in meshcore_dm_cfg.get("contact_rooms", {}).values():
                    for r in (rid if isinstance(rid, list) else [rid]):
                        if r and (r == room.room_id or (r.split(":")[0] if ":" in r else r) == room_localpart):
                            room_config = {"meshcore_channel": meshcore_dm_cfg.get("reply_channel", 0), "_meshcore_dm": True}
                            break
                    if room_config:
                        break

    # DM: room not in config, 2 members, dm_enabled -> relay to dm_default_channel
    dm_cfg = config.get("matrix_dms", {})
    if not room_config and dm_cfg.get("enabled") and getattr(room, "member_count", 0) == 2:
        channel = dm_cfg.get("default_channel", 0)
        room_config = {"meshcore_channel": channel, "_dm": True}
    elif not room_config:
        configured = [r["id"].split(":")[0] for r in (matrix_rooms or [])]
        logger.info(f"Matrix: room {room.room_id} not in config (have: {configured}), skipping")
        return

    if not config.get("meshcore", {}).get("broadcast_enabled", True):
        return

    channel = room_config.get("meshcore_channel", room_config.get("meshtastic_channel", 0))
    display_name = room.user_name(event.sender) or event.sender
    reply_message = f"{display_name}: {text}"

    # Dedup: same message from SAME room (avoid double process). Include channel so different rooms can both relay.
    now = time.time()
    dedup_key = f"{room_localpart}|{display_name}|{text}"
    for k, ts in list(_matrix_recent_relay.items()):
        if now - ts > _MATRIX_RELAY_DEDUP_SEC:
            del _matrix_recent_relay[k]
    if dedup_key in _matrix_recent_relay:
        logger.debug(f"Matrix: skipping duplicate relay: {text[:40]!r}")
        return
    _matrix_recent_relay[dedup_key] = now

    # Anti-loop: register IMMEDIATELY (echo may arrive before queue)
    register_sent_to_meshcore(reply_message)
    if _DEBUG:
        logger.info(f"[DEBUG] MATRIX_RECV: queued {reply_message!r} for MeshCore")

    # meshcore_dm: prefer DM to contact(s) over channel broadcast (room → contacts, shared = all)
    use_dm = room_config.get("_meshcore_dm") and hasattr(meshcore_client.commands, "send_msg")
    dm_reply_pubkeys = room_config.get("_dm_pubkeys") or (get_dm_reply_pubkeys_for_room(room.room_id, room_localpart) if use_dm else [])

    if use_dm and dm_reply_pubkeys:
        success_count = 0
        for pk in dm_reply_pubkeys:
            async def send_to_meshcore(pubkey=pk):
                return await meshcore_client.commands.send_msg(pubkey, reply_message)

            if queue_message(
                send_to_meshcore,
                description=f"Matrix DM reply from {display_name} to MeshCore contact",
                mapping_info={"matrix_sent_text": reply_message},
            ):
                success_count += 1
        if success_count:
            logger.info(f"Relaying Matrix message from {display_name} to MeshCore DM ({success_count} contacts, room {room_localpart})")
        else:
            logger.warning(f"Failed to queue Matrix->MeshCore DM from {display_name} (room {room_localpart})")
    else:
        async def send_to_meshcore():
            return await meshcore_client.commands.send_chan_msg(channel, reply_message)

        success = queue_message(
            send_to_meshcore,
            description=f"Matrix message from {display_name}",
            mapping_info={"matrix_sent_text": reply_message},
        )
        if success:
            logger.info(f"Relaying Matrix message from {display_name} to MeshCore channel {channel} (room {room_localpart})")
        else:
            logger.warning(f"Failed to queue Matrix->MeshCore from {display_name} (room {room_localpart})")
    get_message_queue().ensure_processor_started()

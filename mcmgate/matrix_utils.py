"""Matrix connection and message relay for MCMGate."""
import asyncio
import os
import re
import ssl
import time

import certifi

_DEBUG = os.environ.get("MCMGATE_DEBUG") == "1"
from nio import AsyncClient, AsyncClientConfig, ReactionEvent, RoomMessageEmote, RoomMessageNotice, RoomMessageText, WhoamiError
from nio.events.room_events import MegolmEvent, RoomMemberEvent

from mcmgate.config import get_base_dir
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
    matrix_homeserver = config["matrix"]["homeserver"]
    matrix_rooms = config["matrix_rooms"]
    matrix_access_token = config["matrix"]["access_token"]
    bot_user_id = config["matrix"]["bot_user_id"]
    if matrix_client:
        return matrix_client
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    # E2EE for encrypted rooms (Matrix room encryption)
    encryption_enabled = config.get("matrix", {}).get("encryption_enabled", True)
    client_config = AsyncClientConfig(encryption_enabled=encryption_enabled)
    store_path = os.path.join(get_base_dir(), "store")
    os.makedirs(store_path, exist_ok=True)
    matrix_client = AsyncClient(
        homeserver=matrix_homeserver,
        user=bot_user_id,
        device_id="",  # obtained from whoami
        store_path=store_path,
        config=client_config,
        ssl=ssl_context,
    )
    matrix_client.access_token = matrix_access_token
    matrix_client.user_id = bot_user_id
    whoami = await matrix_client.whoami()
    if isinstance(whoami, WhoamiError):
        logger.error(f"Matrix whoami failed: {whoami}")
        return None
    matrix_client.device_id = whoami.device_id
    # restore_login loads E2EE store (decryption keys)
    if encryption_enabled:
        try:
            matrix_client.restore_login(bot_user_id, whoami.device_id, matrix_access_token)
            logger.info("Matrix E2EE store loaded")
        except Exception as e:
            logger.warning(f"Matrix E2EE store load failed (encrypted rooms may not work): {e}")
    return matrix_client


async def join_matrix_room(client, room_config):
    room_id = room_config["id"]
    if room_id.startswith("#"):
        resp = await client.room_resolve_alias(room_id)
        if hasattr(resp, "room_id") and resp.room_id:
            room_id = resp.room_id
    try:
        await client.join(room_id)
        logger.info(f"Joined room {room_id}")
    except Exception as e:
        logger.warning(f"Could not join {room_id}: {e}")


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
    # Conduit may use different server suffix - use room ID from client.rooms if available
    room_localpart = room_id.split(":")[0] if ":" in room_id else room_id
    if room_id not in client.rooms and client.rooms:
        for rid in client.rooms:
            if rid.split(":")[0] == room_localpart:
                room_id = rid
                logger.info(f"Using client.rooms ID for {room_localpart}: {room_id}")
                break
        else:
            logger.debug(
                f"Room {room_localpart} not in client.rooms, trying config room_id. "
                f"Known: {list(client.rooms.keys())[:5]}"
            )
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


def _on_megolm_event(room, event):
    """Log undecrypted MegolmEvent - bot has no keys for room."""
    room_short = room.room_id.split(":")[0] if ":" in room.room_id else room.room_id
    logger.warning(
        f"Matrix: undecryptable message in {room_short} from {event.sender} - "
        "Matrix->MeshCore relay blocked (verify bot device in Element Security settings)"
    )


async def on_room_message(room, event):
    """Handle Matrix message - relay to MeshCore if broadcast enabled."""
    from mcmgate.meshcore_utils import meshcore_client, register_sent_to_meshcore
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
    if not room_config:
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

    async def send_to_meshcore():
        result = await meshcore_client.commands.send_chan_msg(channel, reply_message)
        return result

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

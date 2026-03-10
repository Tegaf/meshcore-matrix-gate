"""
MeshCore connection and message handling for MCMGate.
Uses direct serial protocol (no appstart) for USB firmware compatibility.
"""

import asyncio
import os
import time
import unicodedata
from typing import List, Optional

_DEBUG = os.environ.get("MCMGATE_DEBUG") == "1"

import serial_asyncio_fast as serial_asyncio
from hashlib import sha256

from meshcore import EventType
from meshcore.events import Event, EventDispatcher
from meshcore.reader import MessageReader

from mcmgate.db_utils import get_longname, get_shortname, save_longname, save_shortname
from mcmgate.log_utils import get_logger

config = None
matrix_rooms: List[dict] = []
_dm_key_store = None  # MeshCoreKeyStore for RX_LOG_DATA TEXT_MSG decryption

logger = get_logger(name="MeshCore")

# Anti-loop: messages we sent from Matrix -> MeshCore (do not relay back)
_recently_sent_to_meshcore: dict[str, float] = {}  # normalized text -> timestamp
_recently_sent_hashes: dict[str, float] = {}  # hash(content) -> timestamp (more robust)
_SENT_EXPIRE_SEC = 120

# Deduplication: content hash – mobile app may send same message 2x
_recently_relayed_hash: dict[str, float] = {}  # hash(normalized_content) -> timestamp
_RELAY_DEDUP_SEC = 90
_relay_lock = asyncio.Lock()


def _normalize_text(t: str) -> str:
    """Normalize for comparison (whitespace, unicode NFC)."""
    if not t:
        return ""
    return " ".join(unicodedata.normalize("NFC", t).split())


def register_sent_to_meshcore(text: str) -> None:
    """Register message sent from Matrix to MeshCore (for anti-loop)."""
    now = time.time()
    key = _normalize_text(text)
    if key:
        _recently_sent_to_meshcore[key] = now
        h = sha256(key.encode("utf-8")).hexdigest()[:16]
        _recently_sent_hashes[h] = now
        if _DEBUG:
            logger.info(f"[DEBUG] REGISTER_SENT: {key!r} hash={h} (set size={len(_recently_sent_to_meshcore)})")
    for d in (_recently_sent_to_meshcore, _recently_sent_hashes):
        for k in list(d):
            if now - d[k] > _SENT_EXPIRE_SEC:
                del d[k]


def _get_contacts_list(dm_cfg: dict) -> list:
    """Contacts from config or derived from contact_rooms + matrix_to_meshcore_only."""
    contacts = dm_cfg.get("contacts", []) or dm_cfg.get("peer_public_keys", [])
    if contacts:
        return contacts
    seen = set()
    result = []
    for pk in (dm_cfg.get("contact_rooms", {}) or {}).keys():
        if isinstance(pk, str) and len(pk) == 64 and pk.lower() not in seen:
            seen.add(pk.lower())
            result.append(pk)
    for pubkeys in (dm_cfg.get("matrix_to_meshcore_only", {}) or {}).values():
        for item in (pubkeys if isinstance(pubkeys, list) else [pubkeys]):
            pk = (item.get("pubkey", item) if isinstance(item, dict) else str(item)).strip()
            if len(pk) == 64 and pk.lower() not in seen:
                seen.add(pk.lower())
                result.append(pk)
    return result


def get_dm_reply_pubkey(cfg: Optional[dict] = None) -> Optional[str]:
    """Get first contact pubkey for Matrix->MeshCore DM reply. Returns 64-char hex or None."""
    cfg = cfg or config
    dm_cfg = (cfg or {}).get("meshcore_dm", {})
    if not dm_cfg.get("enabled"):
        return None
    contacts = _get_contacts_list(dm_cfg)
    if not isinstance(contacts, list):
        return None
    for item in contacts:
        if isinstance(item, str) and len(item) == 64 and all(c in "0123456789abcdefABCDEF" for c in item):
            return item
    global _dm_key_store
    if _dm_key_store and hasattr(_dm_key_store, "peer_public_keys") and _dm_key_store.peer_public_keys:
        return next(iter(_dm_key_store.peer_public_keys), None)
    return None


def get_dm_reply_pubkeys_for_room(room_id: str, room_localpart: str, cfg: Optional[dict] = None) -> list:
    """Get all contact pubkeys for Matrix->MeshCore DM when message is from given room.
    Room can be in multiple contacts (shared) → returns all. Falls back to first contact if none."""
    cfg = cfg or config
    dm_cfg = (cfg or {}).get("meshcore_dm", {})
    if not dm_cfg.get("enabled"):
        return []
    result = []
    for pk, rid in (dm_cfg.get("contact_rooms", {}) or {}).items():
        for r in (rid if isinstance(rid, list) else [rid]):
            if r and (r == room_id or (r.split(":")[0] if ":" in r else r) == room_localpart):
                if isinstance(pk, str) and len(pk) == 64 and all(c in "0123456789abcdefABCDEF" for c in pk):
                    result.append(pk)
                break
    if not result:
        first = get_dm_reply_pubkey(cfg)
        if first:
            result = [first]
    return result


def _was_recently_sent_to_meshcore(text: str) -> bool:
    """Was this message recently sent from Matrix to MeshCore?"""
    now = time.time()
    key = _normalize_text(text)
    if not key:
        return False
    # Content after first ":" – "Device: User: text" → "User: text"
    content = key.split(":", 1)[1].strip() if ":" in key else key
    # Direct match: received content == sent message (echo from mesh)
    if content and content in _recently_sent_to_meshcore:
        if now - _recently_sent_to_meshcore[content] <= _SENT_EXPIRE_SEC:
            return True
    # Hash-based: content "User: text" or "Device: User: text" → content
    if content:
        h = sha256(content.encode("utf-8")).hexdigest()[:16]
        if h in _recently_sent_hashes and now - _recently_sent_hashes[h] <= _SENT_EXPIRE_SEC:
            return True
    ts = _recently_sent_to_meshcore.get(key)
    if ts is not None and now - ts <= _SENT_EXPIRE_SEC:
        return True
    if ts is not None and now - ts > _SENT_EXPIRE_SEC:
        del _recently_sent_to_meshcore[key]
        return False
    # Fuzzy: mesh prepends "Device: " to our message
    for sent in list(_recently_sent_to_meshcore):
        if now - _recently_sent_to_meshcore[sent] > _SENT_EXPIRE_SEC:
            del _recently_sent_to_meshcore[sent]
            continue
        if key == sent or key.endswith(sent) or (len(sent) >= 8 and sent in key):
            return True
        if ":" in key:
            rest = key.split(":", 1)[1].strip()
            if rest == sent or (len(sent) >= 8 and sent in rest):
                return True
    return False


def _add_meshcore_shared_secrets(key_store, priv_hex: str) -> None:
    """Pre-compute X25519 shared secrets using MeshCore expanded key format.
    MeshCore exports orlp/ed25519 format: first 32 bytes = scalar for X25519 (not standard seed).
    meshcoredecoder's node_keys path fails; shared_secrets bypass works."""
    try:
        from cryptography.hazmat.primitives.asymmetric import x25519

        priv_bytes = bytes.fromhex(priv_hex)
        if len(priv_bytes) < 32:
            return
        scalar = bytearray(priv_bytes[:32])
        scalar[0] &= 248
        scalar[31] &= 63
        scalar[31] |= 64
        x25519_priv = x25519.X25519PrivateKey.from_private_bytes(bytes(scalar))

        try:
            from nacl.bindings import crypto_sign_ed25519_pk_to_curve25519
        except ImportError:
            logger.debug("MeshCore DM: PyNaCl not installed, skipping shared_secrets precompute")
            return

        for peer_pub in key_store.peer_public_keys:
            try:
                peer_bytes = bytes.fromhex(peer_pub)
                if len(peer_bytes) < 32:
                    continue
                peer_x25519 = crypto_sign_ed25519_pk_to_curve25519(peer_bytes[:32])
                x25519_pub = x25519.X25519PublicKey.from_public_bytes(peer_x25519)
                shared = x25519_priv.exchange(x25519_pub)
                key_store.add_shared_secret(peer_pub, shared.hex())
            except Exception as e:
                logger.debug(f"MeshCore DM: shared secret for {peer_pub[:16]}... failed: {e}")
        if key_store.shared_secrets:
            logger.info(f"MeshCore DM: precomputed {len(key_store.shared_secrets)} shared secret(s)")
    except Exception as e:
        logger.debug(f"MeshCore DM: shared secrets setup failed: {e}")


async def _setup_dm_key_store(mc, cfg) -> None:
    """Setup DM key store for RX_LOG_DATA TEXT_MSG decryption (TCP/WiFi only).
    Auto-fetches: node key from device. Peer keys from config contacts (pubkeys only)."""
    global _dm_key_store
    _dm_key_store = None
    dm_cfg = (cfg or {}).get("meshcore_dm", {})
    if not dm_cfg.get("enabled"):
        return
    try:
        from meshcoredecoder.crypto import MeshCoreKeyStore

        key_store = MeshCoreKeyStore()

        # Node public key: config override, or from device self_info
        pub_hex = dm_cfg.get("node_public_key")
        if not pub_hex and mc and mc.self_info:
            pub_hex = mc.self_info.get("public_key", "")

        # Node private key: config override, or try export from device
        priv_hex = dm_cfg.get("node_private_key")
        if not priv_hex and mc and hasattr(mc.commands, "export_private_key"):
            try:
                from meshcore import EventType

                res = await mc.commands.export_private_key()
                if res and res.type == EventType.PRIVATE_KEY and res.payload.get("private_key"):
                    priv_hex = res.payload["private_key"].hex()
                    logger.info("MeshCore DM: node private key exported from device")
            except Exception as e:
                logger.debug(f"export_private_key failed: {e}")

        if pub_hex and priv_hex and len(pub_hex) == 64 and len(priv_hex) == 128:
            key_store.add_node_key(pub_hex, priv_hex)
            src = "config" if dm_cfg.get("node_private_key") else "device export"
            logger.info(f"MeshCore DM: node key loaded for decryption (from {src})")
        elif not (pub_hex and priv_hex):
            logger.debug("MeshCore DM: no node key (add node_public_key + node_private_key to config if export disabled)")

        # Peer keys: from contacts or derived from contact_rooms + matrix_to_meshcore_only
        contact_pubkeys = _get_contacts_list(dm_cfg)
        for item in contact_pubkeys:
            pk = (item.get("pubkey", item) if isinstance(item, dict) else item)
            if not isinstance(pk, str):
                continue
            pk = pk.strip()
            if len(pk) == 64 and all(c in "0123456789abcdefABCDEF" for c in pk):
                key_store.add_peer_public_key(pk)
            else:
                logger.warning(f"MeshCore DM: invalid contact '{pk[:32]}...' – expected pubkey 64 hex chars")

        if key_store.peer_public_keys:
            logger.info(f"MeshCore DM: {len(key_store.peer_public_keys)} contacts for DM")

        # Pre-compute shared secrets using MeshCore key format (orlp/ed25519 expanded).
        # meshcoredecoder expects standard Ed25519 seed; MeshCore exports expanded format.
        # First 32 bytes of priv = X25519 scalar. Pre-compute and add to shared_secrets.
        if pub_hex and priv_hex and len(priv_hex) == 128:
            _add_meshcore_shared_secrets(key_store, priv_hex)

        if key_store.node_keys or key_store.shared_secrets:
            _dm_key_store = key_store
    except Exception as e:
        logger.warning(f"MeshCore DM key store setup failed: {e}")


async def _announce_dm_contacts(mc, cfg) -> None:
    """Send a DM to each meshcore_dm contact on startup so this device appears in their list."""
    dm_cfg = (cfg or {}).get("meshcore_dm", {})
    if not dm_cfg.get("enabled") or not dm_cfg.get("announce_on_start", False):
        return
    if not mc or not hasattr(mc.commands, "send_msg"):
        return
    contacts = _get_contacts_list(dm_cfg)
    if not contacts:
        return
    skip = set()
    for item in dm_cfg.get("announce_skip_contacts", []) or []:
        pk = (item.get("pubkey", item) if isinstance(item, dict) else str(item)).strip()
        if len(pk) == 64 and all(c in "0123456789abcdefABCDEF" for c in pk):
            skip.add(pk.lower())
    name = (mc.self_info or {}).get("adv_name", "Bridge") if mc.self_info else "Bridge"
    msg = f"{name} online"
    for item in contacts:
        if not isinstance(item, str):
            continue
        pk = item.strip()
        if len(pk) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pk):
            continue
        if pk.lower() in skip:
            continue
        try:
            await asyncio.sleep(0.5)
            register_sent_to_meshcore(msg)
            result = await mc.commands.send_msg(pk, msg)
            if result and result.type == EventType.ERROR:
                logger.debug(f"MeshCore DM announce to {pk[:16]}... failed: {result.payload}")
            else:
                logger.info(f"MeshCore DM: sent announce to contact {pk[:16]}... (shows in their list)")
        except Exception as e:
            logger.debug(f"MeshCore DM announce failed: {e}")


def _try_decrypt_rx_log_dm(payload: dict) -> tuple[str | None, str]:
    """Try to decrypt RX_LOG_DATA payload_type 2 (TEXT_MSG/DM). Returns (text, sender_prefix) or (None, '?')."""
    global _dm_key_store
    if not _dm_key_store:
        return None, "?"
    raw_payload = payload.get("payload")
    if not raw_payload or isinstance(raw_payload, str):
        raw_hex = raw_payload if isinstance(raw_payload, str) else (raw_payload.hex() if raw_payload else "")
    else:
        raw_hex = raw_payload.hex() if hasattr(raw_payload, "hex") else ""
    if not raw_hex or len(raw_hex) < 8:
        return None, "?"
    try:
        from meshcoredecoder import MeshCoreDecoder
        from meshcoredecoder.crypto import MeshCoreKeyStore
        from meshcoredecoder.types.crypto import DecryptionOptions
        from meshcoredecoder.types.enums import PayloadType

        options = DecryptionOptions(key_store=_dm_key_store)
        packet = MeshCoreDecoder.decode(raw_hex, options)
        if not packet or packet.payload_type != PayloadType.TextMessage:
            return None, "?"
        tm = packet.payload.get("decoded")
        if not tm:
            return None, "?"
        if not getattr(tm, "decrypted", None):
            logger.warning(
                f"DM decrypt: packet decoded but decrypted=None "
                f"(dest_hash={getattr(tm,'destination_hash','?')} src_hash={getattr(tm,'source_hash','?')})"
            )
            return None, "?"
        dec = tm.decrypted
        msg = dec.get("message", "")
        if not msg:
            return None, "?"
        src_hash = getattr(tm, "source_hash", "").upper()
        return msg, src_hash if src_hash else "?"
    except Exception as e:
        logger.warning(f"DM decrypt failed: {e}")
        return None, "?"


def _content_hash(text: str) -> str:
    """Hash of normalized content for deduplication."""
    key = _normalize_text(text)
    if not key:
        return ""
    # Use content (after first ": ") – "Device: message" → "message"
    content = key.split(":", 1)[1].strip() if ":" in key else key
    return sha256(content.encode("utf-8")).hexdigest()[:16] if content else ""


def _was_recently_relayed(text: str) -> bool:
    """Have we recently relayed this message to Matrix?"""
    now = time.time()
    h = _content_hash(text)
    if not h:
        return False
    ts = _recently_relayed_hash.get(h)
    if ts is not None and now - ts <= _RELAY_DEDUP_SEC:
        return True
    if ts is not None and now - ts > _RELAY_DEDUP_SEC:
        del _recently_relayed_hash[h]
    return False


def _mark_relayed(text: str) -> None:
    h = _content_hash(text)
    if h:
        now = time.time()
        _recently_relayed_hash[h] = now
        for k in list(_recently_relayed_hash):
            if now - _recently_relayed_hash[k] > _RELAY_DEDUP_SEC:
                del _recently_relayed_hash[k]

meshcore_client = None
event_loop = None
reconnecting = False
shutting_down = False
_msg_subscription = None
_auto_fetch_task = None
_tcp_poll_task = None

# MeshCore frame: 0x3c = send, 0x3e = receive
FRAME_SEND = 0x3C
FRAME_RECV = 0x3E

# Commands
CMD_GET_MSG = 0x0A
CMD_SEND_CHAN_MSG = 0x03
CMD_SEND_MSG = 0x02  # DM to contact


def _send_frame(transport, data: bytes) -> None:
    """Send MeshCore frame: 0x3c + len(2) + data."""
    frame = bytes([FRAME_SEND]) + len(data).to_bytes(2, "little") + data
    transport.write(frame)


class MeshCoreDirectCommands:
    """Minimal command interface for direct serial - send_chan_msg and get_msg."""

    def __init__(self, client: "MeshCoreDirectClient"):
        self._client = client

    async def send_chan_msg(
        self, chan: int, msg: str, timestamp: Optional[int] = None
    ) -> Event:
        """Send text to channel. Returns Event(OK) or Event(ERROR)."""
        # LoRa has ~230B payload limit - truncate
        msg = msg[:220] if len(msg) > 220 else msg
        if timestamp is None:
            timestamp = int(time.time())
        timestamp_bytes = timestamp.to_bytes(4, "little")
        data = bytes([CMD_SEND_CHAN_MSG, 0x00]) + chan.to_bytes(1, "little") + timestamp_bytes + msg.encode("utf-8")
        # Short pause - avoid stressing device with rapid commands
        await asyncio.sleep(0.3)
        # USB firmware typically doesn't respond to send_chan_msg - sends and stays silent. On timeout = assume OK.
        result = await self._client._send_and_wait(
            data, [EventType.OK, EventType.ERROR, EventType.MSG_SENT], timeout=3.0
        )
        if result.type == EventType.ERROR and result.payload.get("reason") == "timeout":
            return Event(EventType.OK, {})  # fire-and-forget, assume success
        return result

    async def send_msg(
        self, dst: "str | bytes | dict", msg: str, timestamp: Optional[int] = None, attempt: int = 0
    ) -> Event:
        """Send DM to contact. dst: pubkey hex (64 chars) or contact dict with public_key."""
        msg = msg[:220] if len(msg) > 220 else msg
        if timestamp is None:
            timestamp = int(time.time())
        if isinstance(dst, dict):
            dst_bytes = bytes.fromhex(dst.get("public_key", ""))[:6]
        elif isinstance(dst, str):
            dst_bytes = bytes.fromhex(dst)[:6]
        else:
            dst_bytes = dst[:6] if isinstance(dst, bytes) else b""
        if len(dst_bytes) < 6:
            return Event(EventType.ERROR, {"reason": "invalid_destination"})
        timestamp_bytes = timestamp.to_bytes(4, "little")
        data = (
            bytes([CMD_SEND_MSG, 0x00])
            + attempt.to_bytes(1, "little")
            + timestamp_bytes
            + dst_bytes
            + msg.encode("utf-8")
        )
        await asyncio.sleep(0.3)
        result = await self._client._send_and_wait(
            data, [EventType.MSG_SENT, EventType.OK, EventType.ERROR], timeout=5.0
        )
        if result.type == EventType.ERROR and result.payload.get("reason") == "timeout":
            return Event(EventType.MSG_SENT, {})
        return result

    async def get_msg(self, timeout: Optional[float] = 5.0) -> Event:
        """Request next pending message. Returns message event or NO_MORE_MSGS."""
        data = bytes([CMD_GET_MSG])
        return await self._client._send_and_wait(
            data,
            [
                EventType.CONTACT_MSG_RECV,
                EventType.CHANNEL_MSG_RECV,
                EventType.NO_MORE_MSGS,
                EventType.ERROR,
            ],
            timeout=timeout,
        )


class MeshCoreDirectClient:
    """
    Direct serial connection to MeshCore USB firmware.
    Bypasses appstart (0x01 0x03 mccli) which USB firmware does not support.
    """

    def __init__(self, port: str, baudrate: int = 115200, channel_secrets: Optional[dict] = None):
        self.port = port
        self.baudrate = baudrate
        self._transport = None
        self._protocol = None
        self._dispatcher = EventDispatcher()
        self._reader = MessageReader(self._dispatcher)
        self.commands = MeshCoreDirectCommands(self)
        self._channel_secrets = channel_secrets or {}
        self._connected = False
        self._poll_task = None
        self._pending_response = asyncio.Queue()
        self._response_event = None
        self._response_types = None
        self.self_info = {}
        self._rx_buffer = b""
        self._frame_expected_size = 0
        self._send_lock = asyncio.Lock()

    def get_contact_by_key_prefix(self, prefix: str):
        """No contact list without appstart - return None."""
        return None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._transport is not None

    def subscribe(self, event_type, callback, attribute_filters=None):
        return self._dispatcher.subscribe(event_type, callback, attribute_filters)

    def _handle_rx_data(self, data: bytes) -> None:
        """Parse 0x3e frames and feed payloads to MessageReader."""
        self._rx_buffer, self._frame_expected_size = self._parse_frames(
            self._rx_buffer + data, self._frame_expected_size
        )

    def _parse_frames(self, buf: bytes, frame_size: int) -> tuple[bytes, int]:
        """Parse frames from buffer, dispatch to reader. Returns (remaining_buf, next_frame_size)."""
        while True:
            if frame_size == 0:
                idx = buf.find(bytes([FRAME_RECV]))
                if idx < 0:
                    return buf, 0
                buf = buf[idx:]
                if len(buf) < 3:
                    return buf, 0
                frame_size = int.from_bytes(buf[1:3], "little", signed=False)
                if frame_size > 500:
                    buf = buf[1:]
                    frame_size = 0
                    continue

            if len(buf) < 3 + frame_size:
                return buf, frame_size

            payload = bytes(buf[3 : 3 + frame_size])
            buf = buf[3 + frame_size :]
            frame_size = 0

            asyncio.create_task(self._reader.handle_rx(bytearray(payload)))

    async def _on_serial_data(self, data: bytes) -> None:
        """Called when serial data received - runs in protocol callback."""
        self._handle_rx_data(data)

    async def _send_and_wait(
        self,
        data: bytes,
        expected_types: List[EventType],
        timeout: float = 5.0,
    ) -> Optional[Event]:
        """Send command and wait for response event."""
        future = asyncio.get_running_loop().create_future()

        def handler(event: Event):
            if not future.done() and event.type in expected_types:
                future.set_result(event)

        sub = self._dispatcher.subscribe(None, handler)
        async with self._send_lock:
            try:
                _send_frame(self._transport, data)
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                return Event(EventType.ERROR, {"reason": "timeout"})
            finally:
                sub.unsubscribe()

    async def _poll_messages_loop(self) -> None:
        """Periodically poll for messages (USB firmware may not send MESSAGES_WAITING)."""
        poll_interval = 2.5
        while self._connected and not shutting_down:
            try:
                result = await self.commands.get_msg(timeout=3.0)
                if result.type == EventType.CHANNEL_MSG_RECV or result.type == EventType.CONTACT_MSG_RECV:
                    logger.info(f"MeshCore message: {result.payload.get('text', '')[:60]!r}")
                if result.type == EventType.NO_MORE_MSGS or result.type == EventType.ERROR:
                    await asyncio.sleep(poll_interval)
                    continue
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Poll error: {e}")
                await asyncio.sleep(poll_interval)

    async def connect(self) -> bool:
        """Open serial and start message polling."""
        global _auto_fetch_task

        class SerialProtocol(asyncio.Protocol):
            def __init__(self, client):
                self.client = client

            def connection_made(self, transport):
                self.transport = transport
                if hasattr(transport, "serial") and transport.serial:
                    transport.serial.rts = False
                self.client._transport = transport
                self.client._connected = True

            def data_received(self, data):
                asyncio.create_task(self.client._on_serial_data(data))

            def connection_lost(self, exc):
                self.client._connected = False
                self.client._transport = None

        loop = asyncio.get_running_loop()
        try:
            await serial_asyncio.create_serial_connection(
                loop,
                lambda: SerialProtocol(self),
                self.port,
                baudrate=self.baudrate,
            )
        except Exception as e:
            logger.error(f"Serial connect failed: {e}")
            return False

        await asyncio.sleep(0.3)

        # Register channels for LOG_DATA decryption (USB firmware returns 0x88 instead of CHANNEL_MSG_RECV)
        for idx, secret_hex in self._channel_secrets.items():
            try:
                secret = bytes.fromhex(secret_hex)
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid channel_{idx}_secret (not hex): {e}")
                continue
            ch_hash = sha256(secret).hexdigest()[0:2]
            self._reader.channels[idx] = {
                "channel_idx": idx,
                "channel_name": f"channel_{idx}",
                "channel_secret": secret,
                "channel_hash": ch_hash,
            }

        await self._dispatcher.start()

        _auto_fetch_task = asyncio.create_task(self._poll_messages_loop())
        logger.info(f"Connected to MeshCore (direct serial) at {self.port}")
        return True

    async def disconnect(self) -> None:
        """Close serial connection."""
        global _auto_fetch_task
        self._connected = False
        if _auto_fetch_task:
            _auto_fetch_task.cancel()
            try:
                await _auto_fetch_task
            except asyncio.CancelledError:
                pass
            _auto_fetch_task = None
        await self._dispatcher.stop()
        if self._transport:
            self._transport.close()
            self._transport = None
        logger.info("MeshCore disconnected")


async def _tcp_poll_messages_loop(client) -> None:
    """Poll get_msg for TCP/WiFi firmware (may not push MESSAGES_WAITING)."""
    poll_interval = 2.5
    while client and client.is_connected and not shutting_down:
        try:
            result = await client.commands.get_msg(timeout=3.0)
            if result.type == EventType.CHANNEL_MSG_RECV or result.type == EventType.CONTACT_MSG_RECV:
                logger.info(f"MeshCore TCP message: {result.payload.get('text', '')[:60]!r}")
            if result.type == EventType.NO_MORE_MSGS or result.type == EventType.ERROR:
                await asyncio.sleep(poll_interval)
                continue
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"TCP poll error: {e}")
            await asyncio.sleep(poll_interval)


async def connect_meshcore(passed_config=None, force_connect=False):
    """
    Establish connection to MeshCore device.
    For serial: uses direct protocol (no appstart) for USB firmware.
    """
    global meshcore_client, config, matrix_rooms, _msg_subscription, _auto_fetch_task

    if shutting_down:
        return None

    if passed_config:
        config = passed_config
        if config and "matrix_rooms" in config:
            matrix_rooms = config["matrix_rooms"]

    if meshcore_client and not force_connect:
        return meshcore_client

    if meshcore_client:
        try:
            await meshcore_client.disconnect()
        except Exception as e:
            logger.warning(f"Error closing previous connection: {e}")
        meshcore_client = None

    if not config:
        logger.error("No configuration available.")
        return None

    mc_config = config["meshcore"]
    connection_type = mc_config.get("connection_type", "serial")
    attempts = 0

    while not shutting_down:
        try:
            if connection_type == "serial":
                port = mc_config.get("serial_port", "/dev/ttyUSB0")
                baudrate = mc_config.get("baudrate", 115200)
                channel_secrets = {}
                for i in range(5):
                    k = f"channel_{i}_secret"
                    if mc_config.get(k):
                        channel_secrets[i] = mc_config[k]
                logger.info(f"Connecting to MeshCore via direct serial {port}")
                meshcore_client = MeshCoreDirectClient(port, baudrate, channel_secrets=channel_secrets)
                ok = await meshcore_client.connect()
                if not ok:
                    meshcore_client = None
                    raise ConnectionError("Direct serial connect failed")
            elif connection_type == "tcp":
                from meshcore import MeshCore

                host = mc_config.get("host", "192.168.1.100")
                port = mc_config.get("port", 4000)
                logger.info(f"Connecting to MeshCore via TCP {host}:{port}")
                meshcore_client = await MeshCore.create_tcp(host, port)
                # Register channels for LOG_DATA decryption (WiFi firmware returns 0x88)
                if meshcore_client:
                    channel_secrets = {}
                    for i in range(5):
                        k = f"channel_{i}_secret"
                        if mc_config.get(k):
                            channel_secrets[i] = mc_config[k]
                    channels = meshcore_client._reader.channels
                    next_slot = 0
                    for idx, secret_hex in channel_secrets.items():
                        try:
                            secret = bytes.fromhex(secret_hex)
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Invalid channel_{idx}_secret (not hex): {e}")
                            continue
                        # SHA256 hash (MeshCore)
                        ch_hash = sha256(secret).hexdigest()[0:2]
                        channels[next_slot] = {
                            "channel_idx": idx,
                            "channel_name": f"channel_{idx}",
                            "channel_secret": secret,
                            "channel_hash": ch_hash,
                        }
                        logger.info(f"MeshCore channel {idx} configured: hash={ch_hash}")
                        next_slot += 1
                    # Heltec WiFi may use different hashes (e.g. 34, 00) – add fallbacks
                    for observed_hash in ("34", "00"):
                        for idx, secret_hex in channel_secrets.items():
                            try:
                                secret = bytes.fromhex(secret_hex)
                            except (ValueError, TypeError):
                                continue
                            channels[next_slot] = {
                                "channel_idx": idx,
                                "channel_name": f"channel_{idx}",
                                "channel_secret": secret,
                                "channel_hash": observed_hash,
                            }
                            logger.info(f"MeshCore channel {idx} fallback: hash={observed_hash}")
                            next_slot += 1
                    # DM decryption for RX_LOG_DATA payload_type 2 (TEXT_MSG)
                    await _setup_dm_key_store(meshcore_client, config)
            elif connection_type == "ble":
                from meshcore import MeshCore

                address = mc_config.get("ble_address")
                if not address:
                    logger.error("No BLE address provided.")
                    return None
                logger.info(f"Connecting to MeshCore via BLE {address}")
                meshcore_client = await MeshCore.create_ble(address)
            else:
                logger.error(f"Unknown connection type: {connection_type}")
                return None

            if meshcore_client is None:
                raise ConnectionError("MeshCore connect returned None")

            # Subscribe to incoming messages – create_task so handler does not block
            # (otherwise Matrix->MeshCore send waits for response but receive loop is blocked in matrix_relay)
            def _log_task_exc(t):
                if t.cancelled():
                    return
                exc = t.exception()
                if exc:
                    logger.error(f"MeshCore relay task failed: {exc}")

            async def on_message(event):
                t = asyncio.create_task(on_meshcore_message(event))
                t.add_done_callback(_log_task_exc)

            _msg_subscription = meshcore_client.subscribe(
                EventType.CONTACT_MSG_RECV, on_message
            )
            # TCP/WiFi: RX_LOG_DATA only – CHANNEL_MSG_RECV would duplicate same message
            # Serial/USB: both – firmware may send either
            if connection_type == "tcp":
                meshcore_client.subscribe(EventType.RX_LOG_DATA, on_message)
            else:
                meshcore_client.subscribe(EventType.CHANNEL_MSG_RECV, on_message)
                meshcore_client.subscribe(EventType.RX_LOG_DATA, on_message)

            # TCP poll: WiFi firmware pushes RX_LOG_DATA on RF receive.
            # Poll returns same messages repeatedly → duplicates. Enable only when push doesn't work.
            tcp_poll = mc_config.get("tcp_poll_enabled", False)
            global _tcp_poll_task
            if connection_type == "tcp" and meshcore_client and tcp_poll:
                _tcp_poll_task = asyncio.create_task(_tcp_poll_messages_loop(meshcore_client))
                logger.info("TCP message polling started")

            # Send DM to contacts so this device appears in their list (e.g. Tegaf Mobile)
            await _announce_dm_contacts(meshcore_client, config)

            name = meshcore_client.self_info.get("adv_name", "unknown") if meshcore_client.self_info else "unknown"
            logger.info(f"Connected to MeshCore: {name}")
            return meshcore_client

        except Exception as e:
            attempts += 1
            wait_time = min(attempts * 2, 30)
            logger.warning(f"Attempt #{attempts} failed. Retrying in {wait_time}s: {e}")
            await asyncio.sleep(wait_time)

    return None


async def on_meshcore_message(event):
    """Process incoming MeshCore message and relay to Matrix."""
    global config, matrix_rooms, meshcore_client

    payload = event.payload

    # RX_LOG_DATA: WiFi/TCP firmware returns raw RF data, reader decrypts to "message"
    if event.type == EventType.RX_LOG_DATA:
        pt = payload.get("payload_type")
        if pt == 2:
            logger.info("RX_LOG_DATA: payload_type=2 (DM) received, attempting decrypt")
        text = payload.get("message", "")
        # Derive channel from chan_hash – RX_LOG_DATA does not include channel_idx
        chan_hash = payload.get("chan_hash", "")
        chan_name = payload.get("chan_name", "")
        channel = 0  # default
        if meshcore_client and chan_hash:
            channels_list = getattr(meshcore_client._reader, "channels", None)
            if channels_list:
                for idx, c in enumerate(channels_list):
                    if isinstance(c, dict) and c.get("channel_hash") == chan_hash:
                        channel = c.get("channel_idx", idx)
                        break
                else:
                    # Fallback: chan_name may be "channel_1" or "meshcoregate"
                    if "1" in chan_name or "meshcoregate" in chan_name.lower():
                        channel = 1
                    logger.info(f"RX_LOG_DATA chan_hash={chan_hash} chan_name={chan_name!r} -> channel={channel} (hash not in list)")
            else:
                if "1" in chan_name or "meshcoregate" in chan_name.lower():
                    channel = 1
                logger.info(f"RX_LOG_DATA chan_hash={chan_hash} chan_name={chan_name!r} -> channel={channel} (no channels)")
        if text:
            logger.info(f"RX_LOG_DATA channel={channel} chan_hash={chan_hash} chan_name={chan_name!r} text={text[:40]!r}")
        sender = "?"
        path_len = payload.get("path_len", 1)  # 0 = likely echo of our broadcast
        is_meshcore_dm = False
        if not text:
            pt = payload.get("payload_type")
            if pt == 2:
                # TEXT_MSG/DM – try decrypt with meshcoredecoder
                text, sender = _try_decrypt_rx_log_dm(payload)
                if text:
                    is_meshcore_dm = True
                    logger.info(f"RX_LOG_DATA: decrypted DM from {sender}: {text[:40]!r}")
                else:
                    logger.warning("RX_LOG_DATA: payload_type=2 (DM) decrypt failed – check node_public_key/node_private_key in config")
            elif pt == 0x05:  # channel msg but failed to decrypt
                ch = payload.get("chan_hash", "?")
                logger.warning(
                    f"RX_LOG_DATA: message from channel hash={ch} - check channel_0_secret in config matches other devices"
                )
            elif pt == 4 and _dm_key_store and not _get_contacts_list(config.get("meshcore_dm", {})):
                # Advert: learn peer pubkey (only when we have no contacts in config)
                adv_key = payload.get("adv_key", "")
                if adv_key and len(adv_key) == 64 and adv_key not in (p.upper() for p in _dm_key_store.peer_public_keys):
                    _dm_key_store.add_peer_public_key(adv_key)
                    logger.debug(f"MeshCore DM: learned peer from Advert {adv_key[:16]}...")
            elif pt is not None:
                logger.debug(f"RX_LOG_DATA: payload_type={pt}, no message")
    else:
        text = payload.get("text", "")
        path_len = payload.get("path_len", 1)
        if event.type == EventType.CHANNEL_MSG_RECV:
            channel = payload.get("channel_idx", 0)
            sender = payload.get("pubkey_prefix", "?") or "?"
            is_meshcore_dm = False
        else:
            # CONTACT_MSG_RECV = MeshCore DM (device-to-device)
            channel = 0
            sender = payload.get("pubkey_prefix", "?") or "?"
            is_meshcore_dm = payload.get("type") == "PRIV" or event.type == EventType.CONTACT_MSG_RECV

    if not text:
        return

    if _DEBUG:
        content = text.split(":", 1)[1].strip() if ":" in text else text
        h = sha256(content.encode("utf-8")).hexdigest()[:16] if content else ""
        logger.info(
            f"[DEBUG] RECV event={event.type.name} path_len={path_len} text={text[:50]!r} "
            f"content_hash={h} sent_set={list(_recently_sent_to_meshcore.keys())[:3]}"
        )

    # Anti-loop: do not relay back messages we sent from Matrix
    if _was_recently_sent_to_meshcore(text):
        logger.info(f"Skip relay (loop): {text[:60]!r}")
        if _DEBUG:
            logger.info(f"[DEBUG] LOOP_MATCH: received {text[:40]!r} matched sent set")
        return

    # path_len 0 = Heltec received own broadcast (radio echo) – skip for channel msgs only.
    # DM from contact (is_meshcore_dm, sender=DF) is not our echo – relay it.
    if event.type == EventType.RX_LOG_DATA and path_len == 0 and not is_meshcore_dm:
        logger.info(f"Skip relay (path_len=0, own echo): {text[:60]!r}")
        return

    # Deduplication: hold lock until relay complete – serialization prevents duplicates
    async with _relay_lock:
        if _was_recently_relayed(text):
            logger.info(f"Skip relay (duplicate): {text[:60]!r}")
            if _DEBUG:
                logger.info(f"[DEBUG] DEDUP_MATCH: content already relayed")
            return
        _mark_relayed(text)
        if _DEBUG:
            logger.info(f"[DEBUG] RELAY: sending to Matrix {text[:40]!r}")

        # Skip messages from our device (echo of our broadcast) - only relevant for CONTACT_MSG
        if sender != "?" and meshcore_client and meshcore_client.self_info:
            pk = meshcore_client.self_info.get("public_key", "")
            # sender is pubkey_prefix (typically 2 hex chars); match against our pubkey
            if pk and (sender or "").lower() and pk.lower().startswith((sender or "").lower()):
                logger.info(f"Skip relay (own device): {text[:60]!r}")
                return

        if event.type == EventType.RX_LOG_DATA:
            logger.info(f"MeshCore message (LOG): {text[:60]!r}")
        elif is_meshcore_dm:
            logger.info(f"MeshCore DM from {sender}: {text[:60]!r}")

        # MeshCore DM: relay to meshcore_dm destination (room and/or recipients)
        meshcore_dm_cfg = config.get("meshcore_dm", {})
        if is_meshcore_dm:
            if not meshcore_dm_cfg.get("enabled"):
                logger.debug("MeshCore DM: disabled in config, skipping")
                return
            # Will relay to meshcore_dm.room_id and/or meshcore_dm.recipients
        else:
            # Channel message: require channel mapping
            channel_mapped = any(
                r.get("meshcore_channel", r.get("meshtastic_channel")) == channel
                for r in matrix_rooms
            ) or any(
                r.get("meshcore_channel", 0) == channel
                for r in config.get("matrix_dms", {}).get("recipients", [])
            )
            if not channel_mapped:
                logger.info(f"Skip relay: channel={channel} not mapped (rooms want {[r.get('meshcore_channel', r.get('meshtastic_channel')) for r in matrix_rooms]})")
                return

        longname = get_longname(sender) or sender
        shortname = get_shortname(sender) or sender
        contact = meshcore_client.get_contact_by_key_prefix(sender) if meshcore_client and sender != "?" else None
        if contact:
            adv_name = contact.get("adv_name")
            if adv_name:
                longname = adv_name
                shortname = adv_name[:8] if len(adv_name) > 8 else adv_name
                save_longname(sender, longname)
                save_shortname(sender, shortname)

        meshnet_name = config.get("meshcore", {}).get("meshnet_name", "MeshCore")

        from mcmgate.matrix_utils import get_matrix_prefix, matrix_relay, matrix_relay_dm

        prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)
        formatted_message = f"{prefix}{text}"

        if is_meshcore_dm:
            # MeshCore DM → Matrix: room(s) and/or recipients
            # contact_rooms: map pubkey → room_id (different contacts → different rooms)
            # room_id: default when no contact-specific mapping
            contact_rooms = meshcore_dm_cfg.get("contact_rooms", {}) or {}
            default_room = meshcore_dm_cfg.get("room_id")
            dm_recipients = meshcore_dm_cfg.get("recipients", [])

            # Resolve room(s) for this sender (source_hash = first byte of pubkey)
            # contact_rooms: pubkey -> room_id or [room_id, ...]
            rooms_to_send = []
            sender_upper = (sender or "?").upper()
            for item in _get_contacts_list(meshcore_dm_cfg):
                pk = (item.get("pubkey", item) if isinstance(item, dict) else str(item))
                pk = (pk or "").strip()
                if len(pk) >= 2 and pk[:2].upper() == sender_upper:
                    rid = contact_rooms.get(pk) or contact_rooms.get(pk.upper()) or contact_rooms.get(pk.lower())
                    if rid:
                        rooms_to_send.extend(rid if isinstance(rid, list) else [rid])
                    break
            if not rooms_to_send and default_room:
                rooms_to_send = [default_room]
            rooms_to_send = list(dict.fromkeys(rooms_to_send))  # dedupe

            logger.info(
                f"Relaying MeshCore DM from {longname} to Matrix "
                f"(rooms={len(rooms_to_send)}, recipients={len(dm_recipients)})"
            )
            for dm_room_id in rooms_to_send:
                if dm_room_id:
                    await matrix_relay(
                        dm_room_id,
                        formatted_message,
                        longname,
                        shortname,
                        meshnet_name,
                        "TEXT_MESSAGE_APP",
                        meshtastic_id=None,
                        meshtastic_text=text,
                    )
            for user_id in dm_recipients:
                if isinstance(user_id, dict):
                    user_id = user_id.get("user_id", "")
                if user_id:
                    await matrix_relay_dm(user_id, formatted_message, longname, shortname, meshnet_name, meshtastic_text=text)
        else:
            # Channel message → Matrix rooms and optional matrix_dms recipients
            room_count = len([r for r in matrix_rooms if r.get("meshcore_channel", r.get("meshtastic_channel")) == channel])
            dm_recipients = [r for r in config.get("matrix_dms", {}).get("recipients", []) if r.get("meshcore_channel", 0) == channel]
            logger.info(f"Relaying MeshCore message from {longname} to Matrix ({room_count} rooms, {len(dm_recipients)} DMs)")

            for room in matrix_rooms:
                if room.get("meshcore_channel", room.get("meshtastic_channel")) == channel:
                    logger.info(f"Sending to room {room['id']}")
                    await matrix_relay(
                        room["id"],
                        formatted_message,
                        longname,
                        shortname,
                        meshnet_name,
                        "TEXT_MESSAGE_APP",
                        meshtastic_id=None,
                        meshtastic_text=text,
                    )

            for rec in dm_recipients:
                user_id = rec.get("user_id")
                if user_id:
                    await matrix_relay_dm(user_id, formatted_message, longname, shortname, meshnet_name, meshtastic_text=text)


def send_channel_message(interface, text: str, channel: int = 0, **kwargs) -> None:
    """Send text to MeshCore channel. Used by message queue."""
    async def _send():
        result = await interface.commands.send_chan_msg(channel, text)
        if result.type == EventType.ERROR:
            logger.error(f"Failed to send to channel {channel}: {result.payload}")
        return result

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.ensure_future(_send())
        else:
            return loop.run_until_complete(_send())
    except RuntimeError:
        return asyncio.run(_send())


async def send_channel_message_async(interface, text: str, channel: int = 0) -> bool:
    """Async version for message queue processor."""
    if not interface:
        return False
    result = await interface.commands.send_chan_msg(channel, text)
    return result.type != EventType.ERROR


async def check_connection():
    """Periodic connection health check."""
    global meshcore_client, shutting_down

    while not shutting_down:
        if meshcore_client:
            if not meshcore_client.is_connected:
                logger.error("MeshCore connection lost. Reconnecting...")
                meshcore_client = None
                await connect_meshcore(force_connect=True)
        await asyncio.sleep(60)

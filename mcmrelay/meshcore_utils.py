"""
MeshCore connection and message handling for MCMRelay.
Uses direct serial protocol (no appstart) for USB firmware compatibility.
"""

import asyncio
import os
import time
import unicodedata
from typing import List, Optional

_DEBUG = os.environ.get("MCMRELAY_DEBUG") == "1"

import serial_asyncio_fast as serial_asyncio
from hashlib import sha256

from meshcore import EventType
from meshcore.events import Event, EventDispatcher
from meshcore.reader import MessageReader

from mcmrelay.db_utils import get_longname, get_shortname, save_longname, save_shortname
from mcmrelay.log_utils import get_logger

config = None
matrix_rooms: List[dict] = []

logger = get_logger(name="MeshCore")

# Anti-loop: messages we sent from Matrix -> MeshCore (do not relay back)
_recently_sent_to_meshcore: dict[str, float] = {}  # normalized text -> timestamp
_recently_sent_hashes: dict[str, float] = {}  # hash(content) -> timestamp (more robust)
_SENT_EXPIRE_SEC = 120

# Deduplication: content hash – Tegaf Mobile sends same message 2x
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


def _was_recently_sent_to_meshcore(text: str) -> bool:
    """Was this message recently sent from Matrix to MeshCore?"""
    now = time.time()
    key = _normalize_text(text)
    if not key:
        return False
    # Content after first ":" – "Tegaf Gate: Miroslav: text" → "Miroslav: text"
    content = key.split(":", 1)[1].strip() if ":" in key else key
    # Direct match: received content == sent message (echo from mesh)
    if content and content in _recently_sent_to_meshcore:
        if now - _recently_sent_to_meshcore[content] <= _SENT_EXPIRE_SEC:
            return True
    # Hash-based: content "Miroslav: text" or "Tegaf Gate: Miroslav: text" → content
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


def _content_hash(text: str) -> str:
    """Hash of normalized content for deduplication."""
    key = _normalize_text(text)
    if not key:
        return ""
    # Use content (after first ": ") – "Tegaf Mobile: xcvcxv" → "xcvcxv"
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
                    for idx, secret_hex in channel_secrets.items():
                        try:
                            secret = bytes.fromhex(secret_hex)
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Invalid channel_{idx}_secret (not hex): {e}")
                            continue
                        ch_hash = sha256(secret).hexdigest()[0:2]
                        meshcore_client._reader.channels[idx] = {
                            "channel_idx": idx,
                            "channel_name": f"channel_{idx}",
                            "channel_secret": secret,
                            "channel_hash": ch_hash,
                        }
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

            # Subscribe to incoming messages
            async def on_message(event):
                await on_meshcore_message(event)

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

    # RX_LOG_DATA: USB firmware returns raw RF data, reader decrypts to "message"
    if event.type == EventType.RX_LOG_DATA:
        text = payload.get("message", "")
        channel = 0  # assume channel 0
        sender = "?"
        path_len = payload.get("path_len", 1)  # 0 = likely echo of our broadcast
        if not text:
            pt = payload.get("payload_type")
            if pt == 0x05:  # channel msg but failed to decrypt
                ch = payload.get("chan_hash", "?")
                logger.warning(
                    f"RX_LOG_DATA: message from channel hash={ch} - check channel_0_secret in config matches other devices"
                )
            elif pt is not None:
                logger.debug(f"RX_LOG_DATA: payload_type={pt} (5=channel), no message")
    else:
        text = payload.get("text", "")
        path_len = 1  # CHANNEL_MSG_RECV has no path_len
        if event.type == EventType.CHANNEL_MSG_RECV:
            channel = payload.get("channel_idx", 0)
            sender = payload.get("pubkey_prefix", "?") or "?"
        else:
            channel = 0
            sender = payload.get("pubkey_prefix", "?") or "?"

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

    # path_len 0 = Heltec received own broadcast (radio echo) – always skip
    if event.type == EventType.RX_LOG_DATA and path_len == 0:
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
            our_prefix = pk[:12].lower() if len(pk) >= 12 else ""
            if our_prefix and sender.lower() == our_prefix:
                logger.info(f"Skip relay (own device): {text[:60]!r}")
                return

        if event.type == EventType.RX_LOG_DATA:
            logger.info(f"MeshCore message (LOG): {text[:60]!r}")

        channel_mapped = any(
            r.get("meshcore_channel", r.get("meshtastic_channel")) == channel
            for r in matrix_rooms
        )
        if not channel_mapped:
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

        from mcmrelay.matrix_utils import get_matrix_prefix, matrix_relay

        prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)
        formatted_message = f"{prefix}{text}"

        logger.info(f"Relaying MeshCore message from {longname} to Matrix")

        for room in matrix_rooms:
            if room.get("meshcore_channel", room.get("meshtastic_channel")) == channel:
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

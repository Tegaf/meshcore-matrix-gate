"""Main MCMGate loop - MeshCore <-> Matrix bridge."""
import asyncio
import logging
import signal
import sys

from nio import ReactionEvent, RoomMessageEmote, RoomMessageNotice, RoomMessageText
from nio.events.room_events import MegolmEvent, RoomMemberEvent

from mcmgate import __version__
from mcmgate.db_utils import initialize_database
from mcmgate.log_utils import get_logger
from mcmgate.matrix_utils import connect_matrix, join_matrix_room, on_room_message, on_invite, _on_megolm_event
from mcmgate.meshcore_utils import connect_meshcore, check_connection
from mcmgate.message_queue import get_message_queue, start_message_queue, stop_message_queue

logger = get_logger(name="MCMGate")

# Suppress nio "Timed out, sleeping" warnings (normal retry behavior)
logging.getLogger("nio.client.async_client").setLevel(logging.ERROR)

REJOIN_INTERVAL_SEC = 300  # 5 min – recovery when room freezes


async def _periodic_matrix_rejoin(client, rooms, shutdown_event):
    """Periodically re-join Matrix rooms – recovery when room freezes."""
    await asyncio.sleep(60)  # first rejoin after 1 min
    while not shutdown_event.is_set():
        try:
            for room in rooms:
                await join_matrix_room(client, room)
            logger.info("Matrix: periodic re-join done")
        except Exception as e:
            logger.warning(f"Matrix re-join error: {e}")
        await asyncio.sleep(REJOIN_INTERVAL_SEC)


async def main(cfg):
    from mcmgate import meshcore_utils, matrix_utils
    meshcore_utils.config = cfg
    matrix_utils.config = cfg

    matrix_rooms = cfg["matrix_rooms"]
    meshcore_utils.event_loop = asyncio.get_event_loop()

    # Rooms to join: matrix_rooms + meshcore_dm room(s) + contact_rooms
    rooms_to_join = list(matrix_rooms)
    meshcore_dm_cfg = cfg.get("meshcore_dm", {})
    if meshcore_dm_cfg.get("enabled") and meshcore_dm_cfg.get("room_id"):
        rooms_to_join.append({"id": meshcore_dm_cfg["room_id"]})
    for rid in meshcore_dm_cfg.get("contact_rooms", {}).values():
        for r in (rid if isinstance(rid, list) else [rid]):
            if r and not any(x.get("id") == r for x in rooms_to_join):
                rooms_to_join.append({"id": r})
    for m2m_room in meshcore_dm_cfg.get("matrix_to_meshcore_only", {}).keys():
        if m2m_room and not any(x.get("id") == m2m_room for x in rooms_to_join):
            rooms_to_join.append({"id": m2m_room})

    initialize_database()

    message_delay = cfg.get("meshcore", {}).get("message_delay", 2.2)
    start_message_queue(message_delay=message_delay)

    # Connect MeshCore (async)
    meshcore_utils.meshcore_client = await connect_meshcore(passed_config=cfg)
    if not meshcore_utils.meshcore_client:
        logger.error("Failed to connect to MeshCore")
        return 1

    # Connect Matrix
    matrix_client = await connect_matrix(passed_config=cfg)
    if not matrix_client:
        logger.error("Failed to connect to Matrix")
        return 1

    for room in rooms_to_join:
        await join_matrix_room(matrix_client, room)

    # Initial sync (mmrelay-style) – populates client.rooms for room_send
    sync_resp = await matrix_client.sync(timeout=30000, full_state=True)
    if hasattr(sync_resp, "next_batch") and sync_resp.next_batch:
        logger.info("Matrix initial sync done, rooms ready")

    # Conduit sync may not populate client.rooms – use joined_rooms() API and add rooms manually
    from nio import MatrixRoom
    from nio.responses import JoinedRoomsError
    jr = await matrix_client.joined_rooms()
    if not isinstance(jr, JoinedRoomsError) and hasattr(jr, "joined_rooms"):
        for rid in jr.joined_rooms:
            if rid not in matrix_client.rooms:
                matrix_client.rooms[rid] = MatrixRoom(rid, matrix_client.user_id, encrypted=True)
                await matrix_client.joined_members(rid)
        logger.info(f"Matrix joined rooms: {jr.joined_rooms[:5]}" + ("..." if len(jr.joined_rooms) > 5 else ""))

    matrix_client.add_event_callback(
        on_room_message,
        (RoomMessageText, RoomMessageNotice, RoomMessageEmote),
    )
    matrix_client.add_event_callback(on_room_message, ReactionEvent)
    matrix_client.add_event_callback(_on_megolm_event, MegolmEvent)
    matrix_client.add_event_callback(on_invite, RoomMemberEvent)

    shutdown_event = asyncio.Event()

    meshcore_utils.shutting_down = False
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: shutdown_event.set())

    get_message_queue().ensure_processor_started()
    asyncio.create_task(check_connection())
    # Periodic re-join disabled – caused M_BAD_JSON with Conduit; initial join is enough
    # asyncio.create_task(_periodic_matrix_rejoin(matrix_client, rooms_to_join, shutdown_event))

    logger.info("MCMGate running. Matrix <-> MeshCore bridge active.")

    try:
        while not shutdown_event.is_set():
            try:
                sync_task = asyncio.create_task(matrix_client.sync_forever(timeout=30000))
                shutdown_task = asyncio.create_task(shutdown_event.wait())
                done, _ = await asyncio.wait(
                    [sync_task, shutdown_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if shutdown_event.is_set():
                    sync_task.cancel()
                    try:
                        await sync_task
                    except asyncio.CancelledError:
                        pass
                    break
            except Exception as e:
                logger.error(f"Matrix sync error: {e}")
                await asyncio.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        meshcore_utils.shutting_down = True
        stop_message_queue()
        await matrix_client.close()
        if meshcore_utils.meshcore_client:
            try:
                await meshcore_utils.meshcore_client.disconnect()
            except Exception as e:
                logger.warning(f"MeshCore disconnect: {e}")
        logger.info("Shutdown complete")

    return 0


def run_main(args):
    logger.info(f"Starting MCMGate v{__version__}")
    from mcmgate.config import load_config
    cfg = load_config(args=args)
    if not cfg or "matrix" not in cfg or "meshcore" not in cfg or "matrix_rooms" not in cfg:
        logger.error("Invalid config. Need matrix, meshcore, matrix_rooms.")
        return 1
    try:
        return asyncio.run(main(cfg))
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1

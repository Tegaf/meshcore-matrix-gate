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
from mcmgate.matrix_utils import connect_matrix, join_matrix_room, on_room_message, _on_megolm_event
from mcmgate.meshcore_utils import connect_meshcore, check_connection
from mcmgate.message_queue import get_message_queue, start_message_queue, stop_message_queue

logger = get_logger(name="MCMGate")

# Suppress nio "Timed out, sleeping" warnings (normal retry behavior)
logging.getLogger("nio.client.async_client").setLevel(logging.ERROR)


async def main(cfg):
    from mcmgate import meshcore_utils, matrix_utils
    meshcore_utils.config = cfg
    matrix_utils.config = cfg

    matrix_rooms = cfg["matrix_rooms"]
    meshcore_utils.event_loop = asyncio.get_event_loop()

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

    for room in matrix_rooms:
        await join_matrix_room(matrix_client, room)

    matrix_client.add_event_callback(
        on_room_message,
        (RoomMessageText, RoomMessageNotice, RoomMessageEmote),
    )
    matrix_client.add_event_callback(on_room_message, ReactionEvent)
    matrix_client.add_event_callback(_on_megolm_event, MegolmEvent)

    shutdown_event = asyncio.Event()

    meshcore_utils.shutting_down = False
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: shutdown_event.set())

    get_message_queue().ensure_processor_started()
    asyncio.create_task(check_connection())

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

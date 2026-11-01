"""Message queue with async send support for MeshCore."""
import asyncio
import os
import time
from queue import Empty, Queue
from dataclasses import dataclass
from typing import Callable, Optional

from meshcore import EventType

from mcmgate.log_utils import get_logger

_DEBUG = os.environ.get("MCMGATE_DEBUG") == "1"

logger = get_logger(name="MessageQueue")
DEFAULT_MESSAGE_DELAY = 2.2
MAX_QUEUE_SIZE = 100


@dataclass
class QueuedMessage:
    timestamp: float
    send_function: Callable
    args: tuple
    kwargs: dict
    description: str
    mapping_info: Optional[dict] = None


class MessageQueue:
    def __init__(self):
        self._queue = Queue()
        self._processor_task = None
        self._running = False
        self._last_send_time = 0.0
        self._message_delay = DEFAULT_MESSAGE_DELAY
        self._mesh_send_lock: asyncio.Lock | None = None

    def start(self, message_delay=DEFAULT_MESSAGE_DELAY):
        self._message_delay = max(2.0, message_delay)
        self._running = True
        logger.info(f"Message queue started with {self._message_delay}s delay")

    def stop(self):
        self._running = False
        if self._processor_task:
            self._processor_task.cancel()
        logger.info("Message queue stopped")

    def enqueue(self, send_function, *args, description="", mapping_info=None, **kwargs) -> bool:
        if not self._running:
            return False
        if self._queue.qsize() >= MAX_QUEUE_SIZE:
            logger.warning(f"Queue full, dropping: {description}")
            return False
        self._queue.put(QueuedMessage(
            timestamp=time.time(),
            send_function=send_function,
            args=args,
            kwargs=kwargs,
            description=description,
            mapping_info=mapping_info,
        ))
        return True

    def get_queue_size(self):
        return self._queue.qsize()

    def ensure_processor_started(self):
        if self._running and self._processor_task is None:
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    if self._mesh_send_lock is None:
                        self._mesh_send_lock = asyncio.Lock()
                    self._processor_task = loop.create_task(self._process_queue())
            except RuntimeError:
                pass

    async def _run_queued_send(self, current_message: QueuedMessage) -> None:
        """Run one mesh send (may take tens of seconds with delivery retry). Serialized by lock."""
        assert self._mesh_send_lock is not None
        async with self._mesh_send_lock:
            for _ in range(120):
                try:
                    from mcmgate.meshcore_utils import meshcore_client

                    if meshcore_client and meshcore_client.is_connected:
                        break
                except Exception:
                    pass
                await asyncio.sleep(1.0)
            else:
                logger.error(f"MeshCore not connected, dropping: {current_message.description}")
                return

            if self._last_send_time > 0:
                elapsed = time.time() - self._last_send_time
                if elapsed < self._message_delay:
                    await asyncio.sleep(self._message_delay - elapsed)

            mi = current_message.mapping_info or {}
            if txt := mi.get("matrix_sent_text"):
                from mcmgate.meshcore_utils import register_sent_to_meshcore

                register_sent_to_meshcore(txt)
                if _DEBUG:
                    logger.info(f"[DEBUG] QUEUE: registering BEFORE send {txt[:40]!r}")

            try:
                result = current_message.send_function(
                    *current_message.args, **current_message.kwargs
                )
                if asyncio.iscoroutine(result):
                    result = await result
                self._last_send_time = time.time()
                if result is not None and hasattr(result, "type"):
                    if result.type == EventType.ERROR:
                        logger.error(
                            f"MeshCore send failed: {result.payload} "
                            f"(msg: {current_message.description})"
                        )
                    elif result.type in (EventType.OK, EventType.MSG_SENT):
                        logger.info(f"MeshCore send OK: {current_message.description}")
                elif result is None:
                    logger.warning(
                        f"MeshCore send without ACK (delivery report may have been posted): "
                        f"{current_message.description}"
                    )
            except Exception as e:
                logger.error(f"Error sending: {e}")

    async def _process_queue(self):
        while self._running:
            try:
                try:
                    current_message = self._queue.get_nowait()
                except Empty:
                    await asyncio.sleep(0.1)
                    continue

                asyncio.create_task(self._run_queued_send(current_message))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Queue processor error: {e}")
                await asyncio.sleep(1.0)


_message_queue = MessageQueue()


def get_message_queue():
    return _message_queue


def queue_message(send_function, *args, description="", mapping_info=None, **kwargs) -> bool:
    return _message_queue.enqueue(
        send_function, *args, description=description, mapping_info=mapping_info, **kwargs
    )


def start_message_queue(message_delay=DEFAULT_MESSAGE_DELAY):
    _message_queue.start(message_delay)


def stop_message_queue():
    _message_queue.stop()

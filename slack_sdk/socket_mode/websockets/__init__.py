import asyncio
import logging
from asyncio import Future
from logging import Logger
from asyncio import Queue
from typing import Union, Optional, List, Callable, Awaitable

import websockets
from websockets.client import WebSocketClientProtocol

from slack_sdk.socket_mode.async_client import AsyncBaseSocketModeClient
from slack_sdk.socket_mode.async_listeners import (
    AsyncWebSocketMessageListener,
    AsyncSocketModeRequestListener,
)
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.web.async_client import AsyncWebClient


class SocketModeClient(AsyncBaseSocketModeClient):
    logger: Logger
    web_client: AsyncWebClient
    app_token: str
    wss_uri: Optional[str]
    auto_reconnect_enabled: bool
    message_queue: Queue
    message_listeners: List[
        Union[
            AsyncWebSocketMessageListener,
            Callable[
                ["AsyncBaseSocketModeClient", dict, Optional[str]], Awaitable[None]
            ],
        ]
    ]
    socket_mode_request_listeners: List[
        Union[
            AsyncSocketModeRequestListener,
            Callable[["AsyncBaseSocketModeClient", SocketModeRequest], Awaitable[None]],
        ]
    ]

    message_receiver: Optional[Future]
    message_processor: Future

    ping_interval: float
    current_session: Optional[WebSocketClientProtocol]
    current_session_monitor: Optional[Future]

    def __init__(
        self,
        app_token: str,
        logger: Optional[Logger] = None,
        web_client: Optional[AsyncWebClient] = None,
        auto_reconnect_enabled: bool = True,
        ping_interval: float = 10,
    ):
        self.app_token = app_token
        self.logger = logger or logging.getLogger(__name__)
        self.web_client = web_client or AsyncWebClient()
        self.auto_reconnect_enabled = auto_reconnect_enabled
        self.ping_interval = ping_interval
        self.wss_uri = None
        self.message_queue = Queue()
        self.message_listeners = []
        self.socket_mode_request_listeners = []
        self.current_session = None
        self.current_session_monitor = None

        self.message_receiver = None
        self.message_processor = asyncio.ensure_future(self.process_messages())

    async def monitor_current_session(self) -> None:
        while True:
            await asyncio.sleep(self.ping_interval)
            try:
                if self.auto_reconnect_enabled and (
                    self.current_session is None or self.current_session.closed
                ):
                    self.logger.info(
                        "The session seems to be already closed. Going to reconnect..."
                    )
                    await self.connect_to_new_endpoint()
            except Exception as e:
                self.logger.error(
                    "Failed to check the current session or reconnect to the server "
                    f"(error: {type(e).__name__}, message: {e})"
                )

    async def receive_messages(self) -> None:
        consecutive_error_count = 0
        while True:
            try:
                message = await self.current_session.recv()
                if message is not None:
                    if isinstance(message, bytes):
                        message = message.decode("utf-8")
                    if self.logger.level <= logging.DEBUG:
                        self.logger.debug(f"Received message: {message}")
                    await self.enqueue_message(message)
                consecutive_error_count = 0
            except Exception as e:
                consecutive_error_count += 1
                self.logger.error(
                    f"Failed to receive or enqueue a message: {type(e).__name__}, {e}"
                )
                if isinstance(e, websockets.ConnectionClosedError):
                    await asyncio.sleep(self.ping_interval)
                else:
                    await asyncio.sleep(consecutive_error_count)

    async def connect(self):
        if self.wss_uri is None:
            self.wss_uri = await self.issue_new_wss_url()
        old_session: Optional[
            WebSocketClientProtocol
        ] = None if self.current_session is None else self.current_session
        # NOTE: websockets does not support proxy settings
        self.current_session = await websockets.connect(
            uri=self.wss_uri, ping_interval=self.ping_interval,
        )
        self.logger.info("A new session has been established")

        old_current_session_monitor = self.current_session_monitor
        self.current_session_monitor = asyncio.ensure_future(
            self.monitor_current_session()
        )

        old_message_receiver = self.message_receiver
        self.message_receiver = asyncio.ensure_future(self.receive_messages())

        if old_session is not None:
            await old_session.close()
            if old_current_session_monitor is not None:
                old_current_session_monitor.cancel()
            if old_message_receiver is not None:
                old_message_receiver.cancel()
            self.logger.info("The old session has been abandoned")

    async def disconnect(self):
        if self.current_session is not None:
            await self.current_session.close()

    async def send_message(self, message: str):
        if self.logger.level <= logging.DEBUG:
            self.logger.debug(f"Sending a message: {message}")
        await self.current_session.send(message)

    async def close(self):
        self.disconnect()
        self.message_processor.cancel()
        if self.current_session_monitor is not None:
            self.current_session_monitor.cancel()
        if self.message_receiver is not None:
            self.message_receiver.cancel()

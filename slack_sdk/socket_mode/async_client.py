import asyncio
import json
import logging
from asyncio import Queue
from asyncio.futures import Future
from logging import Logger
from typing import Dict, Union, Any, Optional, List, Callable, Awaitable

from slack_sdk.errors import SlackApiError
from slack_sdk.socket_mode.async_listeners import (
    AsyncWebSocketMessageListener,
    AsyncSocketModeRequestListener,
)
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient


class AsyncBaseSocketModeClient:
    logger: Logger
    web_client: AsyncWebClient
    app_token: str
    wss_uri: str
    auto_reconnect_enabled: bool
    web_socket_message_queue: Queue
    web_socket_message_listeners: List[
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

    async def issue_new_wss_url(self) -> str:
        try:
            response = await self.web_client.apps_connections_open(
                app_token=self.app_token
            )
            return response["url"]
        except SlackApiError as e:
            self.logger.error(f"Failed to retrieve Socket Mode URL: {e}")
            raise e

    async def connect(self):
        raise NotImplementedError()

    async def disconnect(self):
        raise NotImplementedError()

    async def connect_to_new_endpoint(self):
        self.wss_uri = await self.issue_new_wss_url()
        await self.connect()

    async def close(self):
        self.disconnect()

    async def send_web_socket_message(self, message: str):
        raise NotImplementedError()

    async def send_socket_mode_response(
        self, response: Union[Dict[str, Any], SocketModeResponse]
    ):
        if isinstance(response, SocketModeResponse):
            await self.send_web_socket_message(json.dumps(response.to_dict()))
        else:
            await self.send_web_socket_message(json.dumps(response))

    async def enqueue_web_socket_message(self, message: str):
        await self.web_socket_message_queue.put(message)
        if self.logger.level <= logging.DEBUG:
            queue_size = self.web_socket_message_queue.qsize()
            self.logger.debug(
                f"A new message enqueued (current queue size: {queue_size})"
            )

    async def process_web_socket_messages(self):
        while True:
            try:
                await self.process_web_socket_message()
            except Exception as e:
                self.logger.exception(f"Failed to process a message: {e}")

    async def process_web_socket_message(self):
        raw_message = await self.web_socket_message_queue.get()
        if raw_message is not None and raw_message.startswith("{"):
            message: dict = json.loads(raw_message)
            _: Future[None] = asyncio.ensure_future(
                self.run_web_socket_message_listeners(message, raw_message)
            )

    async def run_web_socket_message_listeners(
        self, message: dict, raw_message: str
    ) -> None:
        type, envelope_id = message.get("type"), message.get("envelope_id")
        if self.logger.level <= logging.DEBUG:
            self.logger.debug(
                f"Message processing started (type: {type}, envelope_id: {envelope_id})"
            )
        try:
            if message.get("type") == "disconnect":
                await self.connect_to_new_endpoint()
                return

            for listener in self.web_socket_message_listeners:
                try:
                    await listener(self, message, raw_message)
                except Exception as e:
                    self.logger.exception(f"Failed to run a message listener: {e}")

            if len(self.socket_mode_request_listeners) > 0:
                request = SocketModeRequest.from_dict(message)
                if request is not None:
                    for listener in self.socket_mode_request_listeners:
                        try:
                            await listener(self, request)
                        except Exception as e:
                            self.logger.exception(f"Failed to run a request listener: {e}")
        except Exception as e:
            self.logger.exception(f"Failed to run message listeners: {e}")
        finally:
            if self.logger.level <= logging.DEBUG:
                self.logger.debug(
                    f"Message processing completed (type: {type}, envelope_id: {envelope_id})"
                )

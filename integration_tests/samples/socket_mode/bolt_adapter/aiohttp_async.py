# ------------------
# Only for running this script here
import sys
from os.path import dirname

sys.path.insert(1, f"{dirname(__file__)}/../../../..")
# ------------------

import asyncio
import logging
import os
from typing import Optional
from time import time
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.aiohttp import SocketModeClient

from slack_bolt.app.async_app import AsyncApp
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_bolt.response import BoltResponse


class AsyncSocketModeAdapter:
    app: AsyncApp

    def __init__(self, app: AsyncApp):
        self.app = app

    async def listener(self, client: SocketModeClient, req: SocketModeRequest):
        start = time()
        bolt_req: AsyncBoltRequest = AsyncBoltRequest(mode="socket_mode", body=req.payload)
        bolt_resp: BoltResponse = await self.app.async_dispatch(bolt_req)
        if bolt_resp.status == 200:
            if bolt_resp.body is None or len(bolt_resp.body) == 0:
                await client.send_socket_mode_response(
                    SocketModeResponse(envelope_id=req.envelope_id)
                )
            elif bolt_resp.body.startswith("{"):
                await client.send_socket_mode_response(
                    SocketModeResponse(
                        envelope_id=req.envelope_id, payload=bolt_resp.body,
                    )
                )
            if client.logger.level <= logging.DEBUG:
                spent_time = int((time() - start) * 1000)
                client.logger.debug(f"Response time: {spent_time} milliseconds")
        else:
            client.logger.info(
                f"Unsuccessful Bolt execution result (status: {bolt_resp.status}, body: {bolt_resp.body})"
            )


class AsyncSocketModeApp:
    app: AsyncApp
    app_token: str
    client: SocketModeClient

    def __init__(
        self, app: AsyncApp, app_token: Optional[str] = None,
    ):
        self.app = app
        self.app_token = app_token or os.environ["SLACK_APP_TOKEN"]
        self.client = SocketModeClient(app_token=self.app_token)
        listener = AsyncSocketModeAdapter(self.app).listener
        self.client.socket_mode_request_listeners.append(listener)

    async def connect_async(self):
        await self.client.connect()

    async def disconnect_async(self):
        await self.client.disconnect()

    async def close_async(self):
        await self.client.close()

    async def start_async(self):
        await self.connect_async()
        if self.app.logger.level > logging.INFO:
            print("⚡️ Bolt app is running!")
        else:
            self.app.logger.info("⚡️ Bolt app is running!")
        await asyncio.sleep(float("inf"))

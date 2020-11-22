# ------------------
# Only for running this script here
import sys
from os.path import dirname

sys.path.insert(1, f"{dirname(__file__)}/../../../..")
# ------------------

import logging
import os
from threading import Event
from typing import Optional
from time import time
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.websocket_client import SocketModeClient

from slack_bolt.app import App
from slack_bolt.request import BoltRequest
from slack_bolt.response import BoltResponse


class SocketModeAdapter:
    app: App

    def __init__(self, app: App):
        self.app = app

    def listener(self, client: SocketModeClient, req: SocketModeRequest):
        start = time()
        bolt_req: BoltRequest = BoltRequest(mode="socket_mode", body=req.payload)
        bolt_resp: BoltResponse = self.app.dispatch(bolt_req)
        if bolt_resp.status == 200:
            if bolt_resp.body is None or len(bolt_resp.body) == 0:
                client.send_socket_mode_response(
                    SocketModeResponse(envelope_id=req.envelope_id)
                )
            elif bolt_resp.body.startswith("{"):
                client.send_socket_mode_response(
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


class SocketModeApp:
    app: App
    app_token: str
    client: SocketModeClient

    def __init__(
        self, app: App, app_token: Optional[str] = None,
    ):
        self.app = app
        self.app_token = app_token or os.environ["SLACK_APP_TOKEN"]
        self.client = SocketModeClient(app_token=self.app_token)
        listener = SocketModeAdapter(self.app).listener
        self.client.socket_mode_request_listeners.append(listener)

    def connect(self):
        self.client.connect()

    def disconnect(self):
        self.client.disconnect()

    def close(self):
        self.client.close()

    def start(self):
        self.connect()
        if self.app.logger.level > logging.INFO:
            print("⚡️ Bolt app is running!")
        else:
            self.app.logger.info("⚡️ Bolt app is running!")
        Event().wait()

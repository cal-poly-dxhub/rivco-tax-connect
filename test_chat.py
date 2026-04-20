#!/usr/bin/env python3
"""Test Amazon Connect chat flow programmatically via WebSocket."""
import json
import sys
import threading
import time

import boto3
import websocket

REGION = "us-west-2"
INSTANCE_ID = "b5a167fa-10a6-41c9-9150-affd1f5bfcb5"
FLOW_ID = "b91a5635-1cb1-4f1d-aa4e-c86852087574"

connect = boto3.client("connect", region_name=REGION)
participant = boto3.client("connectparticipant", region_name=REGION)


def start_chat():
    resp = connect.start_chat_contact(
        InstanceId=INSTANCE_ID,
        ContactFlowId=FLOW_ID,
        ParticipantDetails={"DisplayName": "TestUser"},
        ChatDurationInMinutes=60,
    )
    print(f"ContactId: {resp['ContactId']}")
    return resp["ParticipantToken"]


def create_connection(token):
    resp = participant.create_participant_connection(
        ParticipantToken=token, Type=["WEBSOCKET", "CONNECTION_CREDENTIALS"]
    )
    return resp["Websocket"]["Url"], resp["ConnectionCredentials"]["ConnectionToken"]


def send_message(conn_token, text):
    resp = participant.send_message(
        ConnectionToken=conn_token, Content=text, ContentType="text/plain"
    )
    print(f"\n>>> YOU: {text}")
    return resp


def on_message(ws, raw):
    data = json.loads(raw)
    if data.get("topic") == "aws/chat":
        content = json.loads(data["content"])
        msg_type = content.get("Type")
        participant_role = content.get("ParticipantRole", "")
        display = content.get("DisplayName", participant_role)

        if msg_type == "MESSAGE" and participant_role != "CUSTOMER":
            print(f"\n<<< {display}: {content.get('Content', '')}")
        elif msg_type == "EVENT":
            event_type = content.get("ContentType", "")
            if "participant.joined" in event_type:
                print(f"  [{display} joined]")
            elif "participant.left" in event_type:
                print(f"  [{display} left]")
            elif "chat.ended" in event_type:
                print("  [Chat ended]")
                ws.close()
    elif data.get("topic") == "aws/subscribe":
        status = json.loads(data["content"]).get("status") if isinstance(data.get("content"), str) else data.get("content", {}).get("status")
        print(f"  [Subscribed: {status}]")


def on_error(ws, error):
    print(f"  [WS Error: {error}]")


def on_close(ws, code, msg):
    print(f"  [WS Closed: {code} {msg}]")


def on_open(ws):
    ws.send(json.dumps({"topic": "aws/subscribe", "content": {"topics": ["aws/chat"]}}))


def main():
    print("Starting chat contact...")
    token = start_chat()

    print("Creating participant connection...")
    ws_url, conn_token = create_connection(token)

    print("Connecting WebSocket...\n")
    ws = websocket.WebSocketApp(
        ws_url, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close
    )
    wst = threading.Thread(target=ws.run_forever, daemon=True)
    wst.start()

    print("Waiting for welcome message (type messages below, 'quit' to exit)...\n")
    time.sleep(5)

    # Interactive loop
    while True:
        try:
            text = input("You: ").strip()
            if not text:
                continue
            if text.lower() == "quit":
                participant.disconnect_participant(ConnectionToken=conn_token)
                print("Disconnected.")
                break
            send_message(conn_token, text)
            time.sleep(3)  # wait for bot response
        except (KeyboardInterrupt, EOFError):
            print("\nDisconnecting...")
            try:
                participant.disconnect_participant(ConnectionToken=conn_token)
            except Exception:
                pass
            break


if __name__ == "__main__":
    main()

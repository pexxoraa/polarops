import json
import uuid

from js import WebSocketPair
from workers import DurableObject, Response

from core.config import required_env_value
from core.security import decode_token
from core.time import utcnow


class ExpeditionRoom(DurableObject):
    """One hibernatable WebSocket room per expedition."""

    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.sessions = {}
        try:
            for ws in self.ctx.getWebSockets():
                attachment = ws.deserializeAttachment()
                self.sessions[str(attachment or "unauth")] = ws
        except Exception:
            pass

    async def fetch(self, request):
        upgrade = request.headers.get("Upgrade")
        if not upgrade or str(upgrade).lower() != "websocket":
            return Response("Expected WebSocket upgrade", status=426)
        client, server = WebSocketPair.new().object_values()
        self.ctx.acceptWebSocket(server)
        sid = "unauth:" + str(uuid.uuid4())
        server.serializeAttachment(sid)
        self.sessions[sid] = server
        return Response(None, status=101, web_socket=client)

    async def webSocketMessage(self, ws, message):
        try:
            data = json.loads(str(message))
        except Exception:
            return
        attachment = str(ws.deserializeAttachment() or "")
        if data.get("type") == "auth":
            try:
                payload = decode_token(
                    str(data.get("token") or ""),
                    required_env_value(self.env, "AUTH_SECRET"),
                )
                new_attachment = f"auth:{payload['uid']}:{payload['oid']}:{payload['role']}"
                self.sessions.pop(attachment, None)
                ws.serializeAttachment(new_attachment)
                self.sessions[new_attachment] = ws
                ws.send(json.dumps({
                    "type": "auth.ok",
                    "expedition_id": int(self.ctx.id.name or 0),
                    "user_id": payload["uid"],
                    "server_time": utcnow(),
                }))
            except Exception:
                ws.send(json.dumps({
                    "type": "auth.error",
                    "detail": "Invalid or expired session",
                }))
                ws.close(4401, "Unauthorized")
            return
        if not attachment.startswith("auth:"):
            ws.send(json.dumps({
                "type": "auth.error",
                "detail": "Authentication required",
            }))
            return
        if data.get("type") == "ping":
            ws.send(json.dumps({"type": "pong", "server_time": utcnow()}))

    async def webSocketClose(self, ws, code, reason, wasClean):
        attachment = str(ws.deserializeAttachment() or "")
        self.sessions.pop(attachment, None)
        try:
            ws.close(code, reason)
        except Exception:
            pass

    async def broadcast_json(self, payload: str):
        sent = 0
        for ws in self.ctx.getWebSockets():
            try:
                attachment = str(ws.deserializeAttachment() or "")
                if attachment.startswith("auth:"):
                    ws.send(payload)
                    sent += 1
            except Exception:
                pass
        return sent

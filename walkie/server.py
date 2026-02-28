import asyncio
import json
import random
import string
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── State ────────────────────────────────────────────────────────────────────

channels: dict[str, "Channel"] = {}


class Channel:
    def __init__(self, code: str):
        self.code = code
        self.created_at = time.time()
        self.users: dict[int, WebSocket] = {}   # user_id -> ws
        self.busy_by: int | None = None          # user_id currently talking

    def expired(self) -> bool:
        """Code is valid for joining for 10 minutes; existing users stay."""
        return time.time() - self.created_at > 600

    def next_id(self) -> int:
        return max(self.users.keys(), default=0) + 1

    async def broadcast(self, msg: dict, exclude: int | None = None):
        dead = []
        for uid, ws in list(self.users.items()):
            if uid == exclude:
                continue
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(uid)
        for uid in dead:
            self.users.pop(uid, None)


# ─── REST ─────────────────────────────────────────────────────────────────────

def _gen_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


@app.get("/")
async def index():
    with open("index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/create")
async def create_channel():
    # prune abandoned channels
    for code in list(channels.keys()):
        ch = channels[code]
        if not ch.users and ch.expired():
            del channels[code]

    code = _gen_code()
    while code in channels:
        code = _gen_code()

    channels[code] = Channel(code)
    return JSONResponse({"code": code})


@app.get("/check/{code}")
async def check_channel(code: str):
    if code not in channels:
        return JSONResponse({"ok": False, "reason": "not_found"})
    if channels[code].expired() and not channels[code].users:
        del channels[code]
        return JSONResponse({"ok": False, "reason": "expired"})
    return JSONResponse({"ok": True})


# ─── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws/{code}")
async def ws_endpoint(websocket: WebSocket, code: str):
    if code not in channels:
        await websocket.close(code=4004, reason="channel_not_found")
        return

    ch = channels[code]

    # New users can't join if code expired (but existing users stay)
    if ch.expired() and not ch.users:
        await websocket.close(code=4008, reason="code_expired")
        return

    await websocket.accept()

    uid = ch.next_id()
    ch.users[uid] = websocket

    # Tell this user their id + current state
    await websocket.send_json({
        "type": "welcome",
        "user_id": uid,
        "user_count": len(ch.users),
        "busy": ch.busy_by is not None,
        "busy_by": ch.busy_by,
    })

    # Tell everyone else a new peer arrived (they should create offers)
    await ch.broadcast(
        {"type": "peer_joined", "peer_id": uid, "user_count": len(ch.users)},
        exclude=uid,
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            t = data.get("type")

            # ── PTT ──────────────────────────────────────────────────────
            if t == "ptt_start":
                if ch.busy_by is None:
                    ch.busy_by = uid
                    await ch.broadcast({"type": "channel_busy", "user_id": uid})
                else:
                    await websocket.send_json({"type": "channel_blocked"})

            elif t == "ptt_stop":
                if ch.busy_by == uid:
                    ch.busy_by = None
                    await ch.broadcast({"type": "channel_free", "user_id": uid})

            # ── WebRTC signaling ──────────────────────────────────────────
            elif t in ("offer", "answer", "ice"):
                target = data.get("target")
                target_ws = ch.users.get(target)
                if target_ws:
                    data["from"] = uid
                    try:
                        await target_ws.send_json(data)
                    except Exception:
                        pass

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ch.users.pop(uid, None)
        if ch.busy_by == uid:
            ch.busy_by = None
            await ch.broadcast({"type": "channel_free", "user_id": uid})
        await ch.broadcast(
            {"type": "peer_left", "peer_id": uid, "user_count": len(ch.users)}
        )
        if not ch.users:
            channels.pop(code, None)


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)

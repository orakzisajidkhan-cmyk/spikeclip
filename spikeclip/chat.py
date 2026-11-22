"""Anonymous Twitch chat reader over secure WebSocket.

Twitch's anonymous IRC login (``justinfan``) needs no account and no
OAuth token. Plain IRC ports 6667/6697 are blocked on some networks
(HTTP(S)-only egress), so chat rides on the secure WebSocket endpoint
``wss://irc-ws.chat.twitch.tv:443`` — port 443 passes everywhere.

The reader counts PRIVMSG per window and calls ``on_window_closed(idx,
count)`` as each window finishes. It reconnects with backoff if the
connection drops; window indices stay anchored to ``stream_start``, so
counts resume in the right window after a reconnect.
"""

import random
import re
import threading
import time

import websocket  # websocket-client

IRC_WS_URL = "wss://irc-ws.chat.twitch.tv:443"


class ChatReader(threading.Thread):
    """Counts Twitch chat messages per window, anonymously.

    Args:
        channel: Twitch channel name (lowercased).
        window: seconds per counting window.
        stream_start: time.monotonic() value the windows anchor to.
        on_window_closed: called as ``on_window_closed(idx, count)``
            when window ``idx`` finishes.
        on_message: optional ``on_message(idx, total)`` per message.
    """

    def __init__(self, channel, window, stream_start, on_window_closed,
                 on_message=None):
        super().__init__(daemon=True)
        self.channel = channel.lower()
        self.window = window
        self.stream_start = stream_start
        self.on_window_closed = on_window_closed
        self.on_message = on_message
        self.stop_flag = threading.Event()
        self.counts = []  # per-window message counts
        self.total = 0
        self.connected = False

    def _window_index(self):
        return int((time.monotonic() - self.stream_start) // self.window)

    def run(self):
        nick = f"justinfan{random.randint(10000, 99999)}"
        buf = ""
        last_closed = -1
        attempt = 0
        backoffs = [2, 5, 10, 20, 30]
        while not self.stop_flag.is_set():
            attempt += 1
            delay = backoffs[min(attempt - 1, len(backoffs) - 1)]
            try:
                ws = websocket.create_connection(IRC_WS_URL, timeout=15)
                ws.settimeout(5)
                ws.send("PASS oauth:justinfan\r\n")
                ws.send(f"NICK {nick}\r\n")
                ws.send(f"JOIN #{self.channel}\r\n")
            except Exception as e:  # noqa: BLE001 - reconnect on any failure
                print(f"[chat] WS connect failed: {e} — retrying in "
                      f"{delay}s (attempt {attempt})", flush=True)
                self.stop_flag.wait(delay)
                continue
            self.connected = True
            print(f"[chat] joined #{self.channel} as {nick} (anonymous, "
                  f"wss port 443)", flush=True)
            try:
                while not self.stop_flag.is_set():
                    try:
                        chunk = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        chunk = ""
                    except Exception as e:
                        raise OSError(f"WS recv failed: {e}")
                    if chunk:
                        buf += chunk if isinstance(chunk, str) else \
                            chunk.decode("utf-8", errors="ignore")
                        while "\r\n" in buf:
                            line, buf = buf.split("\r\n", 1)
                            self._handle_line(ws, line)
                    # close finished windows
                    cur = self._window_index()
                    while last_closed < cur - 1:
                        last_closed += 1
                        while len(self.counts) <= last_closed:
                            self.counts.append(0)
                        self.on_window_closed(last_closed,
                                              self.counts[last_closed])
            except OSError as e:
                print(f"[chat] WS error: {e} — reconnecting in {delay}s "
                      f"(attempt {attempt})", flush=True)
            finally:
                self.connected = False
                try:
                    ws.close()
                except Exception:
                    pass
            if not self.stop_flag.is_set():
                self.stop_flag.wait(delay)
        # stopped: flush the final partial window
        cur = self._window_index()
        while last_closed < cur:
            last_closed += 1
            while len(self.counts) <= last_closed:
                self.counts.append(0)
            self.on_window_closed(last_closed, self.counts[last_closed])

    def _handle_line(self, ws, line):
        if line.startswith("PING"):
            try:
                ws.send(line.replace("PING", "PONG", 1) + "\r\n")
            except Exception:
                pass
            return
        m = re.search(r"PRIVMSG #\S+ :", line)
        if not m:
            return
        idx = self._window_index()
        while len(self.counts) <= idx:
            self.counts.append(0)
        self.counts[idx] += 1
        self.total += 1
        if self.on_message:
            self.on_message(idx, self.total)

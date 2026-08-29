import socket
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Set, List

@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8890
    backlog: int = 128
    recv_size: int = 1024

class ClientSession:
    def __init__(self, server: 'ChatServer', sock: socket.socket, addr: tuple):
        self.server = server
        self.sock = sock
        self.addr = addr
        self.nickname: Optional[str] = None
        self.connected_at = time.time()
        self.bytes_rx = 0
        self.bytes_tx = 0
        self.buffer = ""
        self.registered = False
        self.closed = False
        self.send_lock = threading.Lock()

    def send_raw(self, message: str):
        if self.closed:
            return
        if not message.endswith("\r\n"):
            message = message.rstrip("\r\n") + "\r\n"
        encoded = message.encode("utf-8")
        
        with self.send_lock:
            try:
                self.sock.sendall(encoded)
                self.bytes_tx += len(encoded)
            except OSError:
                pass

    def run(self):
        try:
            if not self._handshake():
                return
            self._receive_loop()
        except ConnectionResetError:
            print(f"[RESET] '{self.nickname}' connection reset abruptly (TCP RST).")
        except Exception as e:
            print(f"[ERROR] Session error for '{self.nickname}': {e}")
        finally:
            self.cleanup()

    def _handshake(self) -> bool:
        banner = (
            "\r\n"
            "=====================================================\r\n"
            "   Welcome to the TCP Terminal Chat Server!          \r\n"
            "=====================================================\r\n"
            " Commands: /help, /users, /nick <name>, /msg <u> <m>, /quit\r\n"
            "-----------------------------------------------------\r\n"
            "Enter your nickname: "
        )
        self.send_raw(banner)

        while "\n" not in self.buffer:
            try:
                data = self.sock.recv(self.server.config.recv_size)
            except OSError:
                return False
            if not data:
                return False
            self.bytes_rx += len(data)
            self.buffer += data.decode("utf-8", errors="replace")

        line, self.buffer = self.buffer.split("\n", 1)
        chosen_name = line.strip(" \r\n\t")
        candidate_name = chosen_name.replace(" ", "_") if chosen_name else f"User_{self.addr[1]}"

        while True:
            if self.server.register_session(self, candidate_name):
                self.registered = True
                print(f"[JOIN] '{self.nickname}' connected from {self.addr} (Active: {self.server.active_count()})")
                self.send_raw(f"\r\n[SERVER] You are now connected as @{self.nickname}. Start chatting!\r\n")
                self.server.broadcast(f"*** [SERVER] @{self.nickname} has joined the chat room ***", exclude=self)
                return True
            
            self.send_raw(
                f"[SERVER] Error: Nickname @{candidate_name} is already in use. Please enter a different nickname: "
            )
            
            while "\n" not in self.buffer:
                try:
                    data = self.sock.recv(self.server.config.recv_size)
                except OSError:
                    return False
                if not data:
                    return False
                self.bytes_rx += len(data)
                self.buffer += data.decode("utf-8", errors="replace")
                
            line, self.buffer = self.buffer.split("\n", 1)
            chosen_name = line.strip(" \r\n\t")
            candidate_name = chosen_name.replace(" ", "_") if chosen_name else f"User_{self.addr[1]}"

    def _receive_loop(self):
        while not self.server.shutdown_event.is_set() and not self.closed:
            try:
                data = self.sock.recv(self.server.config.recv_size)
            except OSError:
                break
                
            if not data:
                print(f"[LEAVE] '{self.nickname}' disconnected cleanly (EOF/FIN).")
                break

            self.bytes_rx += len(data)
            self.buffer += data.decode("utf-8", errors="replace")

            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                msg = line.strip(" \r\n\t")
                if not msg:
                    continue
                if self._handle_command(msg):
                    return

    def _handle_command(self, msg: str) -> bool:
        if msg.startswith("/"):
            parts = msg.split(" ", 2)
            cmd = parts[0].lower()

            if cmd in ("/quit", "/exit"):
                self.send_raw("[SERVER] Goodbye!\r\n")
                return True

            elif cmd == "/help":
                help_text = (
                    "\r\n--- Available Commands ---\r\n"
                    "  /help              - Show this menu\r\n"
                    "  /users or /list    - List online users\r\n"
                    "  /nick <new_name>   - Change your nickname\r\n"
                    "  /msg <user> <text> - Send private DM\r\n"
                    "  /stats             - View connection metrics\r\n"
                    "  /quit or /exit     - Disconnect\r\n"
                )
                self.send_raw(help_text)

            elif cmd in ("/users", "/list"):
                users = self.server.get_all_users()
                user_list = [f" - @{u.nickname} ({u.addr[0]}:{u.addr[1]})" for u in users]
                self.send_raw(f"\r\n--- Online Users ({len(user_list)}) ---\r\n" + "\r\n".join(user_list) + "\r\n")

            elif cmd == "/nick":
                if len(parts) > 1 and parts[1].strip():
                    new_name = parts[1].strip().replace(" ", "_")
                    old_name = self.nickname
                    if self.server.change_nickname(self, new_name):
                        self.send_raw(f"[SERVER] Nickname changed to @{new_name}\r\n")
                        self.server.broadcast(f"*** [SERVER] @{old_name} is now known as @{new_name} ***", exclude=self)
                    else:
                        self.send_raw(f"[SERVER] Error: Nickname @{new_name} is already in use.\r\n")
                else:
                    self.send_raw("[SERVER] Usage: /nick <new_name>\r\n")

            elif cmd in ("/msg", "/dm") and len(parts) > 2:
                target_user = parts[1].lstrip("@")
                dm_text = parts[2]
                target_session = self.server.get_user_by_nickname(target_user)
                if target_session:
                    target_session.send_raw(f"[DM from @{self.nickname}]: {dm_text}")
                    self.send_raw(f"[DM to @{target_user}]: {dm_text}")
                else:
                    self.send_raw(f"[SERVER] User @{target_user} not found.")

            elif cmd == "/stats":
                uptime = time.time() - self.connected_at
                stats_msg = (
                    f"\r\n--- TCP Connection Stats ---\r\n"
                    f"  Endpoint: {self.addr[0]}:{self.addr[1]}\r\n"
                    f"  Uptime:   {uptime:.1f} seconds\r\n"
                    f"  RX Bytes: {self.bytes_rx} bytes\r\n"
                )
                self.send_raw(stats_msg)
            else:
                self.send_raw(f"[SERVER] Unknown command '{cmd}'. Type /help for assistance.")
        else:
            print(f"[CHAT] @{self.nickname}: {msg}")
            self.server.broadcast(f"[@{self.nickname}]: {msg}", exclude=self)
        return False

    def cleanup(self):
        if self.closed:
            return
        self.closed = True
        
        try:
            self.sock.close()
        except OSError:
            pass

        if self.registered:
            self.server.unregister_session(self)
            self.registered = False
            print(f"[DISCONNECT] Closed socket for '{self.nickname}'. Remaining active: {self.server.active_count()}")
            self.server.broadcast(f"*** [SERVER] @{self.nickname} left the chat room ***")


class ChatServer:
    def __init__(self, config: ServerConfig = ServerConfig()):
        self.config = config
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        self.sessions: Set[ClientSession] = set()
        self.registry_lock = threading.Lock()
        self.shutdown_event = threading.Event()
        
        self.worker_threads: Set[threading.Thread] = set()
        self.worker_lock = threading.Lock()
        self._bound_address: Optional[Tuple[str, int]] = None

    def start(self):
        self.server_sock.bind((self.config.host, self.config.port))
        self.server_sock.listen(self.config.backlog)
        self._bound_address = self.server_sock.getsockname()

    def get_bound_address(self) -> Tuple[str, int]:
        if not self._bound_address:
            raise RuntimeError("Server is not bound to an address. Call start() first.")
        return self._bound_address

    def _is_nickname_taken(self, nickname: str) -> bool:
        target = nickname.casefold()
        for session in self.sessions:
            if session.nickname and session.nickname.casefold() == target:
                return True
        return False

    def register_session(self, session: ClientSession, nickname: str) -> bool:
        with self.registry_lock:
            if self._is_nickname_taken(nickname):
                return False
            session.nickname = nickname
            self.sessions.add(session)
            return True

    def unregister_session(self, session: ClientSession):
        with self.registry_lock:
            self.sessions.discard(session)

    def change_nickname(self, session: ClientSession, new_nickname: str) -> bool:
        with self.registry_lock:
            if self._is_nickname_taken(new_nickname):
                return False
            session.nickname = new_nickname
            return True

    def active_count(self) -> int:
        with self.registry_lock:
            return len(self.sessions)

    def get_all_users(self) -> List[ClientSession]:
        with self.registry_lock:
            return list(self.sessions)

    def get_user_by_nickname(self, nickname: str) -> Optional[ClientSession]:
        target = nickname.casefold()
        with self.registry_lock:
            for s in self.sessions:
                if s.nickname and s.nickname.casefold() == target:
                    return s
        return None

    def broadcast(self, message: str, exclude: Optional[ClientSession] = None):
        with self.registry_lock:
            recipients = [s for s in self.sessions if s != exclude]
        for s in recipients:
            s.send_raw(message)

    def _run_worker(self, session: ClientSession):
        try:
            session.run()
        finally:
            with self.worker_lock:
                self.worker_threads.discard(threading.current_thread())

    def serve_forever(self):
        try:
            while not self.shutdown_event.is_set():
                try:
                    client_sock, client_addr = self.server_sock.accept()
                except OSError:
                    break
                    
                session = ClientSession(self, client_sock, client_addr)
                t = threading.Thread(target=self._run_worker, args=(session,), daemon=True)
                
                with self.worker_lock:
                    if self.shutdown_event.is_set():
                        client_sock.close()
                        break
                    self.worker_threads.add(t)
                    
                t.start()
        finally:
            self.shutdown()

    def shutdown(self):
        if self.shutdown_event.is_set():
            return
        self.shutdown_event.set()
        
        try:
            self.server_sock.close()
        except OSError:
            pass

        with self.registry_lock:
            sessions_to_close = list(self.sessions)
            
        for s in sessions_to_close:
            s.cleanup()

        current_t = threading.current_thread()
        with self.worker_lock:
            workers = list(self.worker_threads)
            
        for t in workers:
            if t != current_t:
                t.join(timeout=2.0)


def run_server():
    config = ServerConfig()
    server = ChatServer(config)
    server.start()
    
    port = server.get_bound_address()[1]
    print("=" * 65)
    print(f" [TCP CHAT SERVER] Running on port {port}")
    print(" Connect via:")
    print(f"   - Telnet:  telnet localhost {port}")
    print(f"   - Netcat:  nc localhost {port}  (or ncat localhost {port})")
    print("   - Python:  python3 client.py")
    print("=" * 65)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down TCP Chat Server...")
    finally:
        server.shutdown()

if __name__ == "__main__":
    run_server()

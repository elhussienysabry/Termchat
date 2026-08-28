import socket
import threading
import time
from typing import Dict, Any

HOST = "0.0.0.0"  # Listen on all network interfaces
PORT = 8890

# Registry mapping socket -> client state dict
clients: Dict[socket.socket, Dict[str, Any]] = {}
clients_lock = threading.Lock()


def send_raw(sock: socket.socket, message: str):
    """Sends text ensuring Telnet-compatible CRLF line endings."""
    if not message.endswith("\r\n"):
        message = message.rstrip("\r\n") + "\r\n"
    try:
        sock.sendall(message.encode("utf-8"))
    except OSError:
        pass


def broadcast(message: str, sender_sock: socket.socket = None):
    """Broadcasts a message to all connected clients except sender."""
    with clients_lock:
        recipients = [s for s in clients if s != sender_sock]

    for s in recipients:
        send_raw(s, message)


def handle_client(sock: socket.socket, addr: tuple):
    """Handles an individual client connection session."""
    local_buffer = ""
    client_info = {
        "name": f"User_{addr[1]}",
        "addr": addr,
        "connected_at": time.time(),
        "bytes_rx": 0,
        "bytes_tx": 0,
    }

    # Send Welcome Banner
    banner = (
        "\r\n"
        "=====================================================\r\n"
        "   Welcome to the TCP Terminal Chat Server!          \r\n"
        "=====================================================\r\n"
        " Commands: /help, /users, /nick <name>, /msg <u> <m>, /quit\r\n"
        "-----------------------------------------------------\r\n"
        "Enter your nickname: "
    )
    send_raw(sock, banner)

    # Initial handshake: get nickname
    try:
        while True:
            data = sock.recv(1024)
            if not data:
                sock.close()
                return
            client_info["bytes_rx"] += len(data)
            local_buffer += data.decode("utf-8", errors="replace")

            if "\n" in local_buffer:
                line, local_buffer = local_buffer.split("\n", 1)
                chosen_name = line.strip(" \r\n\t")
                if chosen_name:
                    client_info["name"] = chosen_name.replace(" ", "_")
                break
    except Exception:
        sock.close()
        return

    with clients_lock:
        clients[sock] = client_info

    name = client_info["name"]
    print(f"[JOIN] '{name}' connected from {addr} (Active: {len(clients)})")
    send_raw(sock, f"\r\n[SERVER] You are now connected as @{name}. Start chatting!\r\n")
    broadcast(f"*** [SERVER] @{name} has joined the chat room ***", sender_sock=sock)

    # Main message processing loop
    try:
        while True:
            data = sock.recv(1024)
            if not data:
                # Client closed connection (TCP FIN received / EOF)
                print(f"[LEAVE] '{name}' disconnected cleanly (EOF/FIN).")
                break

            client_info["bytes_rx"] += len(data)
            local_buffer += data.decode("utf-8", errors="replace")

            # Process all complete lines in buffer (Stream Framing)
            while "\n" in local_buffer:
                line, local_buffer = local_buffer.split("\n", 1)
                msg = line.strip(" \r\n\t")
                if not msg:
                    continue

                # Handle Commands
                if msg.startswith("/"):
                    parts = msg.split(" ", 2)
                    cmd = parts[0].lower()

                    if cmd in ("/quit", "/exit"):
                        send_raw(sock, "[SERVER] Goodbye!\r\n")
                        return

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
                        send_raw(sock, help_text)

                    elif cmd in ("/users", "/list"):
                        with clients_lock:
                            user_list = [f" - @{info['name']} ({info['addr'][0]}:{info['addr'][1]})" for info in clients.values()]
                        send_raw(sock, f"\r\n--- Online Users ({len(user_list)}) ---\r\n" + "\r\n".join(user_list) + "\r\n")

                    elif cmd == "/nick" and len(parts) > 1:
                        old_name = client_info["name"]
                        new_name = parts[1].strip().replace(" ", "_")
                        client_info["name"] = new_name
                        name = new_name
                        send_raw(sock, f"[SERVER] Nickname changed to @{new_name}\r\n")
                        broadcast(f"*** [SERVER] @{old_name} is now known as @{new_name} ***", sender_sock=sock)

                    elif cmd in ("/msg", "/dm") and len(parts) > 2:
                        target_user = parts[1].lstrip("@")
                        dm_text = parts[2]
                        target_sock = None
                        with clients_lock:
                            for s, info in clients.items():
                                if info["name"].lower() == target_user.lower():
                                    target_sock = s
                                    break
                        if target_sock:
                            send_raw(target_sock, f"[DM from @{name}]: {dm_text}")
                            send_raw(sock, f"[DM to @{target_user}]: {dm_text}")
                        else:
                            send_raw(sock, f"[SERVER] User @{target_user} not found.")

                    elif cmd == "/stats":
                        uptime = time.time() - client_info["connected_at"]
                        stats_msg = (
                            f"\r\n--- TCP Connection Stats ---\r\n"
                            f"  Endpoint: {addr[0]}:{addr[1]}\r\n"
                            f"  Uptime:   {uptime:.1f} seconds\r\n"
                            f"  RX Bytes: {client_info['bytes_rx']} bytes\r\n"
                        )
                        send_raw(sock, stats_msg)
                    else:
                        send_raw(sock, f"[SERVER] Unknown command '{cmd}'. Type /help for assistance.")
                else:
                    # Public broadcast message
                    print(f"[CHAT] @{name}: {msg}")
                    broadcast(f"[@{name}]: {msg}", sender_sock=sock)

    except ConnectionResetError:
        print(f"[RESET] '{name}' connection reset abruptly (TCP RST).")
    except Exception as e:
        print(f"[ERROR] Session error for '{name}': {e}")
    finally:
        with clients_lock:
            clients.pop(sock, None)
            remaining = len(clients)

        try:
            sock.close()
        except OSError:
            pass

        print(f"[DISCONNECT] Closed socket for '{name}'. Remaining active: {remaining}")
        broadcast(f"*** [SERVER] @{name} left the chat room ***")


def run_server():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(128)

    print("=" * 65)
    print(f" [TCP CHAT SERVER] Running on port {PORT}")
    print(" Connect via:")
    print(f"   - Telnet:  telnet localhost {PORT}")
    print(f"   - Netcat:  nc localhost {PORT}  (or ncat localhost {PORT})")
    print("   - Python:  python3 client.py")
    print("=" * 65)

    try:
        while True:
            client_sock, client_addr = server_sock.accept()
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, client_addr),
                daemon=True,
            )
            t.start()
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down TCP Chat Server...")
    finally:
        server_sock.close()


if __name__ == "__main__":
    run_server()

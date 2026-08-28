import socket
import sys
import threading

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8890


def receive_loop(sock: socket.socket, stop_event: threading.Event):
    """Background listener: reads stream from server and writes to stdout."""
    while not stop_event.is_set():
        try:
            data = sock.recv(1024)
            if not data:
                print("\n[!] Connection closed by server.")
                stop_event.set()
                break

            text = data.decode("utf-8", errors="replace")
            print(text, end="", flush=True)

        except OSError:
            break
        except Exception as e:
            if not stop_event.is_set():
                print(f"\n[!] Receive error: {e}")
            break


def run_client():
    server_host = sys.argv[1] if len(sys.argv) > 1 else SERVER_HOST
    server_port = int(sys.argv[2]) if len(sys.argv) > 2 else SERVER_PORT

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        sock.connect((server_host, server_port))
    except ConnectionRefusedError:
        print(f"[!] Could not connect to chat server at {server_host}:{server_port}.")
        print("    Ensure server.py is running first.")
        return

    stop_event = threading.Event()
    recv_thread = threading.Thread(
        target=receive_loop,
        args=(sock, stop_event),
        daemon=True,
    )
    recv_thread.start()

    try:
        while not stop_event.is_set():
            try:
                line = sys.stdin.readline()
                if not line:  # EOF / Ctrl+D
                    break
            except (KeyboardInterrupt, EOFError):
                break

            if stop_event.is_set():
                break

            # Send line to server
            if line.strip():
                sock.sendall(line.encode("utf-8"))

            if line.strip().lower() in ("/quit", "/exit"):
                break

    finally:
        stop_event.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
        print("\n[DISCONNECTED] Disconnected from chat.")


if __name__ == "__main__":
    run_client()

import unittest
import threading
import time
import socket
from unittest.mock import patch
import server

class TestServerCharacterization(unittest.TestCase):
    def setUp(self):
        # Find a free port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        self.bound_port = s.getsockname()[1]
        s.close()

        server.HOST = "127.0.0.1"
        server.PORT = self.bound_port
        server.clients.clear()
        
        self.server_thread = threading.Thread(target=server.run_server, daemon=True)
        self.server_thread.start()
        
        # Wait for server to start listening by trying to connect
        connected = False
        timeout = time.time() + 2
        while not connected and time.time() < timeout:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.1)
                s.connect(("127.0.0.1", self.bound_port))
                s.close()
                connected = True
            except (ConnectionRefusedError, OSError):
                time.sleep(0.05)
                
        if not connected:
            self.fail("Server did not bind to a port within timeout")

        self.client_sockets = []

    def tearDown(self):
        for sock in self.client_sockets:
            try:
                sock.close()
            except OSError:
                pass
                
        # Wait for threads to close. Since run_server loop runs indefinitely and we have no way to stop it cleanly in current code, 
        # we just clear the clients and let daemon threads die at exit, but wait a bit to avoid interference.
        server.clients.clear()
        time.sleep(0.1)

    def create_client(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(("127.0.0.1", self.bound_port))
        self.client_sockets.append(sock)
        # Read the banner and the "Enter your nickname: " prompt
        self._read_until(sock, "Enter your nickname: ")
        return sock

    def _read_until(self, sock, target, timeout=2.0):
        end_time = time.time() + timeout
        buf = ""
        while time.time() < end_time:
            try:
                data = sock.recv(1024).decode('utf-8', errors='replace')
                buf += data
                if target in buf:
                    return buf
            except socket.timeout:
                pass
        self.fail(f"Timeout waiting for '{target}'. Got: {buf}")

    def test_handshake_and_broadcast(self):
        c1 = self.create_client()
        c2 = self.create_client()

        # C1 joins
        c1.sendall(b"Alice\n")
        resp1 = self._read_until(c1, "Start chatting!")
        self.assertIn("You are now connected as @Alice", resp1)
        
        # C2 joins, C1 should see broadcast
        c2.sendall(b"Bob\n")
        resp2 = self._read_until(c2, "Start chatting!")
        self.assertIn("You are now connected as @Bob", resp2)
        
        c1_broadcast = self._read_until(c1, "@Bob has joined")
        self.assertIn("@Bob has joined", c1_broadcast)

        # C1 sends public message
        c1.sendall(b"Hello world\n")
        msg = self._read_until(c2, "[@Alice]: Hello world")
        self.assertIn("[@Alice]: Hello world", msg)

    def test_duplicate_nickname(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        c2 = self.create_client()
        c2.sendall(b"alice\n") # case-insensitive test
        err = self._read_until(c2, "already in use")
        self.assertIn("already in use", err)

        c2.sendall(b"Bob\n")
        self._read_until(c2, "Start chatting!")

    def test_direct_message(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        c2 = self.create_client()
        c2.sendall(b"Bob\n")
        self._read_until(c2, "Start chatting!")
        
        # Send DM from Alice to Bob
        c1.sendall(b"/msg Bob secret message\n")
        dm = self._read_until(c2, "[DM from @Alice]: secret message")
        self.assertIn("[DM from @Alice]: secret message", dm)
        
        dm_echo = self._read_until(c1, "[DM to @Bob]: secret message")
        self.assertIn("[DM to @Bob]: secret message", dm_echo)

    def test_nick_command(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        c1.sendall(b"/nick Alice_New\n")
        resp = self._read_until(c1, "Nickname changed to @Alice_New")
        self.assertIn("Nickname changed to @Alice_New", resp)

    def test_disconnect_unregisters(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        self.assertEqual(len(server.clients), 1)
        c1.close()
        
        timeout = time.time() + 2
        while len(server.clients) > 0 and time.time() < timeout:
            time.sleep(0.05)
            
        self.assertEqual(len(server.clients), 0)

if __name__ == '__main__':
    unittest.main()

import unittest
import threading
import time
import socket
from unittest.mock import patch
import server

class TestServerCharacterization(unittest.TestCase):
    def setUp(self):
        config = server.ServerConfig(host="127.0.0.1", port=0)
        self.chat_server = server.ChatServer(config)
        self.chat_server.start()
        self.bound_port = self.chat_server.get_bound_address()[1]
        
        self.server_thread = threading.Thread(target=self.chat_server.serve_forever, daemon=True)
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
                
        self.chat_server.shutdown()
        self.server_thread.join(timeout=1.0)
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
        c1.sendall(b"alice\n")
        self._read_until(c1, "Start chatting!")

        # Verify changing case for the same user succeeds
        c1.sendall(b"/nick Alice\n")
        resp = self._read_until(c1, "Nickname changed to @Alice")
        self.assertIn("Nickname changed to @Alice", resp)

        # Verify changing to a new nickname succeeds
        c1.sendall(b"/nick Alice_New\n")
        resp = self._read_until(c1, "Nickname changed to @Alice_New")
        self.assertIn("Nickname changed to @Alice_New", resp)

    def test_disconnect_unregisters(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        self.assertEqual(self.chat_server.active_count(), 1)
        c1.close()
        
        timeout = time.time() + 2
        while self.chat_server.active_count() > 0 and time.time() < timeout:
            time.sleep(0.05)
            
        self.assertEqual(self.chat_server.active_count(), 0)

    def test_shutdown_closes_listener(self):
        self.chat_server.shutdown()
        # Ensure we cannot connect anymore
        with self.assertRaises((ConnectionRefusedError, OSError)):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                sock.connect(("127.0.0.1", self.bound_port))

    def test_shutdown_closes_active_sessions(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")
        
        self.chat_server.shutdown()
        
        # the client should eventually get disconnected
        with self.assertRaises(Exception):
            self._read_until(c1, "should not receive this")

    def test_shutdown_safe_multiple_times(self):
        self.chat_server.shutdown()
        self.chat_server.shutdown() # Should not raise

    def test_multiple_independent_servers(self):
        # find another port
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.bind(("127.0.0.1", 0))
        port2 = s2.getsockname()[1]
        s2.close()

        config2 = server.ServerConfig(host="127.0.0.1", port=port2)
        server2 = server.ChatServer(config2)
        server2.start()
        
        t2 = threading.Thread(target=server2.serve_forever, daemon=True)
        t2.start()

        # Connect Alice to server 1
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        # Connect Alice to server 2 (should succeed because it's a different server)
        c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c2.settimeout(1.0)
        c2.connect(("127.0.0.1", port2))
        self.client_sockets.append(c2)
        self._read_until(c2, "Enter your nickname: ")
        
        c2.sendall(b"Alice\n")
        resp = self._read_until(c2, "Start chatting!")
        self.assertIn("You are now connected as @Alice", resp)
        
        server2.shutdown()
        t2.join(timeout=1.0)

if __name__ == '__main__':
    unittest.main()

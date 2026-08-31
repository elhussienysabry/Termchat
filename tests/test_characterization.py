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
        config2 = server.ServerConfig(host="127.0.0.1", port=0)
        server2 = server.ChatServer(config2)
        server2.start()
        port2 = server2.get_bound_address()[1]
        
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

    def test_direct_message_aliases_and_at_stripping(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        c2 = self.create_client()
        c2.sendall(b"Bob\n")
        self._read_until(c2, "Start chatting!")

        # /msg with leading @
        c1.sendall(b"/msg @Bob hello alice here\n")
        self.assertIn("[DM from @Alice]: hello alice here", self._read_until(c2, "[DM from @Alice]: hello alice here"))
        self.assertIn("[DM to @Bob]: hello alice here", self._read_until(c1, "[DM to @Bob]: hello alice here"))

        # /dm alias without @
        c2.sendall(b"/dm Alice hey alice from bob\n")
        self.assertIn("[DM from @Bob]: hey alice from bob", self._read_until(c1, "[DM from @Bob]: hey alice from bob"))
        self.assertIn("[DM to @Alice]: hey alice from bob", self._read_until(c2, "[DM to @Alice]: hey alice from bob"))

        # /w alias with @
        c1.sendall(b"/w @Bob whisper message\n")
        self.assertIn("[DM from @Alice]: whisper message", self._read_until(c2, "[DM from @Alice]: whisper message"))
        self.assertIn("[DM to @Bob]: whisper message", self._read_until(c1, "[DM to @Bob]: whisper message"))

    def test_direct_message_privacy_isolation(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        c2 = self.create_client()
        c2.sendall(b"Bob\n")
        self._read_until(c2, "Start chatting!")

        c3 = self.create_client()
        c3.sendall(b"Charlie\n")
        self._read_until(c3, "Start chatting!")

        self._read_until(c1, "@Charlie has joined")
        self._read_until(c2, "@Charlie has joined")

        # Alice sends private message to Bob
        c1.sendall(b"/msg Bob top_secret_for_bob\n")
        self._read_until(c2, "[DM from @Alice]: top_secret_for_bob")
        self._read_until(c1, "[DM to @Bob]: top_secret_for_bob")

        # Charlie should receive nothing related to the DM
        c3.sendall(b"Charlie public probe\n")
        self._read_until(c1, "[@Charlie]: Charlie public probe")
        self._read_until(c2, "[@Charlie]: Charlie public probe")

        c3.settimeout(0.2)
        charlie_data = ""
        try:
            while True:
                chunk = c3.recv(1024).decode('utf-8', errors='replace')
                if not chunk:
                    break
                charlie_data += chunk
        except socket.timeout:
            pass

        self.assertNotIn("top_secret_for_bob", charlie_data)

    def test_direct_message_error_handling(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        # Target not found
        c1.sendall(b"/msg Ghost hello\n")
        resp = self._read_until(c1, "Error: User @Ghost not found or offline.")
        self.assertIn("[SERVER] Error: User @Ghost not found or offline.", resp)

        # Target not found with @
        c1.sendall(b"/dm @Ghost hello\n")
        resp = self._read_until(c1, "Error: User @Ghost not found or offline.")
        self.assertIn("[SERVER] Error: User @Ghost not found or offline.", resp)

        # Self-DM
        c1.sendall(b"/msg Alice hello self\n")
        resp = self._read_until(c1, "Error: You cannot send a direct message to yourself.")
        self.assertIn("[SERVER] Error: You cannot send a direct message to yourself.", resp)

        c1.sendall(b"/w @Alice hello self\n")
        resp = self._read_until(c1, "Error: You cannot send a direct message to yourself.")
        self.assertIn("[SERVER] Error: You cannot send a direct message to yourself.", resp)

        # Empty usage
        c1.sendall(b"/msg\n")
        resp = self._read_until(c1, "Usage: /msg <username> <message>")
        self.assertIn("[SERVER] Usage: /msg <username> <message>", resp)

        c1.sendall(b"/dm Bob\n")
        resp = self._read_until(c1, "Usage: /dm <username> <message>")
        self.assertIn("[SERVER] Usage: /dm <username> <message>", resp)

        c1.sendall(b"/w\n")
        resp = self._read_until(c1, "Usage: /w <username> <message>")
        self.assertIn("[SERVER] Usage: /w <username> <message>", resp)

    def test_private_chat_mode_session_locking(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        c2 = self.create_client()
        c2.sendall(b"Bob\n")
        self._read_until(c2, "Start chatting!")

        c3 = self.create_client()
        c3.sendall(b"Charlie\n")
        self._read_until(c3, "Start chatting!")

        self._read_until(c1, "@Charlie has joined")
        self._read_until(c2, "@Charlie has joined")

        # Alice locks session into private chat mode with Bob
        c1.sendall(b"/chat Bob\n")
        resp = self._read_until(c1, "Entered private chat mode with @Bob")
        self.assertIn("[SERVER] Entered private chat mode with @Bob", resp)

        # Alice sends plain text (without /msg)
        c1.sendall(b"Hey Bob this is private\n")
        self.assertIn("[DM from @Alice]: Hey Bob this is private", self._read_until(c2, "[DM from @Alice]: Hey Bob this is private"))
        self.assertIn("[DM to @Bob]: Hey Bob this is private", self._read_until(c1, "[DM to @Bob]: Hey Bob this is private"))

        # Charlie should receive nothing
        c3.settimeout(0.2)
        charlie_data = ""
        try:
            while True:
                chunk = c3.recv(1024).decode('utf-8', errors='replace')
                if not chunk:
                    break
                charlie_data += chunk
        except socket.timeout:
            pass
        self.assertNotIn("Hey Bob this is private", charlie_data)

        # Switch back to public chat
        c1.sendall(b"/chat all\n")
        resp = self._read_until(c1, "Switched to public chat room.")
        self.assertIn("[SERVER] Switched to public chat room.", resp)

        # Alice sends public message
        c1.sendall(b"Hello world everyone\n")
        self.assertIn("[@Alice]: Hello world everyone", self._read_until(c2, "[@Alice]: Hello world everyone"))
        self.assertIn("[@Alice]: Hello world everyone", self._read_until(c3, "[@Alice]: Hello world everyone"))

    def test_private_chat_mode_edge_cases(self):
        c1 = self.create_client()
        c1.sendall(b"Alice\n")
        self._read_until(c1, "Start chatting!")

        c2 = self.create_client()
        c2.sendall(b"Bob\n")
        self._read_until(c2, "Start chatting!")

        # Self-chat error
        c1.sendall(b"/chat Alice\n")
        resp = self._read_until(c1, "Error: You cannot start a private chat with yourself.")
        self.assertIn("[SERVER] Error: You cannot start a private chat with yourself.", resp)

        # Non-existent user
        c1.sendall(b"/chat Ghost\n")
        resp = self._read_until(c1, "Error: User @Ghost not found or offline.")
        self.assertIn("[SERVER] Error: User @Ghost not found or offline.", resp)

        # Usage
        c1.sendall(b"/chat\n")
        resp = self._read_until(c1, "Usage: /chat <username> | /chat all | /chat public")
        self.assertIn("[SERVER] Usage: /chat <username> | /chat all | /chat public", resp)

        # Already public
        c1.sendall(b"/chat public\n")
        resp = self._read_until(c1, "You are already in the public chat room.")
        self.assertIn("[SERVER] You are already in the public chat room.", resp)

        # Enter private chat mode with Bob, then Bob disconnects
        c1.sendall(b"/chat @Bob\n")
        self._read_until(c1, "Entered private chat mode with @Bob")

        c2.close()
        time.sleep(0.2)

        # Alice sends message, should be notified Bob went offline and switched to public
        c1.sendall(b"Are you still here?\n")
        resp = self._read_until(c1, "Error: User @Bob not found or offline. Switched back to public chat room.")
        self.assertIn("[SERVER] Error: User @Bob not found or offline. Switched back to public chat room.", resp)

if __name__ == '__main__':
    unittest.main()

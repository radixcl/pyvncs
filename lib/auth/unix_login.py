from struct import pack
from lib import log


class UnixLoginAuth:

    def __init__(self):
        self.getbuff = lambda _: None

    def auth(self, sock, userlist):
        try:
            data = self._recv_exact(sock, 8)
            if len(data) < 8:
                self._send_result(sock, False)
                return False

            from struct import unpack
            user_len, pass_len = unpack("!II", data)

            username = self._recv_exact(sock, user_len).decode('iso-8859-1')
            password = self._recv_exact(sock, pass_len).decode('iso-8859-1')

            log.debug(__name__, "Unix login attempt:", username)

            if userlist.get(username) == password:
                self._send_result(sock, True)
                log.debug(__name__, "Unix login OK:", username)
                return True
            else:
                log.debug(__name__, "Unix login failed:", username)
                self._send_result(sock, False)
                return False
        except Exception as e:
            log.debug(__name__, "Unix login error:", e)
            self._send_result(sock, False)
            return False

    def _recv_exact(self, sock, n):
        data = b''
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def _send_result(self, sock, success):
        sock.sendall(pack("!I", 0 if success else 1))

import ssl
import os
from struct import pack
from lib import log


class TLSAuth:
    """RFB security type 18 - anonymous TLS (TigerVNC style).

    Encrypts the channel with TLS but does not require client certificates.
    After the TLS handshake, VNC DES challenge-response auth is performed
    over the encrypted channel.
    """

    def __init__(self):
        self.getbuff = lambda _: None
        self.pem_file = None
        self.ssl_socket = None

    def auth(self, sock, password):
        try:
            sslctx = self._load_ssl_context()
            sock.settimeout(30)
            self.ssl_socket = sslctx.wrap_socket(sock, server_side=True)
            self.ssl_socket.settimeout(None)
            log.debug(__name__, "TLS handshake completed")

            from lib.auth.vnc_auth import VNCAuth
            vnc = VNCAuth()
            vnc.getbuff = self._ssl_getbuff
            if not vnc.auth(self.ssl_socket, password):
                return False
            return True
        except ssl.SSLError as e:
            log.debug(__name__, "TLS error:", e)
            return False
        except Exception as e:
            log.debug(__name__, "TLS auth error:", e)
            return False

    def _ssl_getbuff(self, timeout):
        self.ssl_socket.settimeout(timeout)
        try:
            data = self.ssl_socket.recv(1024)
        except Exception:
            data = None
        self.ssl_socket.settimeout(None)
        return data

    def get_socket(self):
        if self.ssl_socket:
            return self.ssl_socket
        return None

    def _load_ssl_context(self):
        sslctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        sslctx.minimum_version = ssl.TLSVersion.TLSv1_2

        pem = self.pem_file
        if not pem or not os.path.isfile(pem):
            cert_dir = os.path.dirname(os.path.abspath(__file__))
            pem = os.path.join(cert_dir, 'vencrypt.pem')
            if not os.path.isfile(pem):
                from lib.auth.vencrypt import VeNCrypt
                v = VeNCrypt.__new__(VeNCrypt)
                pem = v._generate_self_signed_cert(pem)

        sslctx.load_cert_chain(certfile=pem)
        return sslctx

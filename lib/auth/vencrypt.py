from lib import log
from time import sleep
import ssl
import select
from struct import *

class VeNCrypt():

    subtypes = [
        256,       # Plain
        259,       # TLSPlain
    ]

    def __init__(self, sock):
        self.getbuff = lambda _: None
        self.sock = sock
        self.client_subtype = None
        self.pem_file = None
        log.debug(__name__, "initialized")

        # send version
        version = b'\x00\x02'   # 0.2
        sock.send(version)
        data = sock.recv(2)
        if data != version:
            sock.send(b'\x01')
            sock.close()
            raise Exception("unknown vencrypt version")

        sock.send(b'\x00')

    def send_subtypes(self):
        # send subtypes
        data = pack('!B', len(self.subtypes))
        for i in self.subtypes:
            data += pack('!I', i)
            log.debug(__name__, "subtype", i)

        self.sock.send(data)

        # get client choosen subtype
        data = self.sock.recv(4)
        (data,) = unpack('!I', data)
        log.debug("client subtype", data)
        self.client_subtype = data

    def auth_plain(self, userlist={}):
        data = self.sock.recv(8)
        user_length, pass_length = unpack('!II', data)
        username = self.sock.recv(user_length).decode()
        password = self.sock.recv(pass_length).decode()
        log.debug("user", username, password)

        if userlist.get(username) == password:
            self.sock.send(pack("!I", 0))
            log.debug(__name__, "Auth OK")
            return True
        else:
            log.debug(__name__, "Invalid auth")
            sleep(3)
            self.sock.send(pack("!I", 1))
            return False

    def auth_tls_plain(self, userlist={}):
        log.debug(__name__, 'Using TLSPlain')

        if not self.pem_file:
            log.debug(__name__, "No PEM file configured for TLSPlain")
            self.sock.send(pack("!I", 1))
            return False

        sslctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            sslctx.load_cert_chain(certfile=self.pem_file, keyfile=self.pem_file)
        except Exception as e:
            log.debug(__name__, "Failed to load PEM certificate:", str(e))
            self.sock.send(pack("!I", 1))
            return False

        # Only allow secure ciphers; reject anonymous/NULL export suites
        sslctx.set_ciphers(
            'ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:'
            'ECDHE+AES128:ECDHE+AES256:DHE+AES128:DHE+AES256:!aNULL:!eNULL:!EXPORT:!DES:!RC4'
        )

        self.sock.settimeout(30)
        try:
            wrapped = sslctx.wrap_socket(self.sock, server_side=True)
        except Exception as e:
            log.debug(__name__, "TLS handshake failed:", str(e))
            try:
                self.sock.send(pack("!I", 1))
            except Exception:
                pass
            return False

        wrapped.settimeout(None)
        self.sock = wrapped
        self.socket = wrapped  # keep in sync with VNCServer reference

        ret = self.auth_plain(userlist=userlist)
        if not ret:
            try:
                self.sock.close()
            except Exception:
                pass
        return ret

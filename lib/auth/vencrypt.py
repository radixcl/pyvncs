from lib import log
from time import sleep
import ssl
import select
from struct import *

class VeNCrypt():

    subtypes = [
        256,       # Plain
        #258,	   # TLSVnc     # FIXME: not yet implemented
        #259,       # TLSPlain   # FIXME: not yet implemented
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
        #log.debug("user", username, password)

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
        #TODO: implement TLS plain
        log.debug(__name__, 'Using TLSPlain')

        self.sock.sendall(pack("!I", 1))   # send ACK

        #data = self.getbuff(30)
        #print("data", data)


        #sslctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        sslctx = ssl.SSLContext(protocol=ssl.PROTOCOL_TLS_SERVER)
        sslctx.protocol = ssl.PROTOCOL_TLS
        #sslctx.load_cert_chain(certfile=self.pem_file, keyfile=self.pem_file)
        # this is quite insecure...
        sslctx.set_ciphers(":aNULL:kDHE:kEDH:ADH:DH:kECDHE:kEECDH:AECDH:ECDH")

        self.sock.settimeout(30)
        self.sock = sslctx.wrap_socket(self.sock, server_side=True)
        self.sock.settimeout(None)

        ret = self.auth_plain(userlist=userlist)
        return ret


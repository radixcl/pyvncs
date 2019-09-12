from time import sleep
from struct import *
from pyDes import *
import os
from lib import log

class VNCAuth():

    def __init__(self):
        self.getbuff = lambda _: None

    def _mirrorBits(self, key):
        newkey = []
        for ki in range(len(key)):
            bsrc = key[ki]
            btgt = 0
            for i in range(8):
                if ord(bsrc) & (1 << i):
                    btgt = btgt | (1 << 7-i)
            newkey.append(btgt)
        
        return newkey

    def auth(self, sock, password):
        # el cliente encripta el challenge con la contraseña ingresada como key
        pw = (password + '\0' * 8)[:8]
        challenge = os.urandom(16)  # challenge
        sock.send(challenge)    # send challenge
        # obtener desde el cliente el dato encritado
        data = self.getbuff(30)
        # la encriptacion de challenge, con pw como key debe dar data
        
        k = des(self._mirrorBits(pw))
        crypted = k.encrypt(challenge)

        if data == crypted:
            # Handshake successful
            sock.send(pack("!I", 0))
            log.debug(__name__, "Auth OK")
            return True
        else:
            log.debug(__name__, "Invalid auth")
            sleep(3)
            sock.send(pack("!I", 1))
            return False

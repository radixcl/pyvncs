from struct import pack
from lib import log


class NoneAuth:

    def auth(self, sock):
        sock.send(pack("!I", 0))
        log.debug(__name__, "None auth OK")
        return True

import os
import hashlib
from struct import pack, unpack
from lib import log

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.asymmetric import dh
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False


# Apple ARD uses a fixed 128-byte (1024-bit) DH prime
_ARD_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A63A3620FFFFFFFFFFFFFFFF", 16
)
_ARD_GENERATOR = 2
_ARD_KEY_LEN = 128  # bytes


class AppleARDAuth:
    """RFB security type 30 - Apple Remote Desktop authentication.

    Protocol:
    1. Server generates DH keypair, sends: generator(2) + keylen(2) + prime(128) + pubkey(128)
    2. Client sends its DH public key (128 bytes)
    3. Both compute shared secret, derive AES key = MD5(shared_secret)
    4. Server sends 16-byte random challenge encrypted with AES-ECB
    5. Client responds with 16-byte encrypted response (MD5 of challenge+credentials)
    """

    def __init__(self):
        self.getbuff = lambda _: None

    def auth(self, sock, userlist):
        if not _HAS_CRYPTO:
            log.error("Apple ARD auth requires 'cryptography' package")
            return False

        try:
            private_key = dh.generate_private_key(
                parameter_numbers=dh.DHParameterNumbers(_ARD_PRIME, _ARD_GENERATOR),
                backend=default_backend()
            )
            pub_bytes = private_key.public_key().public_numbers().y.to_bytes(_ARD_KEY_LEN, 'big')

            sendbuff = pack("!HH", _ARD_GENERATOR, _ARD_KEY_LEN)
            sendbuff += _ARD_PRIME.to_bytes(_ARD_KEY_LEN, 'big')
            sendbuff += pub_bytes
            sock.sendall(sendbuff)

            client_pub_data = self._recv_exact(sock, _ARD_KEY_LEN)
            if len(client_pub_data) < _ARD_KEY_LEN:
                log.debug(__name__, "Short client DH key")
                return False

            client_pub_int = int.from_bytes(client_pub_data, 'big')
            client_pub_key = dh.DHPublicNumbers(
                client_pub_int,
                dh.DHParameterNumbers(_ARD_PRIME, _ARD_GENERATOR)
            ).public_key(default_backend())

            shared_secret = private_key.exchange(client_pub_key)
            aes_key = hashlib.md5(shared_secret).digest()

            challenge = os.urandom(16)
            cipher_enc = Cipher(algorithms.AES(aes_key), modes.ECB(), backend=default_backend())
            encryptor = cipher_enc.encryptor()
            encrypted_challenge = encryptor.update(challenge) + encryptor.finalize()
            sock.sendall(encrypted_challenge)

            response = self._recv_exact(sock, 16)
            if len(response) < 16:
                log.debug(__name__, "Short ARD response")
                return False

            cipher_dec = Cipher(algorithms.AES(aes_key), modes.ECB(), backend=default_backend())
            decryptor = cipher_dec.decryptor()
            decrypted = decryptor.update(response) + decryptor.finalize()

            for username, password in userlist.items():
                cred = (username + '\x00' * 64)[:64] + (password + '\x00' * 64)[:64]
                expected = hashlib.md5(challenge + cred.encode('iso-8859-1')).digest()
                if decrypted == expected:
                    sock.sendall(pack("!I", 0))
                    log.debug(__name__, "Apple ARD auth OK:", username)
                    return True

            sock.sendall(pack("!I", 1))
            log.debug(__name__, "Apple ARD auth failed")
            return False

        except Exception as e:
            log.debug(__name__, "Apple ARD auth error:", e)
            try:
                sock.sendall(pack("!I", 1))
            except Exception:
                pass
            return False

    def _recv_exact(self, sock, n):
        data = b''
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                break
            data += chunk
        return data

# coding=utf-8
# pyvncs
# Copyright (C) 2017-2018 Matias Fernandez
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from lib import log
from time import sleep
import ssl
import os
import socket
from struct import *

class VeNCrypt():

    # VeNCrypt subtypes
    SUBTYPE_TLSNONE = 250     # TLS + no auth
    SUBTYPE_TLSVNC = 251      # TLS + VNC DES challenge
    SUBTYPE_TLSPLAIN = 252    # TLS + Plain auth
    SUBTYPE_X509NONE = 253    # X509 client cert + no auth
    SUBTYPE_X509VNC = 254     # X509 client cert + VNC DES challenge
    SUBTYPE_X509PLAIN = 255   # X509 client cert + Plain auth
    SUBTYPE_PLAIN = 256       # Plain authentication (no TLS)
    SUBTYPE_TLSPLAIN2 = 259   # TLS + Plain (alternate)

    subtypes = [
        SUBTYPE_TLSNONE,
        SUBTYPE_TLSVNC,
        SUBTYPE_TLSPLAIN,
        SUBTYPE_X509NONE,
        SUBTYPE_X509VNC,
        SUBTYPE_X509PLAIN,
        SUBTYPE_PLAIN,
        SUBTYPE_TLSPLAIN2,
    ]

    def __init__(self, sock):
        self.getbuff = lambda _: None
        self.sock = sock
        self.client_subtype = None
        self.pem_file = None
        self.ssl_socket = None
        log.debug(__name__, "initialized")

        # Send VeNCrypt version 0.2
        version = b'\x00\x02'   # version 0.2
        self.sock.send(version)
        data = self.sock.recv(2)
        if data != version:
            self.sock.send(b'\x01')
            self.sock.close()
            raise Exception("unknown vencrypt version")
        
        # Send master success (0x00)
        self.sock.send(b'\x00')

    def send_subtypes(self):
        # Send list of supported subtypes
        data = pack('!B', len(self.subtypes))
        for i in self.subtypes:
            data += pack('!I', i)
            log.debug(__name__, "subtype", i)
        
        self.sock.send(data)

        # Get client chosen subtype
        data = self.sock.recv(4)
        (data,) = unpack('!I', data)
        log.debug("client subtype", data)
        self.client_subtype = data

    def _recv_exact(self, n):
        """Receive exactly n bytes, handling partial reads."""
        data = b''
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def auth_plain(self, userlist={}):
        """
        Plain authentication over (optionally encrypted) channel.
        
        Protocol:
        4 bytes: username length (big-endian)
        N bytes: username
        4 bytes: password length (big-endian)  
        M bytes: password
        4 bytes: result (0=success, 1=failure)
        """
        try:
            # Read header: two 4-byte integers
            data = self._recv_exact(8)
            if len(data) < 8:
                log.debug(__name__, "Invalid plain auth data length")
                self._send_auth_result(False)
                return False

            user_length, pass_length = unpack('!II', data[:8])
            log.debug(__name__, "user_length:", user_length, "pass_length:", pass_length)

            # Read username
            username = self._recv_exact(user_length)
            username = username.decode('iso-8859-1')

            # Read password
            password = self._recv_exact(pass_length)
            password = password.decode('iso-8859-1')

            log.debug(__name__, "Auth attempt:", username)

            # Check credentials
            if userlist.get(username) == password:
                self._send_auth_result(True)
                log.debug(__name__, "Auth OK for user:", username)
                return True
            else:
                log.debug(__name__, "Invalid auth for user:", username)
                self._send_auth_result(False)
                sleep(1)
                return False
        except Exception as e:
            log.debug(__name__, "Plain auth error:", e)
            self._send_auth_result(False)
            return False

    def _send_auth_result(self, success):
        """Send authentication result (4 bytes, big-endian)."""
        result = pack("!I", 0 if success else 1)
        self.sock.sendall(result)

    def _generate_self_signed_cert(self, cert_path):
        """
        Generate a self-signed certificate if no PEM file is provided.
        Creates cert valid for 10 years.
        Uses cryptography library if available, falls back to openssl command.
        """
        cert_generated = False

        # Try cryptography library first
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
            from datetime import datetime, timedelta
            import ipaddress

            # Generate private key
            key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )

            # Generate certificate
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COUNTRY_NAME, "XX"),
                x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Unknown"),
                x509.NameAttribute(NameOID.LOCALITY_NAME, "Unknown"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PyVNCs"),
                x509.NameAttribute(NameOID.COMMON_NAME, "pyvncs-server"),
            ])

            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.utcnow())
                .not_valid_after(datetime.utcnow() + timedelta(days=3650))
                .add_extension(
                    x509.SubjectAlternativeName([
                        x509.IPAddressInterface(ipaddress.IPv4Interface("127.0.0.1/32")),
                        x509.IPAddressInterface(ipaddress.IPv4Interface("0.0.0.0/0")),
                    ]),
                    critical=False,
                )
                .sign(key, hashes.SHA256())
            )

            # Write private key
            key_path = cert_path + ".key"
            with open(key_path, 'wb') as f:
                f.write(key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                ))

            # Write certificate
            with open(cert_path, 'wb') as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))

            cert_generated = True
            log.debug(__name__, "Generated self-signed cert with cryptography:", cert_path)

        except ImportError:
            # Fallback to openssl command
            import subprocess
            import os
            key_path = cert_path + ".key"

            log.debug(__name__, "cryptography not available, using openssl")

            try:
                # Generate key and cert with openssl
                subprocess.run([
                    'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
                    '-keyout', key_path,
                    '-out', cert_path,
                    '-days', '3650',
                    '-nodes',
                    '-subj', '/C=XX/ST=Unknown/L=Unknown/O=PyVNCs/CN=pyvncs-server',
                    '-addext', 'subjectAltName=IP:127.0.0.1,IP:0.0.0.0'
                ], check=True, capture_output=True)
                cert_generated = True
                log.debug(__name__, "Generated self-signed cert with openssl:", cert_path)
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                log.debug(__name__, "Failed to generate cert:", e)
                return None

        if not cert_generated:
            return None

        return cert_path

    def _load_ssl_context(self, pem_file):
        """
        Create and configure an SSL context for server-side TLS.
        """
        sslctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        
        # Set minimum TLS version to 1.2 for security
        sslctx.minimum_version = ssl.TLSVersion.TLSv1_2
        
        # Configure secure cipher suites
        sslctx.set_ciphers(
            'ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:'
            'ECDHE+AES256:DHE+AES256:!aNULL:!eNULL:!EXPORT:!DES:!RC4:!MD5:'
            '!PSK:!aECDH:!EDH-DSS-DES-CBC3-SHA:!KRB5'
        )

        # Load certificate and key
        if pem_file and os.path.isfile(pem_file):
            sslctx.load_cert_chain(certfile=pem_file)
            log.debug(__name__, "Loaded SSL cert from:", pem_file)
        else:
            # Generate self-signed cert in same directory as script
            cert_dir = os.path.dirname(os.path.abspath(__file__))
            default_cert = os.path.join(cert_dir, 'vencrypt.pem')
            log.debug(__name__, "No cert provided, generating/using:", default_cert)
            pem_file = self._generate_self_signed_cert(default_cert)
            if pem_file is None:
                raise Exception("Unable to generate or load SSL certificate")
            sslctx.load_cert_chain(certfile=pem_file)

        return sslctx

    def auth_tls_plain(self, userlist={}):
        """
        TLS + Plain authentication.
        
        Protocol flow:
        1. TLS handshake (encrypted channel established)
        2. Plain authentication over encrypted channel
        """
        try:
            log.debug(__name__, "Starting TLS handshake")

            # Create SSL context
            sslctx = self._load_ssl_context(self.pem_file)

            # Set socket timeout for TLS handshake
            self.sock.settimeout(30)

            # Wrap socket with SSL
            self.ssl_socket = sslctx.wrap_socket(
                self.sock,
                server_side=True
            )
            self.ssl_socket.settimeout(None)  # Non-blocking after handshake

            log.debug(__name__, "TLS handshake completed")

            # All further I/O (auth_plain, _send_auth_result) must go through
            # the encrypted channel — swap self.sock so auth_plain reads from
            # the SSL socket instead of the now-wrapped plain socket.
            self.sock = self.ssl_socket

            # Run plain auth over the encrypted channel
            ret = self.auth_plain(userlist)

            return ret

        except ssl.SSLError as e:
            log.debug(__name__, "SSL/TLS error:", e)
            self._send_auth_result(False)
            return False
        except socket.timeout:
            log.debug(__name__, "TLS handshake timeout")
            self._send_auth_result(False)
            return False
        except Exception as e:
            log.debug(__name__, "TLS auth error:", e)
            self._send_auth_result(False)
            return False

    def _do_tls_handshake(self, require_client_cert=False):
        sslctx = self._load_ssl_context(self.pem_file)
        if require_client_cert:
            sslctx.verify_mode = ssl.CERT_REQUIRED
            sslctx.load_verify_locations(cafile=self.pem_file)
        self.sock.settimeout(30)
        self.ssl_socket = sslctx.wrap_socket(self.sock, server_side=True)
        self.ssl_socket.settimeout(None)
        self.sock = self.ssl_socket
        log.debug(__name__, "TLS handshake completed (client_cert=%s)" % require_client_cert)

    def auth_tls_none(self):
        try:
            self._do_tls_handshake(require_client_cert=False)
            self._send_auth_result(True)
            return True
        except Exception as e:
            log.debug(__name__, "TLSNone error:", e)
            return False

    def auth_tls_vnc(self, password):
        try:
            self._do_tls_handshake(require_client_cert=False)
            from lib.auth.vnc_auth import VNCAuth
            vnc = VNCAuth()
            vnc.getbuff = self._ssl_getbuff
            return vnc.auth(self.sock, password)
        except Exception as e:
            log.debug(__name__, "TLSVnc error:", e)
            return False

    def auth_x509_none(self):
        try:
            self._do_tls_handshake(require_client_cert=True)
            self._send_auth_result(True)
            return True
        except Exception as e:
            log.debug(__name__, "X509None error:", e)
            return False

    def auth_x509_vnc(self, password):
        try:
            self._do_tls_handshake(require_client_cert=True)
            from lib.auth.vnc_auth import VNCAuth
            vnc = VNCAuth()
            vnc.getbuff = self._ssl_getbuff
            return vnc.auth(self.sock, password)
        except Exception as e:
            log.debug(__name__, "X509Vnc error:", e)
            return False

    def auth_x509_plain(self, userlist={}):
        try:
            self._do_tls_handshake(require_client_cert=True)
            return self.auth_plain(userlist)
        except Exception as e:
            log.debug(__name__, "X509Plain error:", e)
            self._send_auth_result(False)
            return False

    def _ssl_getbuff(self, timeout):
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(1024)
        except Exception:
            data = None
        self.sock.settimeout(None)
        return data

    def get_socket(self):
        """
        Return the (possibly SSL-wrapped) socket for further communication.
        Called by VNCServer after successful auth to get the working socket.
        """
        if self.ssl_socket:
            return self.ssl_socket
        return self.sock

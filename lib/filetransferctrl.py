import os
import socket
import time
import zlib
from struct import pack, unpack

from lib import log

# message-type 7 (FileTransfer) is an UltraVNC/TightVNC extension, not part of
# RFC 6143.  Wire format implemented here matches libvncserver (rfbserver.c).

# contentType
FT_DIR_CONTENT_REQUEST = 1
FT_DIR_PACKET = 2
FT_FILE_TRANSFER_REQUEST = 3
FT_FILE_HEADER = 4
FT_FILE_PACKET = 5
FT_END_OF_FILE = 6
FT_ABORT_FILE_TRANSFER = 7
FT_FILE_TRANSFER_OFFER = 8
FT_FILE_ACCEPT_HEADER = 9
FT_COMMAND = 10
FT_COMMAND_RETURN = 11
FT_FILE_CHECKSUMS = 12
FT_FILE_TRANSFER_ACCESS = 14

# contentParam for FT_DIR_CONTENT_REQUEST
R_DIR_CONTENT = 1
R_DRIVES_LIST = 2
R_DIR_RECURSIVE_LIST = 3
R_DIR_RECURSIVE_SIZE = 4

# contentParam for FT_DIR_PACKET / FT_COMMAND_RETURN
A_DIRECTORY = 1
A_FILE = 2
A_DRIVES_LIST = 3
A_DIR_CREATE = 4
A_DIR_DELETE = 5
A_FILE_CREATE = 6
A_FILE_DELETE = 7
A_FILE_RENAME = 8
A_DIR_RENAME = 9

# contentParam for FT_COMMAND
C_DIR_CREATE = 1
C_DIR_DELETE = 2
C_FILE_CREATE = 3
C_FILE_DELETE = 4
C_FILE_RENAME = 5
C_DIR_RENAME = 6

R_ERROR_UNKNOWN_CMD = 1
R_ERROR_CMD = 0xFFFFFFFF

# RFB_FIND_DATA dwFileAttributes bits
ATTR_READONLY = 0x1
ATTR_HIDDEN = 0x2
ATTR_SYSTEM = 0x4
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_NORMAL = 0x80

BLOCK_SIZE = 8192

# 100-ns intervals between 1601-01-01 (FILETIME) and 1970-01-01 (UNIX epoch)
_FILETIME_EPOCH_OFFSET = 11644473600


class FileTransferController:
    """Server-side handler for the UltraVNC/TightVNC file transfer extension.

    Each FileTransfer message on the wire is:

        type(1)=7  contentType(1)  contentParam(1)  pad(1)
        size(4, big-endian)  length(4, big-endian)  data[length]

    The caller (the RFB dispatch loop) has already consumed the leading
    ``type`` byte, so :meth:`handle_message` reads the remaining 11 fixed
    bytes plus the payload.

    All filesystem access is confined to ``root`` (a sandbox directory); any
    client path that resolves outside of it is rejected.  The feature is
    disabled unless ``enabled`` is True.
    """

    def __init__(self, root=None, enabled=False, chunk_size=BLOCK_SIZE):
        self.enabled = enabled
        self.chunk_size = chunk_size
        self.root = os.path.abspath(root) if root else os.path.expanduser('~')
        self._upload_file = None
        self._upload_path = None

    @staticmethod
    def pack_msg(content_type, content_param=0, size=0, data=b''):
        if isinstance(data, str):
            data = data.encode('utf-8', errors='replace')
        return pack('!BBBBII', 7, content_type, content_param, 0,
                    size & 0xFFFFFFFF, len(data)) + data

    def translate_path(self, client_path):
        """Translate a client (DOS-style) path into a relative POSIX path."""
        p = client_path or ''
        if len(p) >= 2 and p[1] == ':':
            p = p[2:]
        p = p.replace('\\', '/')
        return p.lstrip('/')

    def resolve(self, client_path):
        """Resolve a client path to an absolute path inside ``self.root``.

        Returns None when the resolved path escapes the sandbox (path
        traversal) so callers can refuse the operation.
        """
        rel = self.translate_path(client_path)
        root = os.path.abspath(self.root)
        candidate = os.path.abspath(os.path.join(root, rel))
        if candidate != root and not candidate.startswith(root + os.sep):
            return None
        return candidate

    @staticmethod
    def build_find_data(name, is_dir, size, mtime):
        """Build an RFB_FIND_DATA entry (WIN32_FIND_DATA layout).

        Fields are little-endian (Intel/Windows order) even though the
        message header is big-endian; this mirrors libvncserver.  The fixed
        part is 44 bytes followed by the (unterminated) file name; the client
        derives the name length from ``length - 44``.
        """
        attrs = ATTR_DIRECTORY if is_dir else ATTR_ARCHIVE
        ft = int((mtime + _FILETIME_EPOCH_OFFSET) * 10000000) if mtime else 0
        low = ft & 0xFFFFFFFF
        high = (ft >> 32) & 0xFFFFFFFF
        size_low = size & 0xFFFFFFFF
        size_high = (size >> 32) & 0xFFFFFFFF
        header = pack('<IIIIIIIIIII',
                      attrs,
                      low, high,
                      low, high,
                      low, high,
                      size_high, size_low,
                      0, 0)
        return header + name.encode('utf-8', errors='replace')

    def _make_sender(self, sock, send_lock):
        def send(data):
            if send_lock is not None:
                with send_lock:
                    sock.sendall(data)
            else:
                sock.sendall(data)
        return send

    def _close_upload(self):
        if self._upload_file is not None:
            try:
                self._upload_file.close()
            except Exception:
                pass
            self._upload_file = None
            self._upload_path = None

    def handle_message(self, sock, send_lock=None):
        """Read and dispatch one FileTransfer message.

        Returns False when the connection should be closed (feature disabled
        or a fatal framing error), True otherwise.
        """
        if not self.enabled:
            log.debug("FileTransfer: disabled, refusing message")
            return False

        header = sock.recv(11, socket.MSG_WAITALL)
        if len(header) < 11:
            log.debug("FileTransfer: short header read (%d bytes)" % len(header))
            return False
        content_type, content_param, _pad, size, length = unpack('!BBBII', header)

        data = b''
        if length:
            data = sock.recv(length, socket.MSG_WAITALL)
            if len(data) < length:
                log.debug("FileTransfer: short payload read (%d/%d)" % (len(data), length))
                return False

        send = self._make_sender(sock, send_lock)

        try:
            if content_type == FT_FILE_TRANSFER_ACCESS:
                self._handle_access(send)
            elif content_type == FT_DIR_CONTENT_REQUEST:
                self._handle_dir_content_request(send, content_param, data)
            elif content_type == FT_FILE_TRANSFER_REQUEST:
                self._handle_download_request(sock, send, data)
            elif content_type == FT_FILE_TRANSFER_OFFER:
                self._handle_upload_offer(sock, send, size, data)
            elif content_type == FT_FILE_PACKET:
                self._handle_file_packet(size, data)
            elif content_type == FT_END_OF_FILE:
                self._close_upload()
            elif content_type == FT_COMMAND:
                self._handle_command(send, content_param, data)
            elif content_type == FT_ABORT_FILE_TRANSFER:
                self._close_upload()
            else:
                log.debug("FileTransfer: unhandled contentType %d" % content_type)
        except Exception as e:
            log.debug("FileTransfer: error handling contentType %d: %s" % (content_type, e))
            self._close_upload()

        return True

    def _handle_access(self, send):
        send(self.pack_msg(FT_FILE_TRANSFER_ACCESS, 0, 1, b''))

    def _handle_dir_content_request(self, send, content_param, data):
        if content_param == R_DRIVES_LIST:
            send(self.pack_msg(FT_DIR_PACKET, A_DRIVES_LIST, 0, b'C:l\x00\x00'))
            return

        path = data.decode('utf-8', errors='replace')
        send(self.pack_msg(FT_DIR_PACKET, A_DIRECTORY, 0, data))

        abs_path = self.resolve(path)
        if abs_path is None or not os.path.isdir(abs_path):
            send(self.pack_msg(FT_DIR_PACKET, 0, 0, b''))
            return

        try:
            entries = sorted(os.listdir(abs_path))
        except OSError as e:
            log.debug("FileTransfer: listdir error: %s" % e)
            send(self.pack_msg(FT_DIR_PACKET, 0, 0, b''))
            return

        for name in entries:
            full = os.path.join(abs_path, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            is_dir = os.path.isdir(full)
            send(self.pack_msg(FT_DIR_PACKET, A_DIRECTORY, 0,
                               self.build_find_data(name, is_dir, st.st_size, st.st_mtime)))

        send(self.pack_msg(FT_DIR_PACKET, 0, 0, b''))

    def _handle_download_request(self, sock, send, data):
        path = data.decode('utf-8', errors='replace')
        abs_path = self.resolve(path)
        if abs_path is None or not os.path.isfile(abs_path):
            send(self.pack_msg(FT_FILE_HEADER, 0, R_ERROR_CMD, data) + pack('!I', 0))
            return

        try:
            st = os.stat(abs_path)
            timestamp = time.strftime('%m/%d/%Y %H:%M', time.gmtime(st.st_mtime))
            payload = ('%s,%s' % (path, timestamp)).encode('utf-8', errors='replace')
            send(self.pack_msg(FT_FILE_HEADER, 0, st.st_size, payload) + pack('!I', 0))
        except OSError as e:
            log.debug("FileTransfer: stat error: %s" % e)
            send(self.pack_msg(FT_FILE_HEADER, 0, R_ERROR_CMD, data) + pack('!I', 0))
            return

        go = sock.recv(12, socket.MSG_WAITALL)
        if len(go) < 12:
            return
        _t, go_ct, _cp, _pad, go_size, go_len = unpack('!BBBBII', go)
        if go_len:
            sock.recv(go_len, socket.MSG_WAITALL)
        if go_ct != FT_FILE_HEADER or go_size == R_ERROR_CMD:
            return

        try:
            with open(abs_path, 'rb') as f:
                while True:
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break
                    send(self.pack_msg(FT_FILE_PACKET, 0, 0, chunk))
            send(self.pack_msg(FT_END_OF_FILE, 0, 0, b''))
        except OSError as e:
            log.debug("FileTransfer: read error: %s" % e)
            send(self.pack_msg(FT_ABORT_FILE_TRANSFER, 0, 0, b''))

    def _handle_upload_offer(self, sock, send, size, data):
        sock.recv(4, socket.MSG_WAITALL)

        text = data.decode('utf-8', errors='replace')
        path = text.split(',', 1)[0]
        abs_path = self.resolve(path)
        if abs_path is None or not os.path.isdir(os.path.dirname(abs_path)):
            send(self.pack_msg(FT_FILE_ACCEPT_HEADER, 0, R_ERROR_CMD, data))
            return

        try:
            self._upload_file = open(abs_path, 'wb')
            self._upload_path = abs_path
            send(self.pack_msg(FT_FILE_ACCEPT_HEADER, 0, 0, data))
        except OSError as e:
            log.debug("FileTransfer: cannot create file: %s" % e)
            self._close_upload()
            send(self.pack_msg(FT_FILE_ACCEPT_HEADER, 0, R_ERROR_CMD, data))

    def _handle_file_packet(self, size, data):
        if self._upload_file is None:
            return
        try:
            if size != 0:
                data = zlib.decompress(data)
            self._upload_file.write(data)
        except Exception as e:
            log.debug("FileTransfer: write error: %s" % e)
            self._close_upload()

    def _handle_command(self, send, content_param, data):
        text = data.decode('utf-8', errors='replace')

        if content_param == C_DIR_CREATE:
            abs_path = self.resolve(text)
            ok = False
            if abs_path is not None:
                try:
                    os.makedirs(abs_path, exist_ok=True)
                    ok = True
                except OSError as e:
                    log.debug("FileTransfer: mkdir error: %s" % e)
            send(self.pack_msg(FT_COMMAND_RETURN, A_DIR_CREATE,
                               0 if ok else R_ERROR_CMD, data))

        elif content_param == C_FILE_DELETE:
            abs_path = self.resolve(text)
            ok = False
            if abs_path is not None:
                try:
                    if os.path.isdir(abs_path):
                        os.rmdir(abs_path)
                    else:
                        os.unlink(abs_path)
                    ok = True
                except OSError as e:
                    log.debug("FileTransfer: delete error: %s" % e)
            send(self.pack_msg(FT_COMMAND_RETURN, A_FILE_DELETE,
                               0 if ok else R_ERROR_CMD, data))

        elif content_param == C_FILE_RENAME:
            idx = text.rfind('*')
            ok = False
            if idx >= 0:
                src = self.resolve(text[:idx])
                dst = self.resolve(text[idx + 1:])
                if src is not None and dst is not None:
                    try:
                        os.rename(src, dst)
                        ok = True
                    except OSError as e:
                        log.debug("FileTransfer: rename error: %s" % e)
            send(self.pack_msg(FT_COMMAND_RETURN, A_FILE_RENAME,
                               0 if ok else R_ERROR_CMD, data))

        else:
            send(self.pack_msg(FT_COMMAND_RETURN, content_param, R_ERROR_CMD, data))

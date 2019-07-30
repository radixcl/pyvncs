import sys
from struct import pack, unpack
from pynput import keyboard
from lib import log
from lib.kbdmap import *

class KeyboardController:
    kbdmap = kbdmap
    kbdkey = ''
    downflag = None
    key = None
    controller = None
    kbd = None

    def __init__(self):
        self.kbd = keyboard
        self.controller = self.kbd.Controller()

    def process_event(self, data):
        # B = U8, L = U32
        (self.downflag, self.key) = unpack("!BxxL", data)
        log.debug("KeyEvent", self.downflag, hex(self.key))

        # special key
        if self.key in self.kbdmap:
            self.kbdkey = self.kbdmap[self.key]
            log.debug("SPECIAL KEY", self.kbdkey)
        else: # normal key
            try:
                self.kbdkey = self.kbd.KeyCode.from_char(chr(self.key))
            except:
                self.kbdkey = None

        # debug keypress to stdout
        try:
            log.debug("KEY:", self.kbdkey)
        except:
            log.debug("KEY: (unprintable)")

        # send the actual keyboard event
        try:
            if self.downflag:
                self.controller.press(self.kbdkey)
            else:
                self.controller.release(self.kbdkey)
        except:
            log.debug("Error sending key")

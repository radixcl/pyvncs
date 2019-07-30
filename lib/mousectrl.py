from struct import pack, unpack
from pynput import mouse
from lib import log

class MouseController():
    def __init__(self):
        self.buttonmask = 0
        self.buttons = [0, 0, 0, 0, 0, 0, 0, 0]
        self.left_pressed = 0
        self.right_pressed = 0
        self.middle_pressed = 0
    
    def process_event(self, data):
        (self.buttonmask, x, y) = unpack("!BHH", data)
        self.buttons[0] = self.buttonmask & int("0000001", 2) # left button
        self.buttons[1] = self.buttonmask & int("0000010", 2) # middle button
        self.buttons[2] = self.buttonmask & int("0000100", 2) # right button
        self.buttons[3] = self.buttonmask & int("0001000", 2) # scroll up
        self.buttons[4] = self.buttonmask & int("0010000", 2) # scroll down

        # set mouse position
        mouse.Controller().position = (x, y)

        # process mouse button events
        if self.buttons[0] and not self.left_pressed:
            log.debug("LEFT PRESSED")
            mouse.Controller().press(mouse.Button.left)
            self.left_pressed = 1
        elif not self.buttons[0] and self.left_pressed:
            log.debug("LEFT RELEASED")
            mouse.Controller().release(mouse.Button.left)
            self.left_pressed = 0

        if self.buttons[1] and not self.middle_pressed:
            log.debug("MIDDLE PRESSED")
            mouse.Controller().press(mouse.Button.middle)
            self.middle_pressed = 1
        elif not self.buttons[1] and self.middle_pressed:
            log.debug("MIDDLE RELEASED")
            mouse.Controller().release(mouse.Button.middle)
            self.middle_pressed = 0

        if self.buttons[2] and not self.right_pressed:
            log.debug("RIGHT PRESSED")
            mouse.Controller().press(mouse.Button.right)
            self.right_pressed = 1
        elif not self.buttons[2] and self.right_pressed:
            log.debug("RIGHT RELEASED")
            mouse.Controller().release(mouse.Button.right)
            self.right_pressed = 0

        if self.buttons[3]:
            log.debug("SCROLLUP PRESSED")
            mouse.Controller().scroll(0, 2)

        if self.buttons[4]:
            log.debug("SCROLLDOWN PRESSED")
            mouse.Controller().scroll(0, -2)
        
        #log.debug("PointerEvent", buttonmask, x, y)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pynput import mouse, keyboard

keyboard.Controller().press(keyboard.Key.shift)
keyboard.Controller().press('a')

#keyboard.Controller().modifiers(keyboard.Key().shift)
#print("Shift:", keyboard.Controller().shift_pressed)
#keyboard.Controller().press('a')
#keyboard.Controller().release('a')
#keyboard.Controller().release(keyboard.Key.shift_r)



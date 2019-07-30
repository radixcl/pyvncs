import sys
from pynput import keyboard

__all__ = ['kbdmap']

kbdmap = {
    0xff08: keyboard.Key.backspace,
    0xff09: keyboard.Key.tab,
    0xff0d: keyboard.Key.enter,
    0xff1b: keyboard.Key.esc,
    0xff63: keyboard.Key.insert if hasattr(keyboard.Key, "insert") else None,
    0xffff: keyboard.Key.delete,
    0xff50: keyboard.Key.home,
    0xff57: keyboard.Key.end,
    0xff55: keyboard.Key.page_up,
    0xff56: keyboard.Key.page_down,
    0xff51: keyboard.Key.left,
    0xff52: keyboard.Key.up,
    0xff53: keyboard.Key.right,
    0xff54: keyboard.Key.down,
    0xffbe: keyboard.Key.f1,
    0xffbf: keyboard.Key.f2,
    0xffc0: keyboard.Key.f3,
    0xffc1: keyboard.Key.f4,
    0xffc2: keyboard.Key.f5,
    0xffc3: keyboard.Key.f6,
    0xffc4: keyboard.Key.f7,
    0xffc5: keyboard.Key.f8,
    0xffc6: keyboard.Key.f9,
    0xffc7: keyboard.Key.f10,
    0xffc8: keyboard.Key.f11,
    0xffc9: keyboard.Key.f12,
    0xffca: keyboard.Key.f13,
    0xffcb: keyboard.Key.f14,
    0xffcc: keyboard.Key.f15,
    0xffcd: keyboard.Key.f16,
    0xffce: keyboard.Key.f17,
    0xffcf: keyboard.Key.f18,
    0xffd0: keyboard.Key.f19,
    0xffd1: keyboard.Key.f20,
    0xffe1: keyboard.Key.shift_l,
    0xffe2: keyboard.Key.shift_r,
    0xffe3: keyboard.Key.ctrl_l,
    0xffe4: keyboard.Key.ctrl_r,
    0xffe7: None,   # "KEY_MetaLeft"
    0xffe8: None,   # "KEY_MetaRight"
    #0xffe9: keyboard.Key.cmd_l,
    0xffe9: keyboard.Key.alt,
    0xffea: keyboard.Key.alt_gr, # "KEY_AltRight"
    0xff14: keyboard.Key.scroll_lock if hasattr(keyboard.Key, "scroll_lock") else None,
    0xff15: keyboard.Key.print_screen if hasattr(keyboard.Key, "print_screen") else None, # "KEY_Sys_Req"
    0xff7f: keyboard.Key.num_lock if hasattr(keyboard.Key, "num_lock") else None,
    0xffe5: keyboard.Key.caps_lock,
    0xff13: keyboard.Key.pause if hasattr(keyboard.Key, "pause") else None,
    0xffeb: keyboard.Key.cmd_r, # "KEY_Super_L"
    0xffec: keyboard.Key.cmd_r, # "KEY_Super_R"
    0xffed: None, # "KEY_Hyper_L"
    0xffee: None, # "KEY_Hyper_R"
    0xffb0: None, # "KEY_KP_0"
    0xffb1: None, #  "KEY_KP_1"
    0xffb2: None, #  "KEY_KP_2"
    0xffb3: None, #  "KEY_KP_3"
    0xffb4: None, #  "KEY_KP_4"
    0xffb5: None, #  "KEY_KP_5"
    0xffb6: None, #  "KEY_KP_6"
    0xffb7: None, #  "KEY_KP_7"
    0xffb8: None, #  "KEY_KP_8"
    0xffb9: None, #  "KEY_KP_9"
    0xff8d: None, #  "KEY_KP_Enter"
    0x002f: "/",  # KEY_ForwardSlash
    0x005c: "\\",  # KEY_BackSlash
    0x0020: keyboard.Key.space, # "KEY_SpaceBar"
    0xff7e: keyboard.Key.alt_gr, # altgr, at least on a mac (?)
    #0xfe03: keyboard.Key.alt_l,
    0xfe03: keyboard.Key.cmd_l,
}

if sys.platform == "darwin":
    kbdmap[0xffe2] = keyboard.Key.shift

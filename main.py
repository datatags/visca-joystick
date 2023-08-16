import time

from visca_over_ip.exceptions import ViscaException
from numpy import interp

from config import ips, sensitivity_tables, help_text, Camera, long_press_time
from startup_shutdown import shut_down, ask_to_configure

import inputs

invert_tilt = True
cam = None
joystick = None
button_hold_trackers = []
far_focus_down = False
near_focus_down = False

class ButtonHoldTracker:
    def __init__(self, code, value=1) -> None:
        self.code = code
        self.time = None
        self.value = value
    
    def set(self) -> None:
        self.time = time.time()
    
    def reset(self) -> None:
        self.time = None
    
    def is_set(self) -> bool:
        return self.time is not None
    
    def is_long_press(self) -> bool:
        return self.is_set() and time.time() - self.time > long_press_time

class CameraSelect:
    def __init__(self, camera) -> None:
        self.camera = camera

    def run(self, event) -> None:
        if event.state == 1:
            return
        global cam
        cam = connect_to_camera(self.camera)

class Movement:
    def __init__(self, action, invert=False) -> None:
        self.action = action
        self.invert = invert

    def convert_to_sensitivity(self, value: float) -> int:
        sign = 1 if value >= 0 else -1
        table = sensitivity_tables[self.action]

        return sign * round(
            interp(abs(value), table['joy'], table['cam'])
        )    

    def run(self, event) -> None:
        value = event.state
        if event.code.endswith("Z"):
            value /= 255
        elif event.code.endswith("X") or event.code.endswith("Y"):
            value /= 32768

        if self.invert:
                value *= -1
        if value < 0.1:
            value = 0
        else:
            value = self.convert_to_sensitivity(value)

        if self.action == "pan":
            cam.pan(value)
        elif self.action == "tilt":
            cam.tilt(value)
        elif self.action == "zoom":
            cam.zoom(value)

class Focus:
    def __init__(self, action) -> None:
        self.action = action
        self.ignore_next = False

    def toggle_focus_mode(self) -> None:
        manual_focus = cam.get_focus_mode() == 'manual'
        if manual_focus:
            cam.set_focus_mode('auto')
            print('Auto focus')
        else:
            cam.set_focus_mode('manual')
            print('Manual focus')
            self.ignore_next = True

    def run(self, event) -> None:
        if self.action == "near":
            global near_focus_down
            near_focus_down = event.state
        elif self.action == "far":
            global far_focus_down
            far_focus_down = event.state

        if near_focus_down and far_focus_down:
            self.toggle_focus_mode()
            return
        
        if event.state == 1 or cam.get_focus_mode() == 'auto':
            return

        if self.ignore_next:
            self.ignore_next = False
            return
        
        if self.action == "near":
            cam.manual_focus(-1)
        elif self.action == "far":
            cam.manual_focus(1)

class Preset:
    def __init__(self, preset_positive, preset_negative=None) -> None:
        self.preset_negative = preset_negative
        self.preset_positive = preset_positive
        self.ignore_next = False
        if self.preset_negative is not None:
            self.negative_tracker = ButtonHoldTracker(self.preset_negative, -1)
            button_hold_trackers.append(self.negative_tracker)
        self.positive_tracker = ButtonHoldTracker(self.preset_positive, 1)
        button_hold_trackers.append(self.positive_tracker)
    
    def signed_code(self, event) -> str:
        if self.preset_negative is None:
            return event.code
        return event.code + ("P" if event.state == -1 else "N")

    def run(self, event) -> None:
        if event.state != 0:
            if event.state == 1:
                self.positive_tracker.set()
            elif event.state == -1:
                self.negative_tracker.set()
            return
        self.positive_tracker.reset()
        self.negative_tracker.reset()
        if self.ignore_next:
            self.ignore_next = False
            return
        if event.state == -1:
            cam.recall_preset(self.preset_negative)
        elif event.state == 1:
            cam.recall_preset(self.preset_positive)
    
    def check_held(self):
        if self.positive_tracker.is_long_press():
            cam.set_preset(self.preset_positive)
        elif self.negative_tracker.is_long_press():
            cam.set_preset(self.preset_negative)
        else:
            return
        self.ignore_next = True

class ExitAction:
    def run(self, event) -> None:
        if event.state == 1:
            return
        shut_down(joystick, cam)

class InvertTilt:
    def run(self, event) -> None:
        if event.state == 1:
            return
        global invert_tilt
        invert_tilt = not invert_tilt
        print("Invert tilt: " + str(invert_tilt))

mappings = {
    'ABS_X': Movement('pan'),
    'ABS_Y': Movement('tilt'),
    'ABS_Z': Movement('zoom'),
    'ABS_RZ': Movement('zoom'),
    'BTN_TL': Focus('near'),
    'BTN_TR': Focus('far'),
    'BTN_SOUTH': CameraSelect(0),
    'BTN_EAST': CameraSelect(1),
    'BTN_NORTH': CameraSelect(2),
    'ABS_HAT0X': Preset(2, 0),
    'ABS_HAT0Y': Preset(3, 1),
    'BTN_SELECT': ExitAction(),
    'BTN_START': InvertTilt(),
}

# mappings = {
#     'cam_select': {0: 0, 1: 1, 3: 2},
#     'movement': {'pan': 0, 'tilt': 1, 'zoom': 3},
#     'focus': {'near': 9, 'far': 10},
#     'preset': {11: 8, 12: 9, 13: 10, 14: 11},
#     'other': {'exit': 6, 'invert_tilt': 7, 'configure': 3}
# }

def joystick_init():
    """Initializes the gamepad.
    """
    global joystick, joystick_reset_time

    joystick = inputs.get_gamepad()

def connect_to_camera(cam_index) -> Camera:
    """Connects to the camera specified by cam_index and returns it"""
    global cam

    if cam:
        cam.zoom(0)
        cam.pantilt(0, 0)
        cam.close_connection()

    cam = Camera(ips[cam_index])

    try:
        cam.zoom(0)
    except ViscaException:
        pass

    print(f"Camera {cam_index + 1}")

    return cam

def main_loop():
    while True:
        for event in joystick:
            if event.ev_type == "Sync":
                continue
            if event.code in mappings:
                mappings[event.code].run(event)
        time.sleep(0.03)


if __name__ == "__main__":
    print('Welcome to VISCA Joystick!')
    joystick_init()
    print()
    print(help_text)
    ask_to_configure(joystick)
    cam = connect_to_camera(0)

    while True:
        try:
            main_loop()
        except Exception as exc:
            print(exc)

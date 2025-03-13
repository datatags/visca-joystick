import json
import time
import inputs
import requests
from numpy import interp
from collections import namedtuple
from requests.auth import HTTPDigestAuth
from visca_over_ip.exceptions import NoQueryResponse, ViscaException

from startup_shutdown import shut_down, ask_to_configure
from controller_input import check_gamepad, event_queue, positions
from config import ips, sensitivity_tables, help_text, Camera, long_press_time, autotracking

invert_tilt = True
cam = None
current_cam_index = None
joystick = None
far_focus_down = False
near_focus_down = False
pan = 0
tilt = 0
pan_lock = False
autotracking_session = None
autotracking_url = None
autotracking_data = {
    "trackMode": "this should be automatically updated as needed",
    "delayTime": "1",
    "startPosition": autotracking["start"] or "now",
    "stopPosition": autotracking["stop"] or "now",
    "lossPosition": autotracking["loss"] or "now",
    "lossTime": "6"
}
tracking_now = False

FakeEvent = namedtuple("FakeEvent", "ev_type code state")

class ButtonHoldTracker:
    def __init__(self) -> None:
        self.time = None
    
    def set(self) -> None:
        self.time = time.time()
    
    def reset(self) -> None:
        self.time = None
    
    def is_set(self) -> bool:
        return self.time is not None
    
    def is_long_press(self) -> bool:
        return self.is_set() and time.time() - self.time > long_press_time

class AltButtonForHold:
    def __init__(self, momentary, hold) -> None:
        self.tracker = ButtonHoldTracker()
        self.momentary = momentary
        self.hold = hold
    
    def run(self, event) -> None:
        if event.state == 1:
            self.tracker.set()
            return
        if self.tracker.is_long_press():
            self.hold.run(event)
        else:
            self.momentary.run(event)

class CameraSelect:
    def __init__(self, camera: int) -> None:
        self.camera = camera

    def run(self, event) -> None:
        if event.state == 1:
            return
        try:
            connect_to_camera(self.camera)
            return
        except NoQueryResponse:
            # Scope issue, see below
            pass
        except ViscaException:
            print("Camera refused connection (need to disable autotracking?)")
        # current_cam_index hasn't updated yet
        print(f'Could not connect to {self.camera + 1}, going back to {current_cam_index + 1}')

        # If this line is in the except block, it doesn't work,
        # complaining that a socket is already bound to that port.
        # I'm thinking it has something to do with scope but I'm not sure.
        connect_to_camera(current_cam_index)

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

        value = self.convert_to_sensitivity(value)
        if self.invert:
            value *= -1

        if self.action == "pan":
            # Pan value should not be updated if pan lock is on
            if not pan_lock:
                global pan
                pan = value
            return
        # Tilt and zoom should be disabled completely while in pan lock
        if pan_lock:
            value = 0
        if self.action == "tilt":
            global tilt
            tilt = value
        elif self.action == "zoom":
            cam.zoom(value)
            print(f"Zooming to {value}")

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

        if cam.get_focus_mode() == 'auto':
            return

        if event.state == 0:
            cam.manual_focus(0)
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
            self.negative_tracker = ButtonHoldTracker()
        self.positive_tracker = ButtonHoldTracker()

    def run(self, event) -> None:
        if event.state != 0:
            if event.state == 1:
                self.positive_tracker.set()
            elif event.state == -1:
                self.negative_tracker.set()
            return
        self.check_held()
        positive = self.positive_tracker.is_set()
        negative = self.negative_tracker.is_set()
        if not positive and not negative:
            return
        self.positive_tracker.reset()
        self.negative_tracker.reset()
        if self.ignore_next:
            self.ignore_next = False
            return
        if negative:
            preset = self.preset_negative
        elif positive:
            preset = self.preset_positive
        else:
            return
        try:
            cam.recall_preset(preset)
        except ViscaException:
            print("Preset recall failure (does the preset exist?)")
    
    def check_held(self):
        if self.positive_tracker.is_long_press():
            cam.save_preset(self.preset_positive)
        elif self.negative_tracker.is_long_press():
            cam.save_preset(self.preset_negative)
        else:
            return
        self.ignore_next = True

class ExitAction:
    def run(self, event) -> None:
        if event.state == 1:
            return
        shut_down(cam)

class InvertTilt:
    def run(self, event) -> None:
        if event.state == 1:
            return
        global invert_tilt
        invert_tilt = not invert_tilt
        print("Invert tilt: " + str(invert_tilt))

class PanLock:
    """When pan lock is on, the current pan speed will be held constant
    and tilt and zoom will be disabled.
    Pan lock is only enabled while holding the button, it's not a toggle."""
    def run(self, event) -> None:
        global pan_lock
        pan_lock = event.state == 1
        print(f"Pan lock: {pan_lock}")
        # Reset pan when pan lock is disabled
        if not pan_lock:
            global pan
            pan = 0

class OnePushFocus:
    """Triggers 'one push auto focus'"""
    def run(self, event) -> None:
        if event.state == 1:
            return
        # Dunno why the library doesn't use this command for the one push auto focus
        cam._send_command("04 38 04")
        print("Focusing")

class ExposureWhiteBalanceManual:
    """Resets exposure and white balance to manual mode"""
    def run(self, event) -> None:
        if event.state == 1:
            return
        cam.autoexposure_mode('manual')
        cam.white_balance_mode('manual')
        print("Exposure and white balance set to manual")

class AutoTracking:
    """Enables or disables autotracking"""
    def run(self, event) -> None:
        global tracking_now
        if tracking_now:
            autotracking_data["trackMode"] = "off"
        else:
            autotracking_data["trackMode"] = "tracking"
        tracking_now = not tracking_now
        resp = autotracking_session.post(autotracking_url, json=autotracking_data)
        if not resp.ok:
            print(f"Failed to set autotracking state: {resp.status_code}")
            return
        if tracking_now:
            print("Autotracking is now on")
        else:
            print("Autotracking is now off")

mappings = {
    'ABS_X': Movement('pan', invert=True),
    'ABS_Y': Movement('tilt'),
    'ABS_Z': Movement('zoom', invert=True),
    'ABS_RX': Movement('pan', invert=True),
    'ABS_RZ': Movement('zoom'),
    'BTN_TL': Focus('near'),
    'BTN_TR': Focus('far'),
    'BTN_SOUTH': CameraSelect(0),
    'BTN_EAST': CameraSelect(1),
    'BTN_NORTH': CameraSelect(2),
    'BTN_WEST': OnePushFocus(),
    'ABS_HAT0X': Preset(2, 0),
    'ABS_HAT0Y': Preset(3, 1),
    'BTN_SELECT': PanLock(),
    'BTN_START': AltButtonForHold(ExposureWhiteBalanceManual(), AutoTracking())
}

def connect_to_camera(cam_index) -> Camera:
    """Connects to the camera specified by cam_index and returns it"""
    global cam
    global current_cam_index

    if cam:
        cam.zoom(0)
        cam.pantilt(0, 0)
        cam.close_connection()

    cam = Camera(ips[cam_index])

    cam.zoom(0)

    # Set this late in case an exception is thrown
    current_cam_index = cam_index
    print(f"Camera {cam_index + 1} connected")

    return cam

def initial_connection():
    global cam
    for i in range(len(ips)):
        try:
            connect_to_camera(i)
            return
        except NoQueryResponse:
            print(f"Couldn't find camera {i + 1}")
    print("Couldn't find any cameras, quitting")
    shut_down(None)

def autotracking_init():
    try:
        with open("credentials.json") as f:
            creds = json.load(f)
            host = creds["host"]
            username = creds["username"]
            password = creds["password"]
    except IOError:
        print("credentials.json not found, will not use auto tracking")
        return
    except KeyError:
        print("credentials.json does not contain all required values, will not use auto tracking")
        return

    global autotracking_session
    autotracking_session = requests.Session()
    autotracking_session.auth = HTTPDigestAuth(username, password)

    global autotracking_url
    autotracking_url = f"http://{host}/api/v1/control/track"
    # We just do this to check that our credentials actually work
    resp = autotracking_session.get(autotracking_url)
    if not resp.ok:
        print("Error response from auto tracking device, will not use auto tracking")
        autotracking_session = None
        return
    print("Auto tracking ready!")

def check_quickedit():
    if not inputs.WIN:
        return
    # https://stackoverflow.com/a/76855923
    import ctypes
    kernel32 = ctypes.windll.kernel32
    # 0x81 = ENABLE_PROCESSED_INPUT & ENABLE_EXTENDED_FLAGS
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), 0x81)

def main_loop():
    while True:
        while not event_queue.empty():
            event = event_queue.get_nowait()
            if event.code in mappings:
                try:
                    mappings[event.code].run(event)
                except ViscaException:
                    print("Control failure")
            else:
                print(f"Unmapped key {event.code} {event.state}")
        for key,position in positions.items():
            if position.reset_changed() and key in mappings:
                try:
                    mappings[key].run(FakeEvent("Absolute", key, positions[key].get()))
                except ViscaException:
                    print("Control failure")
        try:
            cam.pantilt(pan, tilt)
        except ViscaException:
            print("Pan-tilt control failure")
        time.sleep(0.03)

if __name__ == "__main__":
    check_quickedit()
    print('Welcome to VISCA Joystick!')
    check_gamepad()
    print()
    print(help_text)
    ask_to_configure()
    initial_connection()
    autotracking_init()

    try:
        main_loop()
    except KeyboardInterrupt:
        shut_down(cam)

import time
import sys

from visca_over_ip.exceptions import ViscaException, NoQueryResponse
from numpy import interp

from config import ips, sensitivity_tables, help_text, Camera, long_press_time
from startup_shutdown import shut_down, ask_to_configure, discard_input

import inputs
from threading import Thread, Lock, Event
from queue import Queue
from collections import namedtuple

FakeEvent = namedtuple("FakeEvent", "ev_type code state")

invert_tilt = True
cam = None
current_cam_index = None
joystick = None
button_hold_trackers = []
far_focus_down = False
near_focus_down = False
pan = 0
tilt = 0
event_queue = Queue()
pan_lock = False

# Manually implement https://github.com/zeth/inputs/pull/81
PATCHED_EVENT_MAP_LIST = []
for item in inputs.EVENT_MAP:
    if item[0] != "type_codes":
        PATCHED_EVENT_MAP_LIST.append(item)
        continue
    PATCHED_EVENT_MAP_LIST.append(("type_codes", tuple((value, key) for key, value in inputs.EVENT_TYPES)))
inputs.EVENT_MAP = tuple(PATCHED_EVENT_MAP_LIST)

class AxisPosition:
    def __init__(self) -> None:
        self.lock = Lock()
        self.x = 0
        self.changed = False
    
    def set(self, x) -> None:
        if self.x != x:
            with self.lock:
                self.changed = True
                self.x = x
    
    def get(self) -> int:
        with self.lock:
            return self.x
    
    def reset_changed(self) -> bool:
        with self.lock:
            if self.changed:
                self.changed = False
                return True
            return False

positions = {
    'ABS_X': AxisPosition(),
    'ABS_Y': AxisPosition(),
    'ABS_Z': AxisPosition(),
    'ABS_RX': AxisPosition(),
    'ABS_RY': AxisPosition(),
    'ABS_RZ': AxisPosition(),
}

class ButtonHoldTracker:
    def __init__(self, code: str, value: int=1) -> None:
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
    def __init__(self, camera: int) -> None:
        self.camera = camera

    def run(self, event) -> None:
        if event.state == 1:
            return
        try:
            connect_to_camera(self.camera)
        except NoQueryResponse:
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

        if abs(value) < 0.1:
            value = 0
        else:
            value = self.convert_to_sensitivity(value)
            if self.invert:
                value *= -1

        if self.action == "pan":
            # Pan value should be held, i.e. not updated, if pan lock is on
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
            cam.recall_preset(self.preset_negative)
        elif positive:
            cam.recall_preset(self.preset_positive)
    
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

mappings = {
    'ABS_X': Movement('pan', invert=True),
    'ABS_Y': Movement('tilt'),
    'ABS_Z': Movement('zoom', invert=True),
    'ABS_RZ': Movement('zoom'),
    'BTN_TL': Focus('near'),
    'BTN_TR': Focus('far'),
    'BTN_SOUTH': CameraSelect(0),
    'BTN_EAST': CameraSelect(1),
    'BTN_NORTH': CameraSelect(2),
    'ABS_HAT0X': Preset(2, 0),
    'ABS_HAT0Y': Preset(3, 1),
    'BTN_SELECT': PanLock(),
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

def main_loop():
    while True:
        while not event_queue.empty():
            event = event_queue.get_nowait()
            if event.code in mappings:
                mappings[event.code].run(event)
            else:
                print(f"Unmapped key {event.code} {event.state}")
        for key,position in positions.items():
            if position.reset_changed() and key in mappings:
                mappings[key].run(FakeEvent("Absolute", key, positions[key].get()))
        cam.pantilt(pan, tilt)
            
        time.sleep(0.03)

def wait_for_gamepad():
    """Wait for a controller to be connected"""
    devices = 0
    while devices == 0:
        time.sleep(0.5)
        # Reinitialize devices
        inputs.devices = inputs.DeviceManager()
        devices = len(inputs.devices.gamepads)

def get_gamepad_events():
    try:
        return inputs.get_gamepad()
    except inputs.UnpluggedError:
        print("Controller disconnected, waiting for it to be reconnected...")
        wait_for_gamepad()
        print("Controller reconnected")
        # Recurse rather than repeating code
        return get_gamepad_events()

def axis_tracker():
    while True:
        for event in get_gamepad_events():
            if event.ev_type == "Sync":
                continue
            elif event.ev_type == "Absolute":
                if event.code in positions:
                    positions[event.code].set(event.state)
                    continue
            # Send button events and unmapped axes along to main loop to be handled
            # Unmapped axes must be sent because D-Pad is an axis while functioning as buttons
            event_queue.put(event)

def check_gamepad():
    """Check for a connected controller and wait for one if none are connected"""
    if len(inputs.devices.gamepads) == 0:
        print("Waiting for controller to be connected...")
        wait_for_gamepad()
    print("Controller connected")

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

if __name__ == "__main__":
    print('Welcome to VISCA Joystick!')
    check_gamepad()
    print()
    print(help_text)
    ask_to_configure()
    initial_connection()
    axis_tracker_thread = Thread(target=axis_tracker, daemon=True)
    axis_tracker_thread.start()

    try:
        main_loop()
    except KeyboardInterrupt:
        shut_down(cam)

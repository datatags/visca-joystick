import time
import inputs
from queue import Queue, Empty
from threading import Thread, Lock

event_queue = Queue()

# Manually implement https://github.com/zeth/inputs/pull/81
PATCHED_EVENT_MAP_LIST = []
for item in inputs.EVENT_MAP:
    if item[0] != "type_codes":
        PATCHED_EVENT_MAP_LIST.append(item)
        continue
    PATCHED_EVENT_MAP_LIST.append(("type_codes", tuple((value, key) for key, value in inputs.EVENT_TYPES)))
inputs.EVENT_MAP = tuple(PATCHED_EVENT_MAP_LIST)

input_thread_running = False

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

def wait_for_gamepad():
    """Wait for a controller to be connected"""
    devices = 0
    while devices == 0:
        time.sleep(0.5)
        # Reinitialize devices
        inputs.devices = inputs.DeviceManager()
        devices = len(inputs.devices.gamepads)

def get_gamepad_events():
    while True:
        # Ugly but otherwise it just sits in a busy loop waiting for events :(
        # This is essentially a re-implementation of the GamePad __iter__ method with a 1ms delay added
        try:
            if inputs.WIN:
                inputs.devices.gamepads[0]._GamePad__check_state()
            events = inputs.devices.gamepads[0]._do_iter()
        except (IndexError, inputs.UnpluggedError):
            print("Controller disconnected, waiting for it to be reconnected...")
            wait_for_gamepad()
            print("Controller reconnected")
        if events:
            return events
        time.sleep(0.001)

def wait_for_button():
    if not input_thread_running:
        start_input_thread()
    while True:
        try:
            event = event_queue.get(timeout=0.1)
            if event.ev_type == "Key" and event.state == 1:
                return event.code
        except Empty:
            continue
        except KeyboardInterrupt:
            return "CTRL_C"

def input_thread():
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

def start_input_thread():
    global input_thread_running
    Thread(target=input_thread, daemon=True).start()
    input_thread_running = True

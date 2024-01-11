import time
import inputs

from visca_over_ip import Camera
from visca_over_ip.exceptions import NoQueryResponse

from config import ips

def discard_input():
    # Discard any unread events without blocking
    inputs.devices = inputs.DeviceManager()

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
        except IndexError:
            print("Controller disconnected, waiting for it to be reconnected...")
            wait_for_gamepad()
            print("Controller reconnected")
            # Recurse rather than repeating code
        if events:
            return events
        time.sleep(0.001)

def wait_for_button():
    discard_input()
    while True:
        for event in get_gamepad_events():
            if event.ev_type == "Key" and event.state == 1:
                return event.code
            time.sleep(0.05)

def ask_to_configure():
    """Allows the user to configure the cameras or skip this step
    If the user chooses to configure the cameras, they are powered on and preset 9 is recalled
    """
    print('Press Y to power on cameras and recall preset 9 or any other button to skip')
    if wait_for_button() == "BTN_NORTH":
        configure()
    # Prevents button release messages from being read as input in later code
    time.sleep(0.5)
    discard_input()

def configure():
    print(f'Configuring...')

    for ip in ips:
        cam = Camera(ip)
        cam.set_power(True)
        cam.close_connection()

    print("Giving time for cameras to power on...")
    time.sleep(20)

    for ip in ips:
        cam = Camera(ip)
        cam.recall_preset(8)
        cam.close_connection()

    time.sleep(2)

def shut_down(current_camera: Camera):
    """Shuts down the program.
    The user is asked if they want to shut down the cameras as well.
    """
    # If the supplied camera is None, something bad happened so we won't worry about shutting down the cameras.
    # There's a good chance the cameras can't be connected to anyway.
    if current_camera is not None:
        current_camera.close_connection()
        print('Press Y to shut down cameras or any other button to leave them on')
        if wait_for_button() == "BTN_NORTH":
            for index,ip in enumerate(ips):
                # GitHub Copilot wrote this line and I think it's hilarious so I'm keeping it
                print(f"Bye bye camera {index + 1}! :)")
                # This doesn't fail even if the camera is already off
                cam = Camera(ip)
                cam.set_power(False)
                cam.close_connection()
    exit(0)

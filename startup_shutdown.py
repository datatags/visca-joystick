import time
import inputs

from visca_over_ip import Camera

from config import ips


def ask_to_configure():
    """Allows the user to configure the cameras or skip this step
    If the user chooses to configure the cameras, they are powered on and preset 9 is recalled
    """
    print('Press Y to power on cameras and recall preset 9 or any other button to skip')
    for event in inputs.get_gamepad():
        if event.ev_type == "Key":
            if event.code == "BTN_NORTH":
                configure()
            return
        time.sleep(0.05)

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
    if current_camera is not None:
        current_camera.close_connection()

    print('Press Y to shut down cameras or any other button to leave them on')
    for event in inputs.get_gamepad():
        if event.ev_type == "Key":
            if event.code == "BTN_NORTH":
                for ip in ips:
                    cam = Camera(ip)
                    cam.set_power(False)
                    cam.close_connection()
            return
        time.sleep(0.05)

    exit(0)

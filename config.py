from plotly import graph_objects as go

from visca_over_ip import CachingCamera as Camera
# from visca_over_ip import Camera

num_cams = 3

sensitivity_tables = {
    'pan': {'joy': [0, 0.07, 0.3, 0.7, 0.9, 1], 'cam': [0, 0, 2, 8, 15, 20]},
    'tilt': {'joy': [0, 0.07, 0.3, 0.65, 0.85, 1], 'cam': [0, 0, 3, 6, 14, 18]},
    'zoom': {'joy': [0, 0.1, 1], 'cam': [0, 0, 7]},
}

long_press_time = 1

ips = ['192.168.3.243', '192.168.3.242', '192.168.3.244']

# start: Preset to go to when auto tracking starts
# stop: Preset to go to when auto tracking is stopped
# loss: Preset to go to when auto tracking loses its target
# All 3 support None to mean "don't move".
# Presets should be listed as strings, e.g. "0"
autotracking = {
    'start': None,
    'stop': None,
    'loss': None
}

help_text = """Pan & Tilt: Left stick
Pan only: Right stick
Zoom out: Left trigger, Zoom in: Right trigger
Manual focus adjustment: Hold left/right bumper
Toggle auto focus: Press both bumpers simultaneously
One push auto focus: X
Select camera 1: A, 2: B, 3: Y
Presets: Recall: D-pad, Set: D-pad long press
Pan lock: Press and hold hamburger button
Manual white balance + exposure: Press view button
Auto tracking: Press and hold view button
Exit: Ctrl-C"""


if __name__ == '__main__':
    from numpy import interp

    fig = go.Figure()
    for name in ['pan', 'tilt']:
        x = [i * .001 for i in range(1000)]
        y = interp(x, sensitivity_tables[name]['joy'], sensitivity_tables[name]['cam'])
        y = [round(val) for val in y]
        fig.add_trace(go.Scatter(x=x, y=y, name=name))

    fig.show()

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import serial
import time
import threading
import math
import json
import os

# --- Configuration ---
BAUD_RATE = 115200
SERIAL_TIMEOUT = 1
CONFIG_FILE = "knob_visualizer_config.json"

# Global variables
latest_knob_value = 0
arduino_connected = False
ser = None
root = None
serial_port_global = None

knob_value_var = None
status_var = None
mode_display_var = None
slider_var = None
dial_canvas = None
dial_needle_id = None
current_mode_config = {}
steps_for_current_dial = 12
visualizer_frame = None

param_num_detents_var = None
param_detent_strength_var = None
param_steps_per_rev_var = None
param_is_bounded_var = None
param_min_angle_var = None
param_max_angle_var = None
min_angle_entry = None
max_angle_entry = None


# ---- Config Load/Save & COM Port ----
def load_config():
    global serial_port_global
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                serial_port_global = config.get("last_com_port", None)
                print(f"Loaded last COM port: {serial_port_global}")
                return True
        except Exception as e: print(f"Error loading config: {e}")
    return False

def save_config():
    global serial_port_global
    if serial_port_global:
        try:
            with open(CONFIG_FILE, 'w') as f: json.dump({"last_com_port": serial_port_global}, f)
            print(f"Saved COM port {serial_port_global} to config.")
        except Exception as e: print(f"Error saving config: {e}")

def get_com_port_from_user():
    global serial_port_global
    while True:
        port = simpledialog.askstring("Serial Port", "Enter Arduino COM Port (e.g., COM3 or /dev/ttyUSB0):", parent=root)
        if port: serial_port_global = port.strip(); return True
        else:
            if messagebox.askretrycancel("COM Port Needed", "A COM port is required. Retry?"): continue
            else: return False

# ---- Arduino Communication ----
def connect_to_arduino():
    global ser, arduino_connected, status_var, serial_port_global
    if not serial_port_global:
        status_var.set("COM Port not set.")
        if root and not get_com_port_from_user(): status_var.set("Connection cancelled."); return False
        elif not root: print("Serial port not configured."); return False

    if ser and ser.is_open: ser.close()
    try:
        status_var.set(f"Connecting to {serial_port_global}...")
        if root: root.update_idletasks()
        ser = serial.Serial(serial_port_global, BAUD_RATE, timeout=SERIAL_TIMEOUT)
        time.sleep(2)
        arduino_connected = True
        status_var.set(f"Connected: {serial_port_global}")
        print(f"Successfully connected to Arduino on {serial_port_global}")
        save_config()
        send_to_arduino("S")
        return True
    except serial.SerialException as e: status_var.set(f"Error on {serial_port_global}: Port busy/not found.")
    except Exception as e: status_var.set(f"Connection error: {e}")
    print(f"Connection failed: {e if 'e' in locals() else 'Unknown error'}")
    arduino_connected = False; ser = None
    return False

def send_to_arduino(command_str):
    global ser, arduino_connected, status_var
    if arduino_connected and ser:
        try:
            print(f"Sending: {command_str}")
            ser.write(command_str.encode('utf-8') + b'\n')
            status_var.set(f"Sent: {command_str.split(' ')[0]}...")
        except Exception as e:
            status_var.set(f"Error sending to {serial_port_global}.")
            print(f"Error during send: {e}")
            arduino_connected = False;
            if ser: ser.close();
            ser = None
    else:
        status_var.set("Not connected.")
        print("Arduino not connected.")

# --- Parsing and Reading from Arduino ---
def update_gui_param_fields(config_dict):
    if not root: return
    if param_num_detents_var: param_num_detents_var.set(config_dict.get("num_detents", 0))
    if param_detent_strength_var: param_detent_strength_var.set(f"{config_dict.get('detent_strength_P', 10.0):.1f}")
    if param_steps_per_rev_var: param_steps_per_rev_var.set(config_dict.get("steps_per_revolution", 0))
    if param_is_bounded_var: param_is_bounded_var.set(config_dict.get("bounded", False))
    is_bounded = config_dict.get("bounded", False)
    min_a = f"{config_dict.get('min_angle_rad', 0.0):.3f}" if is_bounded else ""
    max_a = f"{config_dict.get('max_angle_rad', 0.0):.3f}" if is_bounded else ""
    if param_min_angle_var: param_min_angle_var.set(min_a)
    if param_max_angle_var: param_max_angle_var.set(max_a)
    state = tk.NORMAL if is_bounded else tk.DISABLED
    if min_angle_entry: min_angle_entry.config(state=state)
    if max_angle_entry: max_angle_entry.config(state=state)

def parse_arduino_settings(line):
    global current_mode_config, steps_for_current_dial
    try:
        if line.startswith("Name: "): current_mode_config["name"] = line.split("Name: ", 1)[1].strip()
        elif line.startswith("Bounded: "): current_mode_config["bounded"] = (line.split("Bounded: ", 1)[1].strip() == "YES")
        elif line.startswith("Min Angle (rad): "): current_mode_config["min_angle_rad"] = float(line.split("Min Angle (rad): ", 1)[1])
        elif line.startswith("Max Angle (rad): "): current_mode_config["max_angle_rad"] = float(line.split("Max Angle (rad): ", 1)[1])
        elif line.startswith("Num Detents: "): current_mode_config["num_detents"] = int(line.split("Num Detents: ", 1)[1])
        elif line.startswith("Detent Strength (P): "): current_mode_config["detent_strength_P"] = float(line.split("Detent Strength (P): ",1)[1])
        elif line.startswith("Steps/Revolution: "):
            steps = int(line.split("Steps/Revolution: ", 1)[1])
            current_mode_config["steps_per_revolution"] = steps
            steps_for_current_dial = steps if steps > 0 else 12
    except (ValueError, IndexError) as e:
        print(f"Error parsing setting line '{line}': {e}")
        if "steps_per_revolution" not in current_mode_config:
            current_mode_config["steps_per_revolution"] = 0; steps_for_current_dial = 12

def read_from_arduino_V2():
    global latest_knob_value, arduino_connected, ser, root, knob_value_var, status_var, current_mode_config
    while True:
        if not arduino_connected or ser is None:
            if root: status_var.set("Disconnected. Retrying...")
            if not connect_to_arduino(): time.sleep(3)
            continue
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if root is None: break
                if line.startswith("STEP:"):
                    try:
                        value_str = line.split(":")[1]
                        new_value = int(value_str)
                        if new_value != latest_knob_value:
                            latest_knob_value = new_value
                            if knob_value_var: knob_value_var.set(f"Value: {latest_knob_value}")
                            update_visuals(latest_knob_value)
                    except (IndexError, ValueError) as e: print(f"Error parsing STEP: '{line}', Error: {e}")
                elif "--- Current Knob Settings ---" in line: current_mode_config = {}
                elif "-----------------------------" in line:
                    if mode_display_var: mode_display_var.set(f"Mode: {current_mode_config.get('name', 'Unknown')}")
                    update_gui_param_fields(current_mode_config)
                    switch_visualizer_type(current_mode_config)
                    print(f"Parsed Config: {current_mode_config}")
                    update_visuals(latest_knob_value)
                else:
                    parse_arduino_settings(line)
                    if line and root: print(f"Arduino: {line}")
        except serial.SerialException as e:
            if root: status_var.set(f"Serial Error on {serial_port_global}. Reconnecting...")
            print(f"Serial error read: {e}")
            arduino_connected = False;
            if ser: ser.close();
            ser = None
        except Exception as e:
            if root: status_var.set(f"Read Error: {e}")
            print(f"Unexpected error read_from_arduino: {e}")
        time.sleep(0.005)

# ---- GUI Update and Visualizer Switching ----
def update_visuals(value):
    current_name = current_mode_config.get("name", "").lower()
    if "volume" in current_name and slider_var and slider_widget:
        s_min = slider_widget.cget("from"); s_max = slider_widget.cget("to")
        clamped_value = max(s_min, min(s_max, float(value)))
        try: slider_var.set(clamped_value)
        except tk.TclError: pass
    elif dial_canvas:
        draw_dial_needle(value)

def clear_visualizer_frame():
    global visualizer_frame, slider_widget, dial_canvas, dial_needle_id, slider_var
    if visualizer_frame:
        for widget in visualizer_frame.winfo_children(): widget.destroy()
    slider_widget = None; slider_var = None
    dial_canvas = None; dial_needle_id = None

def show_slider_visualizer():
    global visualizer_frame, slider_var, slider_widget
    clear_visualizer_frame()
    slider_var = tk.DoubleVar(value=float(latest_knob_value))
    s_min = 0.0
    s_max = float(current_mode_config.get("steps_per_revolution", 100))
    if s_max <= s_min: s_max = s_min + 100
    slider_widget = ttk.Scale(visualizer_frame, from_=s_min, to=s_max,
        orient=tk.HORIZONTAL, variable=slider_var, length=350, state='disabled')
    slider_widget.pack(pady=30, padx=20, fill=tk.X, expand=False) # Changed expand to False
    update_visuals(latest_knob_value)

def draw_static_dial_face():
    if not dial_canvas: return
    dial_canvas.delete("dial_face_elements")
    
    w = dial_canvas.winfo_width(); h = dial_canvas.winfo_height()
    if w <= 10 or h <= 10: dial_canvas.after(50, draw_static_dial_face); return # Increased minimum size
    cx, cy = w/2, h/2
    # Radius is now a larger proportion of the MINIMUM of width/height, for better squareness
    radius = min(cx, cy) * 0.95 # Increased proportion slightly

    dial_canvas.create_oval(cx-radius, cy-radius, cx+radius, cy+radius,
                            outline="gray", width=2, fill="white", tags="dial_face_elements")
    dial_canvas.create_oval(cx-3, cy-3, cx+3, cy+3, fill="black", tags="dial_face_elements")

    if not current_mode_config.get("bounded", False):
        num_visual_ticks = steps_for_current_dial
        if num_visual_ticks > 0 and num_visual_ticks <= 72 :
            for i in range(num_visual_ticks):
                angle = (i / num_visual_ticks) * 2 * math.pi - math.pi / 2
                is_major = (num_visual_ticks <= 16 or i % max(1, (num_visual_ticks // 4)) == 0)
                r_in, r_out, tick_w = (radius*(0.8 if is_major else 0.85), radius*0.9, 2 if is_major else 1)
                x1,y1 = cx+r_in*math.cos(angle), cy+r_in*math.sin(angle)
                x2,y2 = cx+r_out*math.cos(angle), cy+r_out*math.sin(angle)
                dial_canvas.create_line(x1, y1, x2, y2, fill="dimgray", width=tick_w, tags="dial_face_elements")
        elif num_visual_ticks > 72:
            dial_canvas.create_text(cx, cy - radius*0.7, text=f"{num_visual_ticks} steps",
                                    fill="darkgray", font=("Segoe UI", 10), tags="dial_face_elements")
    
    if current_mode_config.get("bounded", False):
        min_rad_actual = current_mode_config.get("min_angle_rad", 0.0)
        max_rad_actual = current_mode_config.get("max_angle_rad", math.pi)
        if max_rad_actual <= min_rad_actual: max_rad_actual = min_rad_actual + 0.1
        midpoint_actual_rad = (min_rad_actual + max_rad_actual) / 2.0
        angle_offset_to_center_top = -math.pi/2 - midpoint_actual_rad
        start_deg_viz = -math.degrees(max_rad_actual + angle_offset_to_center_top)
        end_deg_viz = -math.degrees(min_rad_actual + angle_offset_to_center_top)
        extent_deg_viz = end_deg_viz - start_deg_viz
        if abs(extent_deg_viz) >= 360: extent_deg_viz = -359.9 if extent_deg_viz < 0 else 359.9
        elif abs(extent_deg_viz) < 0.1: extent_deg_viz = -1 if extent_deg_viz <0 else 1
        bound_radius = radius * 0.92
        dial_canvas.create_arc(cx-bound_radius, cy-bound_radius, cx+bound_radius, cy+bound_radius,
                               start=start_deg_viz, extent=extent_deg_viz,
                               outline="deepskyblue", width=4, style=tk.ARC, tags="dial_face_elements")
        num_actual_detents = current_mode_config.get("num_detents", 0)
        if num_actual_detents > 0:
            actual_angular_span = max_rad_actual - min_rad_actual
            detent_spacing_actual_rad = actual_angular_span / num_actual_detents
            for i in range(num_actual_detents + 1):
                detent_actual_rad = min_rad_actual + (i * detent_spacing_actual_rad)
                viz_angle = detent_actual_rad + angle_offset_to_center_top
                is_major_detent = (i==0 or i==num_actual_detents or (num_actual_detents > 4 and i % (num_actual_detents//2) == 0)) # Emphasize ends and midpoint
                r_in = radius*(0.82 if is_major_detent else 0.86)
                r_out = radius*0.90
                tick_w = 2 if is_major_detent else 1
                x1,y1 = cx + r_in*math.cos(viz_angle), cy + r_in*math.sin(viz_angle)
                x2,y2 = cx + r_out*math.cos(viz_angle), cy + r_out*math.sin(viz_angle)
                dial_canvas.create_line(x1, y1, x2, y2, fill="blue", width=tick_w, tags="dial_face_elements")

def show_dial_visualizer():
    global visualizer_frame, dial_canvas
    clear_visualizer_frame()
    # Request a larger initial size for the canvas, and allow it to expand
    dial_canvas = tk.Canvas(visualizer_frame, width=300, height=300, bg="whitesmoke") # Increased requested size
    dial_canvas.pack(pady=10, expand=True, fill=tk.BOTH) # fill=tk.BOTH and expand=True are key
    
    draw_static_dial_face()
    dial_canvas.bind("<Configure>", lambda e: draw_static_dial_face())

    update_visuals(latest_knob_value)

def switch_visualizer_type(config_dict):
    global steps_for_current_dial
    name = config_dict.get("name", "").lower()
    steps_rev = config_dict.get("steps_per_revolution", 0)
    steps_for_current_dial = steps_rev if steps_rev > 0 else 12
    print(f"Switching visualizer for mode: {name}")
    if "volume" in name: show_slider_visualizer()
    else: show_dial_visualizer()

def draw_dial_needle(value):
    global dial_needle_id, dial_canvas, steps_for_current_dial, current_mode_config
    if not dial_canvas: return
    def _do_draw_needle_on_canvas():
        if not dial_canvas: return
        w = dial_canvas.winfo_width(); h = dial_canvas.winfo_height()
        if w <= 10 or h <= 10: dial_canvas.after(50, _do_draw_needle_on_canvas); return

        cx, cy = w/2, h/2
        radius = min(cx, cy) * 0.95 # Match radius used in face drawing
        needle_len = radius * 0.70
        target_angle_rad_on_dial = 0
        current_step_val = float(value)

        if current_mode_config.get("bounded", False):
            min_rad_actual = current_mode_config.get("min_angle_rad", 0.0)
            max_rad_actual = current_mode_config.get("max_angle_rad", math.pi)
            total_steps_in_bound = float(current_mode_config.get("steps_per_revolution", 1))
            if total_steps_in_bound == 0: total_steps_in_bound = 1
            normalized_pos = current_step_val / total_steps_in_bound
            normalized_pos = max(0.0, min(1.0, normalized_pos))
            actual_angular_span = max_rad_actual - min_rad_actual
            if actual_angular_span <= 0: actual_angular_span = 0.001
            needle_actual_rad = min_rad_actual + (normalized_pos * actual_angular_span)
            midpoint_actual_rad = (min_rad_actual + max_rad_actual) / 2.0
            angle_offset_to_center_top = -math.pi/2 - midpoint_actual_rad
            target_angle_rad_on_dial = needle_actual_rad + angle_offset_to_center_top
        else:
            num_visual_dial_steps = steps_for_current_dial
            if num_visual_dial_steps == 0: num_visual_dial_steps = 1
            effective_value = current_step_val % num_visual_dial_steps
            if effective_value < 0: effective_value += num_visual_dial_steps
            target_angle_rad_on_dial = (effective_value / num_visual_dial_steps) * 2 * math.pi - math.pi/2

        x2 = cx + needle_len * math.cos(target_angle_rad_on_dial)
        y2 = cy + needle_len * math.sin(target_angle_rad_on_dial)
        global dial_needle_id
        if dial_needle_id:
            dial_canvas.coords(dial_needle_id, cx, cy, x2, y2)
        else:
            dial_needle_id = dial_canvas.create_line(cx, cy, x2, y2, fill="red", width=3,
                                                   arrow=tk.LAST, arrowshape=(10,12,5), tags="needle")
        dial_canvas.tag_raise("needle")
    _do_draw_needle_on_canvas()

# ---- GUI Creation and Main Loop ----
def on_closing():
    global root, ser
    if messagebox.askokcancel("Quit", "Do you want to quit?"):
        if ser and ser.is_open: ser.close()
        if root: root.quit(); root.destroy()
        root = None

def create_gui():
    global root, knob_value_var, status_var, mode_display_var, visualizer_frame, serial_port_global
    global param_num_detents_var, param_detent_strength_var, param_steps_per_rev_var
    global param_is_bounded_var, param_min_angle_var, param_max_angle_var
    global min_angle_entry, max_angle_entry

    root = tk.Tk()
    root.title("Smart Knob Configurator v2.4") # Version bump
    root.geometry("600x800") # Increased default window size

    param_num_detents_var = tk.StringVar()
    param_detent_strength_var = tk.StringVar()
    param_steps_per_rev_var = tk.StringVar()
    param_is_bounded_var = tk.BooleanVar()
    param_min_angle_var = tk.StringVar()
    param_max_angle_var = tk.StringVar()

    main_paned_window = ttk.PanedWindow(root, orient=tk.VERTICAL)
    main_paned_window.pack(fill=tk.BOTH, expand=True)

    # --- Top Frame (Connection, Info, Visualizer) ---
    # Make this frame itself expand and give more weight to visualizer_frame inside it
    top_frame_container = ttk.Frame(main_paned_window)
    top_frame_container.pack(fill=tk.BOTH, expand=True) # Allow this container to expand
    main_paned_window.add(top_frame_container, weight=4) # MORE weight to top part

    conn_controls_frame = ttk.Frame(top_frame_container, padding="5")
    conn_controls_frame.pack(fill=tk.X, pady=(5,0), side=tk.TOP) # Pack at top
    ttk.Label(conn_controls_frame, text="COM Port:").grid(row=0, column=0, padx=(5,2), pady=5, sticky="w")
    com_port_entry_var = tk.StringVar(value=serial_port_global if serial_port_global else "")
    com_port_entry = ttk.Entry(conn_controls_frame, textvariable=com_port_entry_var, width=15)
    com_port_entry.grid(row=0, column=1, padx=(0,5), pady=5, sticky="ew")
    def com_connect_action():
        global serial_port_global
        entered_port = com_port_entry_var.get()
        if entered_port: serial_port_global = entered_port.strip(); connect_to_arduino()
        else: messagebox.showwarning("Input Error", "Please enter a COM port.")
    connect_btn = ttk.Button(conn_controls_frame, text="Connect", command=com_connect_action)
    connect_btn.grid(row=0, column=2, padx=5, pady=5)
    conn_controls_frame.columnconfigure(1, weight=1)

    info_frame = ttk.Frame(top_frame_container, padding="10")
    info_frame.pack(fill=tk.X, pady=(0,5), side=tk.TOP) # Pack below connection
    knob_value_var = tk.StringVar(value="Value: N/A")
    mode_display_var = tk.StringVar(value="Mode: Unknown")
    ttk.Label(info_frame, textvariable=knob_value_var, font=("Segoe UI", 20, "bold")).pack(pady=3)
    ttk.Label(info_frame, textvariable=mode_display_var, font=("Segoe UI", 14), foreground="darkslateblue").pack(pady=3)

    # Visualizer Area - Give it more space to grow
    visualizer_frame = ttk.Frame(top_frame_container, padding="10", relief="sunken", borderwidth=1)
    visualizer_frame.pack(pady=5, padx=10, fill=tk.BOTH, expand=True, side=tk.TOP) # fill=BOTH, expand=True


    # --- Bottom Frame (Controls: Presets and Parameters) ---
    bottom_frame_container = ttk.Frame(main_paned_window, padding="10")
    main_paned_window.add(bottom_frame_container, weight=1) # LESS weight to bottom part

    control_notebook = ttk.Notebook(bottom_frame_container)
    control_notebook.pack(fill=tk.BOTH, expand=True, pady=5)

    presets_tab = ttk.Frame(control_notebook, padding="10")
    control_notebook.add(presets_tab, text='Presets')
    ttk.Label(presets_tab, text="Quick Presets:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0,5))
    presets_list = [
        ("Unb. Smooth (M0)", "M0"), ("Unb. 12D (M1)", "M1"),
        ("Bnd. 0-180 8D (M2)", "M2"), ("Volume (M3)", "M3"),
        ("Fine Unb. (M4)", "M4"), ("Switch (M5)", "M5")
    ]
    row, col = 1, 0
    for i, (text, cmd) in enumerate(presets_list):
        btn = ttk.Button(presets_tab, text=text, command=lambda c=cmd: send_to_arduino(c), width=18)
        btn.grid(row=row, column=col, padx=3, pady=3, sticky="ew")
        presets_tab.columnconfigure(col, weight=1)
        col += 1;
        if col >= 3: col = 0; row += 1
    query_btn = ttk.Button(presets_tab, text="Refresh Settings (S)", command=lambda: send_to_arduino("S"))
    query_btn.grid(row=row, column=col, columnspan=max(1, 3-col), padx=3, pady=6, sticky="ew")

    params_tab = ttk.Frame(control_notebook, padding="10")
    control_notebook.add(params_tab, text='Edit Parameters')
    param_labels = ["Num Detents:", "Detent Strength P:", "Steps/Revolution:", "Min Angle (rad):", "Max Angle (rad):"]
    param_vars = [param_num_detents_var, param_detent_strength_var, param_steps_per_rev_var,
                  param_min_angle_var, param_max_angle_var]
    param_cmds_prefix = ['d', 'p', 'r', 'n', 'x']
    current_row = 0
    ttk.Label(params_tab, text="Is Bounded:").grid(row=current_row, column=0, sticky="w", padx=5, pady=3)
    bounded_check = ttk.Checkbutton(params_tab, variable=param_is_bounded_var,
                                   command=lambda: send_to_arduino(f"b{int(param_is_bounded_var.get())}"))
    bounded_check.grid(row=current_row, column=1, sticky="w", padx=5, pady=3)
    current_row += 1
    for i, label_text in enumerate(param_labels):
        ttk.Label(params_tab, text=label_text).grid(row=current_row + i, column=0, sticky="w", padx=5, pady=3)
        entry = ttk.Entry(params_tab, textvariable=param_vars[i], width=10)
        entry.grid(row=current_row + i, column=1, sticky="ew", padx=5, pady=3)
        if label_text == "Min Angle (rad):": min_angle_entry = entry
        if label_text == "Max Angle (rad):": max_angle_entry = entry
        def create_set_command(cmd_p, var_p): return lambda: send_to_arduino(f"{cmd_p}{var_p.get()}")
        set_btn = ttk.Button(params_tab, text="Set", width=5, command=create_set_command(param_cmds_prefix[i], param_vars[i]))
        set_btn.grid(row=current_row + i, column=2, sticky="w", padx=5, pady=3)
    params_tab.columnconfigure(1, weight=1)
    update_gui_param_fields(current_mode_config)

    status_var = tk.StringVar(value="Initializing...")
    status_bar = ttk.Label(root, textvariable=status_var, relief=tk.SUNKEN, anchor=tk.W, padding="3", font=("Segoe UI", 9))
    status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    root.protocol("WM_DELETE_WINDOW", on_closing)
    return root

serial_thread_obj = None
if __name__ == "__main__":
    if not load_config(): print("Config not loaded. Will prompt for COM port.")
    gui_root = create_gui()
    serial_thread_obj = threading.Thread(target=read_from_arduino_V2, daemon=True)
    serial_thread_obj.start()
    if serial_port_global: gui_root.after(200, connect_to_arduino)
    gui_root.mainloop()
    print("GUI Closed. Exiting.")

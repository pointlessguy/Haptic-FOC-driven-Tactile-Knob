import tkinter as tk
from tkinter import ttk, messagebox, simpledialog # GUI elements
import serial                             # For serial communication
import time                               # For delays
import threading                          # For non-blocking serial reads
import math                               # For dial calculations (cos, sin, pi, degrees)
import json                               # For saving/loading app configuration (e.g., COM port)
import os                                 # For checking if config file exists

# --- Application Configuration ---
BAUD_RATE = 115200                # Serial baud rate, must match Arduino
SERIAL_TIMEOUT = 1                # Timeout for serial read operations (seconds)
CONFIG_FILE = "knob_visualizer_config.json" # File to store persistent app settings

# --- Global State Variables ---
latest_knob_value = 0             # Stores the most recent step value from the knob
arduino_connected = False         # Flag indicating if serial connection to Arduino is active
ser = None                        # PySerial object for the serial connection
root = None                       # Main Tkinter window object
serial_port_global = None         # Stores the name of the COM port being used (e.g., "COM3")

# Tkinter StringVars for dynamically updating GUI labels
knob_value_var = None
status_var = None
mode_display_var = None

# Variables for GUI visual elements
slider_var = None                 # Tkinter DoubleVar for the volume slider's value
dial_canvas = None                # Tkinter Canvas widget for drawing the dial
dial_needle_id = None             # Stores the ID of the needle line on the dial canvas

# Variables related to knob's current configuration (parsed from Arduino)
current_mode_config = {}          # Dictionary to store the full parsed config
steps_for_current_dial = 12       # Number of visual steps/ticks for the dial display (updated from config)
visualizer_frame = None           # Frame that holds the current visualizer (slider or dial)

# Tkinter StringVars for parameter editing fields in the GUI
param_num_detents_var = None
param_detent_strength_var = None
param_steps_per_rev_var = None
param_is_bounded_var = None       # BooleanVar for the "Is Bounded" checkbutton
param_min_angle_var = None
param_max_angle_var = None

# References to Entry widgets for min/max angle (to enable/disable them)
min_angle_entry = None
max_angle_entry = None


# ---- Configuration Load/Save & COM Port Management ----

# Loads the last used COM port from the config file.
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

# Saves the currently active COM port to the config file.
def save_config():
    global serial_port_global
    if serial_port_global: # Only save if a port has been set
        try:
            with open(CONFIG_FILE, 'w') as f: json.dump({"last_com_port": serial_port_global}, f)
            print(f"Saved COM port {serial_port_global} to config.")
        except Exception as e: print(f"Error saving config: {e}")

# Prompts the user to enter a COM port via a dialog.
def get_com_port_from_user():
    global serial_port_global
    while True: # Loop until a valid port is entered or user cancels definitively
        port = simpledialog.askstring("Serial Port",
                                      "Enter Arduino COM Port (e.g., COM3 or /dev/ttyUSB0):",
                                      parent=root) # Ensure dialog is child of main window
        if port: # User entered something
            serial_port_global = port.strip()
            return True
        else: # User pressed Cancel or closed dialog
            if messagebox.askretrycancel("COM Port Needed", "A COM port is required to connect. Retry?"):
                continue # Loop back to ask again
            else:
                return False # User chose not to retry

# ---- Arduino Communication Functions ----

# Attempts to establish a serial connection with the Arduino.
def connect_to_arduino():
    global ser, arduino_connected, status_var, serial_port_global

    # Ensure a COM port is selected/entered
    if not serial_port_global:
        if status_var: status_var.set("COM Port not set.")
        if root and not get_com_port_from_user(): # If GUI exists, prompt user
            if status_var: status_var.set("Connection cancelled by user.")
            return False
        elif not root: # No GUI yet (e.g., initial call before GUI mainloop)
             print("Serial port not configured. Cannot connect.")
             return False

    if ser and ser.is_open: ser.close() # Close any existing connection

    try:
        if status_var: status_var.set(f"Connecting to {serial_port_global}...")
        if root: root.update_idletasks() # Force GUI update for status message

        ser = serial.Serial(serial_port_global, BAUD_RATE, timeout=SERIAL_TIMEOUT)
        time.sleep(2) # Allow time for Arduino to reset after serial connection

        arduino_connected = True
        if status_var: status_var.set(f"Connected: {serial_port_global}")
        print(f"Successfully connected to Arduino on {serial_port_global}")
        save_config() # Save the successfully used COM port
        send_to_arduino("S") # Request initial settings from Arduino
        return True
    except serial.SerialException as e:
        if status_var: status_var.set(f"Error on {serial_port_global}: Port busy or not found.")
        print(f"Error connecting to Arduino: {e}")
    except Exception as e: # Catch other potential errors
        if status_var: status_var.set(f"Connection error: {e}")
        print(f"Unexpected error during connect: {e}")
    
    # If connection failed
    arduino_connected = False
    ser = None
    return False

# Sends a command string to the connected Arduino.
def send_to_arduino(command_str):
    global ser, arduino_connected, status_var
    if arduino_connected and ser:
        try:
            print(f"Sending to Arduino: {command_str}")
            ser.write(command_str.encode('utf-8') + b'\n') # Commands need a newline
            if status_var: status_var.set(f"Sent: {command_str.split(' ')[0]}...") # Show brief feedback
        except Exception as e: # Catch potential serial write errors
            if status_var: status_var.set(f"Error sending to {serial_port_global}.")
            print(f"Error during send: {e}")
            arduino_connected = False # Assume connection lost on send error
            if ser: ser.close()
            ser = None
    else:
        if status_var: status_var.set("Not connected. Command not sent.")
        print("Arduino not connected. Cannot send command.")

# --- Parsing Arduino Data & Updating GUI Parameter Fields ---

# Populates the GUI's parameter editing fields based on the parsed Arduino configuration.
def update_gui_param_fields(config_dict):
    if not root: return # GUI not initialized

    # Update StringVars, which in turn update the Entry widgets
    if param_num_detents_var: param_num_detents_var.set(config_dict.get("num_detents", 0))
    if param_detent_strength_var: param_detent_strength_var.set(f"{config_dict.get('detent_strength_P', 10.0):.1f}")
    if param_steps_per_rev_var: param_steps_per_rev_var.set(config_dict.get("steps_per_revolution", 0))
    if param_is_bounded_var: param_is_bounded_var.set(config_dict.get("bounded", False))
    
    is_bounded = config_dict.get("bounded", False)
    min_a = f"{config_dict.get('min_angle_rad', 0.0):.3f}" if is_bounded else ""
    max_a = f"{config_dict.get('max_angle_rad', 0.0):.3f}" if is_bounded else ""
    if param_min_angle_var: param_min_angle_var.set(min_a)
    if param_max_angle_var: param_max_angle_var.set(max_a)

    # Enable/disable min/max angle fields based on bounded status
    bounded_entry_state = tk.NORMAL if is_bounded else tk.DISABLED
    if min_angle_entry: min_angle_entry.config(state=bounded_entry_state)
    if max_angle_entry: max_angle_entry.config(state=bounded_entry_state)

# Parses a single line of configuration data received from the Arduino.
def parse_arduino_settings(line):
    global current_mode_config, steps_for_current_dial
    try: # Use try-except for robustness against malformed lines
        if line.startswith("Name: "): current_mode_config["name"] = line.split("Name: ", 1)[1].strip()
        elif line.startswith("Bounded: "): current_mode_config["bounded"] = (line.split("Bounded: ", 1)[1].strip() == "YES")
        elif line.startswith("Min Angle (rad): "): current_mode_config["min_angle_rad"] = float(line.split("Min Angle (rad): ", 1)[1])
        elif line.startswith("Max Angle (rad): "): current_mode_config["max_angle_rad"] = float(line.split("Max Angle (rad): ", 1)[1])
        elif line.startswith("Num Detents: "): current_mode_config["num_detents"] = int(line.split("Num Detents: ", 1)[1])
        elif line.startswith("Detent Strength (P): "): current_mode_config["detent_strength_P"] = float(line.split("Detent Strength (P): ",1)[1])
        elif line.startswith("Steps/Revolution: "):
            steps = int(line.split("Steps/Revolution: ", 1)[1])
            current_mode_config["steps_per_revolution"] = steps
            steps_for_current_dial = steps if steps > 0 else 12 # Default visual ticks for dial
    except (ValueError, IndexError) as e: # Handle errors if a line is not as expected
        print(f"Error parsing setting line '{line}': {e}")
        # Set safe defaults if critical parsing fails (e.g., for steps_per_revolution)
        if "steps_per_revolution" not in current_mode_config: # Check if it was never set
            current_mode_config["steps_per_revolution"] = 0
            steps_for_current_dial = 12

# Reads data from Arduino in a separate thread; updates GUI variables.
def read_from_arduino_V2():
    global latest_knob_value, arduino_connected, ser, root, knob_value_var, status_var, current_mode_config
    while True: # Main loop for the reading thread
        if not arduino_connected or ser is None: # Check connection status
            if root and status_var: status_var.set("Disconnected. Retrying...")
            if not connect_to_arduino(): # Attempt to reconnect
                time.sleep(3) # Wait before retrying if connection failed
            continue # Go to next iteration to check connection again

        try:
            if ser.in_waiting > 0: # Check if data is available
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if root is None: break # Exit thread if main GUI window is closed

                if line.startswith("STEP:"): # Knob step value update
                    try:
                        value_str = line.split(":")[1]
                        new_value = int(value_str)
                        if new_value != latest_knob_value: # Update only on change
                            latest_knob_value = new_value
                            if knob_value_var: knob_value_var.set(f"Value: {latest_knob_value}")
                            update_visuals(latest_knob_value) # Trigger visual update
                    except (IndexError, ValueError) as e: print(f"Error parsing STEP: '{line}', Error: {e}")
                elif "--- Current Knob Settings ---" in line: # Start of a config block
                    current_mode_config = {} # Clear previous config
                elif "-----------------------------" in line: # End of a config block
                    # Update GUI elements that depend on the full configuration
                    if mode_display_var: mode_display_var.set(f"Mode: {current_mode_config.get('name', 'Unknown')}")
                    update_gui_param_fields(current_mode_config) # Populate parameter edit fields
                    switch_visualizer_type(current_mode_config)   # Change slider/dial visual
                    print(f"Parsed Config: {current_mode_config}")
                    update_visuals(latest_knob_value) # Refresh visual with current value
                else: # It's a line within the config block
                    parse_arduino_settings(line)
                    if line and root: print(f"Arduino: {line}") # Optional: Log all Arduino lines

        except serial.SerialException as e: # Handle serial port errors (e.g., disconnect)
            if root and status_var: status_var.set(f"Serial Error on {serial_port_global}. Reconnecting...")
            print(f"Serial error during read: {e}")
            arduino_connected = False
            if ser: ser.close()
            ser = None # Reset serial object
        except Exception as e: # Catch any other unexpected errors in the thread
            if root and status_var: status_var.set(f"Read Error: {e}")
            print(f"Unexpected error in read_from_arduino: {e}")
        
        time.sleep(0.005) # Small delay to keep thread responsive without hogging CPU

# ---- GUI Update Logic & Visualizer Drawing/Switching ----

# Called when `latest_knob_value` changes to update the active visualizer.
def update_visuals(value):
    current_name = current_mode_config.get("name", "").lower()
    # Determine if the current visual is a slider (volume mode) or dial
    if "volume" in current_name and slider_var and slider_widget:
        s_min = slider_widget.cget("from") # Get current range of the slider
        s_max = slider_widget.cget("to")
        clamped_value = max(s_min, min(s_max, float(value))) # Ensure value is within slider range
        try:
            slider_var.set(clamped_value) # Update slider's Tkinter variable
        except tk.TclError: pass # Ignore error if widget is being destroyed
    elif dial_canvas: # If it's not volume, assume it's a dial
        draw_dial_needle(value) # Update the dial's needle position

# Clears the visualizer frame of any existing widgets (slider or dial).
def clear_visualizer_frame():
    global visualizer_frame, slider_widget, dial_canvas, dial_needle_id, slider_var
    if visualizer_frame:
        for widget in visualizer_frame.winfo_children(): widget.destroy() # Remove all children
    # Reset references to specific visualizer widgets
    slider_widget = None; slider_var = None
    dial_canvas = None; dial_needle_id = None

# Creates and displays the slider visualizer (used for "Volume" mode).
def show_slider_visualizer():
    global visualizer_frame, slider_var, slider_widget
    clear_visualizer_frame() # Remove previous visual
    slider_var = tk.DoubleVar(value=float(latest_knob_value)) # Tkinter var for slider

    # Determine slider range from current mode's steps (e.g., 0-100 for volume)
    s_min = 0.0
    s_max = float(current_mode_config.get("steps_per_revolution", 100)) # Default to 100 if not set
    if s_max <= s_min: s_max = s_min + 100 # Ensure valid range

    slider_widget = ttk.Scale(visualizer_frame, from_=s_min, to=s_max,
        orient=tk.HORIZONTAL, variable=slider_var, length=350, state='disabled') # Read-only
    slider_widget.pack(pady=30, padx=20, fill=tk.X, expand=False) # Fills horizontally
    update_visuals(latest_knob_value) # Set initial slider position

# Draws the static (non-moving) parts of the dial face.
def draw_static_dial_face():
    if not dial_canvas: return # Safety check
    dial_canvas.delete("dial_face_elements") # Clear only static face elements, not needle
    
    # Get current canvas dimensions for responsive drawing
    w = dial_canvas.winfo_width(); h = dial_canvas.winfo_height()
    if w <= 10 or h <= 10: dial_canvas.after(50, draw_static_dial_face); return # Canvas not ready
    cx, cy = w/2, h/2 # Center coordinates
    radius = min(cx, cy) * 0.95 # Dial radius based on available space

    # Main dial circle and center dot
    dial_canvas.create_oval(cx-radius, cy-radius, cx+radius, cy+radius,
                            outline="gray", width=2, fill="white", tags="dial_face_elements")
    dial_canvas.create_oval(cx-3, cy-3, cx+3, cy+3, fill="black", tags="dial_face_elements")

    # Draw tick marks for unbounded modes (full circle ticks)
    if not current_mode_config.get("bounded", False):
        num_visual_ticks = steps_for_current_dial # Use parsed visual steps
        if num_visual_ticks > 0 and num_visual_ticks <= 72 : # Draw individual ticks if not too many
            for i in range(num_visual_ticks):
                angle = (i / num_visual_ticks) * 2 * math.pi - math.pi / 2 # Angle for each tick (0 deg at top)
                # Make quarter-turn ticks more prominent
                is_major = (num_visual_ticks <= 16 or i % max(1, (num_visual_ticks // 4)) == 0)
                r_in = radius * (0.8 if is_major else 0.85)
                r_out = radius * 0.9
                tick_w = 2 if is_major else 1
                x1,y1 = cx+r_in*math.cos(angle), cy+r_in*math.sin(angle)
                x2,y2 = cx+r_out*math.cos(angle), cy+r_out*math.sin(angle)
                dial_canvas.create_line(x1, y1, x2, y2, fill="dimgray", width=tick_w, tags="dial_face_elements")
        elif num_visual_ticks > 72: # If too many ticks, just show the count
            dial_canvas.create_text(cx, cy - radius*0.7, text=f"{num_visual_ticks} steps",
                                    fill="darkgray", font=("Segoe UI", 10), tags="dial_face_elements")
    
    # Draw visual indicators for bounded modes (arc and detent ticks within the arc)
    if current_mode_config.get("bounded", False):
        min_rad_actual = current_mode_config.get("min_angle_rad", 0.0)
        max_rad_actual = current_mode_config.get("max_angle_rad", math.pi)
        if max_rad_actual <= min_rad_actual: max_rad_actual = min_rad_actual + 0.1 # Ensure some extent

        # Calculate offset to center the bounded range at the top (12 o'clock) of the dial
        midpoint_actual_rad = (min_rad_actual + max_rad_actual) / 2.0
        angle_offset_to_center_top = -math.pi/2 - midpoint_actual_rad # -PI/2 is 12 o'clock

        # Convert actual bound angles to visual degrees for Tkinter arc drawing
        # Tkinter angles: 0 deg at 3 o'clock, counter-clockwise.
        # We apply the offset and negate because Tkinter's start/extent can be tricky.
        start_deg_viz = -math.degrees(max_rad_actual + angle_offset_to_center_top)
        end_deg_viz   = -math.degrees(min_rad_actual + angle_offset_to_center_top)
        extent_deg_viz = end_deg_viz - start_deg_viz # This can be negative
        # Normalize extent for create_arc (often expects positive for counter-clockwise extent from start)
        # A common way is to ensure it's within (-360, 360) and consistent.
        if abs(extent_deg_viz) >= 360: extent_deg_viz = -359.9 if extent_deg_viz < 0 else 359.9
        elif abs(extent_deg_viz) < 0.1: extent_deg_viz = -1 if extent_deg_viz <0 else 1 # Ensure some arc

        bound_radius = radius * 0.92 # Arc slightly inside main dial
        dial_canvas.create_arc(cx-bound_radius, cy-bound_radius, cx+bound_radius, cy+bound_radius,
                               start=start_deg_viz, extent=extent_deg_viz,
                               outline="deepskyblue", width=4, style=tk.ARC, tags="dial_face_elements")

        # Draw detent ticks within the bounded arc
        num_actual_detents = current_mode_config.get("num_detents", 0)
        if num_actual_detents > 0:
            actual_angular_span = max_rad_actual - min_rad_actual
            # Spacing of detents within the actual physical bounded range
            detent_spacing_actual_rad = actual_angular_span / num_actual_detents
            
            for i in range(num_actual_detents + 1): # Iterate to include a tick at both ends
                detent_actual_rad = min_rad_actual + (i * detent_spacing_actual_rad)
                # Apply the same centering offset to visualize these detents on the dial
                viz_angle_for_detent_tick = detent_actual_rad + angle_offset_to_center_top
                
                # Make end detent ticks and potentially midpoint more prominent
                is_major_detent_tick = (i==0 or i==num_actual_detents or \
                                       (num_actual_detents > 2 and i == num_actual_detents//2 and num_actual_detents % 2 == 0) or \
                                       (num_actual_detents > 3 and i % (num_actual_detents//2) == 0)
                                       )

                r_in_detent = radius*(0.82 if is_major_detent_tick else 0.86)
                r_out_detent = radius*0.90
                tick_w_detent = 2 if is_major_detent_tick else 1

                x1d,y1d = cx + r_in_detent*math.cos(viz_angle_for_detent_tick), cy + r_in_detent*math.sin(viz_angle_for_detent_tick)
                x2d,y2d = cx + r_out_detent*math.cos(viz_angle_for_detent_tick), cy + r_out_detent*math.sin(viz_angle_for_detent_tick)
                dial_canvas.create_line(x1d, y1d, x2d, y2d, fill="blue", width=tick_w_detent, tags="dial_face_elements")

# Creates and displays the dial visualizer (default for most modes).
def show_dial_visualizer():
    global visualizer_frame, dial_canvas
    clear_visualizer_frame()
    # Request a larger initial size for the canvas, and allow it to expand
    dial_canvas = tk.Canvas(visualizer_frame, width=300, height=300, bg="whitesmoke")
    dial_canvas.pack(pady=10, expand=True, fill=tk.BOTH) # Allow canvas to fill available space
    
    draw_static_dial_face() # Draw the static parts of the dial
    # Redraw static face if canvas size changes (e.g., window resize)
    dial_canvas.bind("<Configure>", lambda e: draw_static_dial_face())

    update_visuals(latest_knob_value) # Draw initial needle position

# Switches the main visualizer type (slider or dial) based on current mode configuration.
def switch_visualizer_type(config_dict):
    global steps_for_current_dial # This is for visual dial ticks
    name = config_dict.get("name", "").lower()
    steps_rev = config_dict.get("steps_per_revolution", 0)
    # For dial visualization, steps_for_current_dial sets number of visual segments on a full dial.
    steps_for_current_dial = steps_rev if steps_rev > 0 else 12 # Default to 12 visual ticks if 0

    print(f"Switching visualizer for mode: {name}")
    if "volume" in name: # Explicitly use slider for "volume" modes
        show_slider_visualizer()
    else: # Default to dial for all other modes
        show_dial_visualizer()

# Draws or updates the position of the dial's needle.
def draw_dial_needle(value):
    global dial_needle_id, dial_canvas, steps_for_current_dial, current_mode_config
    if not dial_canvas: return # Canvas not ready

    # Nested function to handle drawing, allows retrying if canvas not sized
    def _do_draw_needle_on_canvas():
        if not dial_canvas: return # Check again inside nested func
        w = dial_canvas.winfo_width(); h = dial_canvas.winfo_height()
        if w <= 10 or h <= 10: dial_canvas.after(50, _do_draw_needle_on_canvas); return # Canvas not ready

        cx, cy = w/2, h/2 # Current center
        radius = min(cx, cy) * 0.95 # Current radius
        needle_len = radius * 0.70  # Needle length proportional to radius
        
        target_angle_rad_on_dial = 0 # Final visual angle for the needle
        current_step_val = float(value) # Ensure it's a float for calculations

        if current_mode_config.get("bounded", False):
            # Bounded mode: map knob's 0-N steps to the visually centered bounded arc
            min_rad_actual = current_mode_config.get("min_angle_rad", 0.0)
            max_rad_actual = current_mode_config.get("max_angle_rad", math.pi)
            total_steps_in_bound = float(current_mode_config.get("steps_per_revolution", 1))
            if total_steps_in_bound == 0: total_steps_in_bound = 1 # Avoid division by zero
            
            # Normalize current step (0.0 to 1.0) within its defined range
            normalized_pos = current_step_val / total_steps_in_bound
            normalized_pos = max(0.0, min(1.0, normalized_pos)) # Clamp to 0-1 range

            actual_angular_span = max_rad_actual - min_rad_actual
            if actual_angular_span <= 0: actual_angular_span = 0.001 # Ensure positive span
            
            # Calculate the needle's actual physical angle within the bounds
            needle_actual_rad = min_rad_actual + (normalized_pos * actual_angular_span)
            
            # Apply the same offset used for drawing the bound arc to center it at 12 o'clock
            midpoint_actual_rad = (min_rad_actual + max_rad_actual) / 2.0
            angle_offset_to_center_top = -math.pi/2 - midpoint_actual_rad # -PI/2 is 12 o'clock
            target_angle_rad_on_dial = needle_actual_rad + angle_offset_to_center_top
        else: # Unbounded mode
            num_visual_dial_steps = steps_for_current_dial # This is steps_per_revolution from Arduino
            if num_visual_dial_steps == 0: num_visual_dial_steps = 1 # Avoid div by zero
            
            effective_value_for_dial = current_step_val % num_visual_dial_steps
            if effective_value_for_dial < 0: effective_value_for_dial += num_visual_dial_steps
            # Calculate angle for needle, starting at 12 o'clock (-PI/2)
            target_angle_rad_on_dial = (effective_value_for_dial / num_visual_dial_steps) * 2 * math.pi - math.pi/2

        # Calculate needle's end point coordinates
        x2 = cx + needle_len * math.cos(target_angle_rad_on_dial)
        y2 = cy + needle_len * math.sin(target_angle_rad_on_dial)

        global dial_needle_id # We need to modify the global ID
        if dial_needle_id: # If needle exists, update its coordinates
            dial_canvas.coords(dial_needle_id, cx, cy, x2, y2)
        else: # Otherwise, create the needle line
            dial_needle_id = dial_canvas.create_line(cx, cy, x2, y2, fill="red", width=3,
                                                   arrow=tk.LAST, arrowshape=(10,12,5), tags="needle")
        dial_canvas.tag_raise("needle") # Ensure needle is drawn on top of other elements
    
    _do_draw_needle_on_canvas() # Execute the drawing logic


# ---- GUI Creation and Main Application Loop ----

# Handles the window close event.
def on_closing():
    global root, ser
    if messagebox.askokcancel("Quit", "Do you want to quit?"):
        if ser and ser.is_open: ser.close() # Close serial port
        if root: root.quit(); root.destroy() # Properly close Tkinter window
        root = None # Signal threads or other parts that GUI is gone

# Creates the main GUI window and its widgets.
def create_gui():
    global root, knob_value_var, status_var, mode_display_var, visualizer_frame, serial_port_global
    global param_num_detents_var, param_detent_strength_var, param_steps_per_rev_var
    global param_is_bounded_var, param_min_angle_var, param_max_angle_var
    global min_angle_entry, max_angle_entry # References to Entry widgets

    root = tk.Tk()
    root.title("Smart Knob Configurator v2.4")
    root.geometry("600x800") # Default window size

    # Initialize Tkinter StringVars for parameter editing
    param_num_detents_var = tk.StringVar()
    param_detent_strength_var = tk.StringVar()
    param_steps_per_rev_var = tk.StringVar()
    param_is_bounded_var = tk.BooleanVar() # For checkbutton
    param_min_angle_var = tk.StringVar()
    param_max_angle_var = tk.StringVar()

    # Main layout using PanedWindow for resizable sections
    main_paned_window = ttk.PanedWindow(root, orient=tk.VERTICAL)
    main_paned_window.pack(fill=tk.BOTH, expand=True)

    # --- Top Section: Connection, Info, Visualizer ---
    top_frame_container = ttk.Frame(main_paned_window)
    top_frame_container.pack(fill=tk.BOTH, expand=True)
    main_paned_window.add(top_frame_container, weight=4) # Give more space to top section

    # Connection controls (COM port entry and Connect button)
    conn_controls_frame = ttk.Frame(top_frame_container, padding="5")
    conn_controls_frame.pack(fill=tk.X, pady=(5,0), side=tk.TOP)
    ttk.Label(conn_controls_frame, text="COM Port:").grid(row=0, column=0, padx=(5,2), pady=5, sticky="w")
    com_port_entry_var = tk.StringVar(value=serial_port_global if serial_port_global else "")
    com_port_entry = ttk.Entry(conn_controls_frame, textvariable=com_port_entry_var, width=15)
    com_port_entry.grid(row=0, column=1, padx=(0,5), pady=5, sticky="ew")
    def com_connect_action(): # Lambda function for the connect button
        global serial_port_global
        entered_port = com_port_entry_var.get()
        if entered_port: serial_port_global = entered_port.strip(); connect_to_arduino()
        else: messagebox.showwarning("Input Error", "Please enter a COM port.")
    connect_btn = ttk.Button(conn_controls_frame, text="Connect", command=com_connect_action)
    connect_btn.grid(row=0, column=2, padx=5, pady=5)
    conn_controls_frame.columnconfigure(1, weight=1) # Make COM port entry expandable

    # Information display (Knob Value, Current Mode)
    info_frame = ttk.Frame(top_frame_container, padding="10")
    info_frame.pack(fill=tk.X, pady=(0,5), side=tk.TOP)
    knob_value_var = tk.StringVar(value="Value: N/A")
    mode_display_var = tk.StringVar(value="Mode: Unknown")
    ttk.Label(info_frame, textvariable=knob_value_var, font=("Segoe UI", 20, "bold")).pack(pady=3)
    ttk.Label(info_frame, textvariable=mode_display_var, font=("Segoe UI", 14), foreground="darkslateblue").pack(pady=3)

    # Visualizer Area (Dial or Slider)
    visualizer_frame = ttk.Frame(top_frame_container, padding="10", relief="sunken", borderwidth=1)
    visualizer_frame.pack(pady=5, padx=10, fill=tk.BOTH, expand=True, side=tk.TOP)
    # Initial visualizer type will be set after first config is read from Arduino

    # --- Bottom Section: Control Tabs (Presets, Parameters) ---
    bottom_frame_container = ttk.Frame(main_paned_window, padding="10")
    main_paned_window.add(bottom_frame_container, weight=1) # Less space for controls

    control_notebook = ttk.Notebook(bottom_frame_container) # Tabbed interface
    control_notebook.pack(fill=tk.BOTH, expand=True, pady=5)

    # Presets Tab
    presets_tab = ttk.Frame(control_notebook, padding="10")
    control_notebook.add(presets_tab, text='Presets')
    ttk.Label(presets_tab, text="Quick Presets:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0,5))
    presets_list = [ # Ensure these match your Arduino presets
        ("Unb. Smooth (M0)", "M0"), ("Unb. 12D (M1)", "M1"),
        ("Bnd. 0-180 8D (M2)", "M2"), ("Volume (M3)", "M3"),
        ("Fine Unb. (M4)", "M4"), ("Switch (M5)", "M5")
    ]
    row, col = 1, 0 # For grid layout of preset buttons
    for i, (text, cmd) in enumerate(presets_list):
        btn = ttk.Button(presets_tab, text=text, command=lambda c=cmd: send_to_arduino(c), width=18)
        btn.grid(row=row, column=col, padx=3, pady=3, sticky="ew")
        presets_tab.columnconfigure(col, weight=1) # Make buttons expand equally
        col += 1
        if col >= 3: col = 0; row += 1 # 3 buttons per row
    query_btn = ttk.Button(presets_tab, text="Refresh Settings (S)", command=lambda: send_to_arduino("S"))
    query_btn.grid(row=row, column=col, columnspan=max(1, 3-col), padx=3, pady=6, sticky="ew")

    # Parameters Tab (for editing individual settings)
    params_tab = ttk.Frame(control_notebook, padding="10")
    control_notebook.add(params_tab, text='Edit Parameters')
    param_labels = ["Num Detents:", "Detent Strength P:", "Steps/Revolution:", "Min Angle (rad):", "Max Angle (rad):"]
    param_vars = [param_num_detents_var, param_detent_strength_var, param_steps_per_rev_var,
                  param_min_angle_var, param_max_angle_var]
    param_cmds_prefix = ['d', 'p', 'r', 'n', 'x'] # Arduino commands for these params
    
    current_edit_row = 0 # For grid layout in params_tab
    # "Is Bounded" Checkbutton
    ttk.Label(params_tab, text="Is Bounded:").grid(row=current_edit_row, column=0, sticky="w", padx=5, pady=3)
    bounded_check = ttk.Checkbutton(params_tab, variable=param_is_bounded_var,
                                   command=lambda: send_to_arduino(f"b{int(param_is_bounded_var.get())}"))
    bounded_check.grid(row=current_edit_row, column=1, sticky="w", padx=5, pady=3)
    current_edit_row += 1
    
    # Entry fields and "Set" buttons for other parameters
    for i, label_text in enumerate(param_labels):
        ttk.Label(params_tab, text=label_text).grid(row=current_edit_row + i, column=0, sticky="w", padx=5, pady=3)
        entry = ttk.Entry(params_tab, textvariable=param_vars[i], width=10)
        entry.grid(row=current_edit_row + i, column=1, sticky="ew", padx=5, pady=3)
        # Store references to min/max angle entry widgets to enable/disable them later
        if label_text == "Min Angle (rad):": min_angle_entry = entry
        if label_text == "Max Angle (rad):": max_angle_entry = entry
        
        # Helper to create lambda with correct scope for command and variable
        def create_set_command(cmd_prefix, tk_var):
            return lambda: send_to_arduino(f"{cmd_prefix}{tk_var.get()}")
        
        set_btn = ttk.Button(params_tab, text="Set", width=5,
                             command=create_set_command(param_cmds_prefix[i], param_vars[i]))
        set_btn.grid(row=current_edit_row + i, column=2, sticky="w", padx=5, pady=3)
    
    params_tab.columnconfigure(1, weight=1) # Allow entry fields to expand somewhat
    update_gui_param_fields(current_mode_config) # Initial population and state of param fields

    # Status Bar at the bottom of the window
    status_var = tk.StringVar(value="Initializing...") # Ensure status_var is ready
    status_bar = ttk.Label(root, textvariable=status_var, relief=tk.SUNKEN, anchor=tk.W, padding="3", font=("Segoe UI", 9))
    status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    
    root.protocol("WM_DELETE_WINDOW", on_closing) # Handle window close event
    return root

# --- Main Script Execution ---
serial_thread_obj = None # Global reference to the serial thread

if __name__ == "__main__":
    if not load_config(): # Try to load last used COM port
        print("Config not loaded. Will prompt for COM port if GUI is started.")

    gui_root = create_gui() # Create the main window and widgets

    # Start the serial communication thread after GUI is created
    serial_thread_obj = threading.Thread(target=read_from_arduino_V2, daemon=True) # Daemon thread exits with main
    serial_thread_obj.start()

    # If a COM port was loaded from config, attempt to connect automatically after a short delay
    if serial_port_global:
        gui_root.after(200, connect_to_arduino) # `after` schedules call in Tkinter main loop

    gui_root.mainloop() # Start the Tkinter event loop (blocks until window is closed)
    print("GUI Closed. Exiting application.")

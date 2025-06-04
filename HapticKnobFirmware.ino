// ==========================================================================
// Smart Knob Firmware - Configurable Haptic Feedback
// Author: Ali Umut Ozcan - Elif Ecem Yilmaz
// Date: 05.06.2025
//
// Provides dynamic haptic feedback (detents, bounds) for a BLDC motor
// controlled via SimpleFOC, with settings adjustable via serial commands.
// ==========================================================================

#include <SimpleFOC.h>
#include <math.h>

// --- Hardware Configuration ---
MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);
BLDCMotor motor = BLDCMotor(7); // IMPORTANT: Set actual pole pairs for your motor!
BLDCDriver3PWM driver = BLDCDriver3PWM(9, 5, 6, 8); // Arduino Mega pins for PWM A,B,C & Enable

Commander command = Commander(Serial); // For serial command parsing

// --- Knob Behavior Configuration ---
struct KnobSettings {
  char name[32];
  bool is_bounded;
  float min_angle_rad;
  float max_angle_rad;
  int num_detents;          // 0 for smooth. If bounded, detents are within the bounds.
  float detent_strength_P;  // P-gain for angle controller (stiffness).
  int steps_per_revolution; // Output steps. If bounded, it's steps over the bounded_span.
};

const int MAX_PRESET_MODES = 6;
KnobSettings preset_modes[MAX_PRESET_MODES];
int current_preset_mode_idx = 0;
KnobSettings current_knob_settings; // Active configuration

// --- Operational State ---
float target_angle_motor = 0.0f;   // Target angle for FOC
long current_step_output = 0;      // Current output step value
long last_reported_step = -99999L; // Previous step, to send updates only on change
float motor_shaft_offset = 0.0f;   // Zero-point offset for unbounded modes

// --- Function Prototypes for Serial Commands & Helpers ---
void doSwitchPresetMode(char* cmd);
void doCalibrate(char* cmd);
void doSetNumDetents(char* cmd);
void doSetDetentStrength(char* cmd);
void doSetStepsPerRev(char* cmd);
void doSetBounded(char* cmd);
void doSetMinAngle(char* cmd);
void doSetMaxAngle(char* cmd);
void doReportSettings(char* cmd);
void applyCurrentSettingsToMotor();

// ==========================================================================
// SETUP: Initializes hardware, motor, FOC, and defines preset modes.
// ==========================================================================
void setup() {
  Serial.begin(115200);
  SimpleFOCDebug::enable(&Serial); // Verbose FOC debugging (optional)
  Serial.println(F("\nSmart Knob Initializing..."));

  // Define Preset Modes
  strcpy(preset_modes[0].name, "Unbounded Smooth");
  preset_modes[0].is_bounded = false; preset_modes[0].num_detents = 0;
  preset_modes[0].detent_strength_P = 10.0f; preset_modes[0].steps_per_revolution = 360;

  strcpy(preset_modes[1].name, "Unbounded 12 Detents");
  preset_modes[1].is_bounded = false; preset_modes[1].num_detents = 12;
  preset_modes[1].detent_strength_P = 25.0f; preset_modes[1].steps_per_revolution = 12;

  strcpy(preset_modes[2].name, "Bounded 0-180deg, 8 Det");
  preset_modes[2].is_bounded = true; preset_modes[2].min_angle_rad = 0.0f; preset_modes[2].max_angle_rad = _PI;
  preset_modes[2].num_detents = 8; preset_modes[2].detent_strength_P = 15.0f; preset_modes[2].steps_per_revolution = 8;

  strcpy(preset_modes[3].name, "Volume Knob (0-100)");
  preset_modes[3].is_bounded = true; preset_modes[3].min_angle_rad = 0.0f; preset_modes[3].max_angle_rad = _2PI;
  preset_modes[3].num_detents = 100; preset_modes[3].detent_strength_P = 25.0f; preset_modes[3].steps_per_revolution = 100;

  strcpy(preset_modes[4].name, "Fine Adjust Unbounded");
  preset_modes[4].is_bounded = false; preset_modes[4].num_detents = 72;
  preset_modes[4].detent_strength_P = 30.0f; preset_modes[4].steps_per_revolution = 72;

  strcpy(preset_modes[5].name, "Switch (4-pos)"); // 4 positions over 90 deg
  preset_modes[5].is_bounded = true; preset_modes[5].min_angle_rad = 0.0f; preset_modes[5].max_angle_rad = 1.57f; // ~PI/2
  preset_modes[5].num_detents = 3; // 3 detents create 4 snapping positions (0,1,2,3)
  preset_modes[5].detent_strength_P = 30.0f; preset_modes[5].steps_per_revolution = 3; // Output steps 0,1,2,3

  // Initialize Sensor
  Serial.println(F("Sensor Init..."));
  sensor.init();
  motor.linkSensor(&sensor);

  // Initialize Driver
  Serial.println(F("Driver Init..."));
  driver.voltage_power_supply = 12; // Set your actual power supply voltage
  driver.init();
  motor.linkDriver(&driver);

  // Motor & FOC Settings
  motor.foc_modulation = FOCModulationType::SpaceVectorPWM;
  motor.controller = MotionControlType::angle;

  motor.PID_velocity.P = 0.2f; motor.PID_velocity.I = 0; motor.PID_velocity.D = 0;
  motor.voltage_limit = 4;    // Max voltage for motor (adjust for torque/heat)
  motor.LPF_velocity.Tf = 0.05f;
  motor.velocity_limit = 30;  // Max speed for angle seeking (rad/s)

  // motor.useMonitoring(Serial); // Uncomment for detailed FOC telemetry

  Serial.println(F("Motor Init..."));
  motor.init();
  Serial.println(F("Motor Align & FOC Init (motor may move)..."));
  motor.initFOC(); // This aligns sensor and starts FOC - motor will move!
  Serial.println(F("Alignment Complete."));

  // Load default mode and register serial commands
  current_preset_mode_idx = 0; // Start with the first preset
  current_knob_settings = preset_modes[current_preset_mode_idx];
  applyCurrentSettingsToMotor();

  command.add('M', doSwitchPresetMode, "Switch Preset (M0-M5)");
  command.add('C', doCalibrate, "Calibrate Center (unbounded)");
  command.add('S', doReportSettings, "Show Settings");
  command.add('d', doSetNumDetents, "Num Detents (d12)");
  command.add('p', doSetDetentStrength, "Detent P-gain (p15.0)");
  command.add('r', doSetStepsPerRev, "Steps/Rev (r36)");
  command.add('b', doSetBounded, "Set Bounded (b0 or b1)");
  command.add('n', doSetMinAngle, "Min Angle [rad] (n0.0)");
  command.add('x', doSetMaxAngle, "Max Angle [rad] (x3.14)");

  Serial.println(F("\nSmart Knob Ready."));
  doReportSettings(nullptr); // Show initial config
  _delay(1000);
}

// ==========================================================================
// MAIN LOOP: Handles FOC, serial commands, haptics, and step output.
// ==========================================================================
void loop() {
  motor.loopFOC(); // Core FOC processing
  command.run();   // Check for incoming serial commands

  float raw_shaft_angle = motor.shaft_angle;
  float calculated_foc_target_angle = raw_shaft_angle; // Default: motor holds current position

  // Apply haptic logic (detents, bounds) based on current settings
  if (current_knob_settings.is_bounded) {
    // --- BOUNDED MODE ---
    float min_b = current_knob_settings.min_angle_rad;
    float max_b = current_knob_settings.max_angle_rad;
    float bounded_span = max_b - min_b;

    if (bounded_span <= 0.001f) { // Invalid span, just clamp
      calculated_foc_target_angle = constrain(raw_shaft_angle, min_b, max_b);
    } else {
      if (current_knob_settings.num_detents > 0) { // Bounded with detents
        float angle_relative_to_min = raw_shaft_angle - min_b;
        int num_eff_detents = (current_knob_settings.num_detents > 0) ? current_knob_settings.num_detents : 1;
        float detent_spacing = bounded_span / num_eff_detents;
        long detent_idx = round(angle_relative_to_min / detent_spacing);
        detent_idx = constrain(detent_idx, 0, num_eff_detents); // Snap points from index 0 to num_eff_detents
        calculated_foc_target_angle = min_b + (detent_idx * detent_spacing);
        calculated_foc_target_angle = constrain(calculated_foc_target_angle, min_b, max_b); // Final clamp
      } else { // Bounded, no detents
        calculated_foc_target_angle = constrain(raw_shaft_angle, min_b, max_b);
      }
    }
  } else {
    // --- UNBOUNDED MODE ---
    float angle_rel_offset = raw_shaft_angle - motor_shaft_offset; // Angle relative to calibrated zero
    if (current_knob_settings.num_detents > 0) { // Unbounded with detents
      int num_eff_detents = (current_knob_settings.num_detents > 0) ? current_knob_settings.num_detents : 1;
      float detent_spacing = _2PI / num_eff_detents; // Detents over full 360 deg
      float target_rel_offset = round(angle_rel_offset / detent_spacing) * detent_spacing;
      calculated_foc_target_angle = target_rel_offset + motor_shaft_offset;
    } else { // Unbounded, no detents
      calculated_foc_target_angle = raw_shaft_angle; // Hold current physical position
    }
  }

  // Set motor's angle controller P-gain and command the move
  motor.P_angle.P = current_knob_settings.detent_strength_P;
  motor.move(calculated_foc_target_angle);

  // --- Calculate Discrete Step Output for Serial ---
  long new_step_output = 0;
  if (current_knob_settings.steps_per_revolution > 0) {
    if (current_knob_settings.is_bounded) {
      float min_b = current_knob_settings.min_angle_rad;
      float max_b = current_knob_settings.max_angle_rad;
      float bounded_span = max_b - min_b;
      if (bounded_span > 0.001f) {
        float angle_in_span = constrain(raw_shaft_angle - min_b, 0.0f, bounded_span);
        float step_rad_in_bound = bounded_span / current_knob_settings.steps_per_revolution;
        new_step_output = round(angle_in_span / step_rad_in_bound);
        new_step_output = constrain(new_step_output, 0, current_knob_settings.steps_per_revolution);
      }
    } else { // Unbounded
      float angle_rel_offset_steps = raw_shaft_angle - motor_shaft_offset;
      float step_rad_unbounded = _2PI / current_knob_settings.steps_per_revolution;
      new_step_output = round(angle_rel_offset_steps / step_rad_unbounded);
    }
  }
  current_step_output = new_step_output;

  // Report step value only if it changed
  if (current_step_output != last_reported_step) {
    Serial.print("STEP:");
    Serial.println(current_step_output);
    last_reported_step = current_step_output;
  }
}

// ==========================================================================
// HELPER & CALLBACK FUNCTIONS
// ==========================================================================

void applyCurrentSettingsToMotor() {
  motor.P_angle.P = current_knob_settings.detent_strength_P;
  // Add other motor params here if they become part of KnobSettings
  last_reported_step = -99999L; // Force step report on next loop
}

void doSwitchPresetMode(char* cmd) {
  int idx = atoi(cmd);
  if (idx >= 0 && idx < MAX_PRESET_MODES) {
    current_preset_mode_idx = idx;
    current_knob_settings = preset_modes[idx];
    applyCurrentSettingsToMotor();
    Serial.print(F("Preset Mode -> ")); Serial.println(current_knob_settings.name);
    doReportSettings(nullptr);
  } else { Serial.println(F("Error: Invalid preset index.")); }
}

void doCalibrate(char* cmd) {
  if (!current_knob_settings.is_bounded) {
    motor_shaft_offset = motor.shaft_angle;
    current_step_output = 0; // Current pos is now step 0
    last_reported_step = -99999L;
    Serial.print(F("Unbounded calibrated. Offset: ")); Serial.println(motor_shaft_offset, 4);
  } else { Serial.println(F("Info: Calibrate ('C') for unbounded modes.")); }
}

void doReportSettings(char* cmd) {
  Serial.println(F("--- Current Settings ---"));
  Serial.print(F("Name: ")); Serial.println(current_knob_settings.name);
  Serial.print(F("Bounded: ")); Serial.println(current_knob_settings.is_bounded ? "YES" : "NO");
  if (current_knob_settings.is_bounded) {
    Serial.print(F("Min Angle: ")); Serial.print(current_knob_settings.min_angle_rad, 4); Serial.println(F(" rad"));
    Serial.print(F("Max Angle: ")); Serial.print(current_knob_settings.max_angle_rad, 4); Serial.println(F(" rad"));
  }
  Serial.print(F("Num Detents: ")); Serial.println(current_knob_settings.num_detents);
  Serial.print(F("Detent Strength P: ")); Serial.println(current_knob_settings.detent_strength_P, 2);
  Serial.print(F("Steps/Revolution: ")); Serial.println(current_knob_settings.steps_per_revolution);
  Serial.println(F("------------------------"));
}

// Callbacks for individual parameter settings
void doSetNumDetents(char* cmd) {
  int val = atoi(cmd);
  if (val >= 0) {
    current_knob_settings.num_detents = val; strcpy(current_knob_settings.name, "Custom");
    applyCurrentSettingsToMotor(); Serial.print(F("Detents set: ")); Serial.println(val);
    doReportSettings(nullptr);
  } else { Serial.println(F("Error: Detents must be >= 0.")); }
}

void doSetDetentStrength(char* cmd) {
  float val = atof(cmd);
  if (val >= 0) {
    current_knob_settings.detent_strength_P = val; strcpy(current_knob_settings.name, "Custom");
    applyCurrentSettingsToMotor(); Serial.print(F("Detent P set: ")); Serial.println(val, 2);
    doReportSettings(nullptr);
  } else { Serial.println(F("Error: Strength P must be >= 0.")); }
}

void doSetStepsPerRev(char* cmd) {
  int val = atoi(cmd);
  if (val >= 0) { // Allow 0 steps, though output will be 0
    current_knob_settings.steps_per_revolution = val; strcpy(current_knob_settings.name, "Custom");
    applyCurrentSettingsToMotor(); Serial.print(F("Steps/Rev set: ")); Serial.println(val);
    doReportSettings(nullptr);
  } else { Serial.println(F("Error: Steps/Rev must be >= 0.")); }
}

void doSetBounded(char* cmd) {
  int val = atoi(cmd);
  if (val == 0 || val == 1) {
    current_knob_settings.is_bounded = (val == 1); strcpy(current_knob_settings.name, "Custom");
    applyCurrentSettingsToMotor(); Serial.print(F("Bounded set: ")); Serial.println(current_knob_settings.is_bounded ? "YES" : "NO");
    if(!current_knob_settings.is_bounded) Serial.println(F("Info: Now unbounded. Use 'C' to calibrate."));
    doReportSettings(nullptr);
  } else { Serial.println(F("Error: Bounded value must be 0 or 1.")); }
}

void doSetMinAngle(char* cmd) {
  float val = atof(cmd);
  current_knob_settings.min_angle_rad = val; strcpy(current_knob_settings.name, "Custom");
  applyCurrentSettingsToMotor(); Serial.print(F("Min Angle set: ")); Serial.println(val, 4);
  doReportSettings(nullptr);
}

void doSetMaxAngle(char* cmd) {
  float val = atof(cmd);
  current_knob_settings.max_angle_rad = val; strcpy(current_knob_settings.name, "Custom");
  applyCurrentSettingsToMotor(); Serial.print(F("Max Angle set: ")); Serial.println(val, 4);
  doReportSettings(nullptr);
}

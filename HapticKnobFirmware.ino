#include <SimpleFOC.h>
#include <math.h> // For fmod, round, constrain

// Sensor and Motor Configuration
MagneticSensorI2C sensor = MagneticSensorI2C(AS5600_I2C);
BLDCMotor motor = BLDCMotor(7); // 7 pole pairs
BLDCDriver3PWM driver = BLDCDriver3PWM(9, 5, 6, 8); // Mega: PWM A, B, C, Enable

// Commander for serial commands
Commander command = Commander(Serial);

// --- Smart Knob Configuration Struct ---
struct KnobSettings {
  char name[32]; // Increased size for custom names
  bool is_bounded;
  float min_angle_rad;
  float max_angle_rad;
  int num_detents;
  float detent_strength_P;
  int steps_per_revolution;
  // velocity_limit
};

// --- Preset Modes ---
const int MAX_PRESET_MODES = 6;  // Increase if new modes defined
KnobSettings preset_modes[MAX_PRESET_MODES];
int current_preset_mode_idx = 0; // Index of the last loaded preset

// --- Active Configuration ---
KnobSettings current_knob_settings; // holds the settings being actively used and modified

// --- Operational Variables ---
float target_angle_motor = 0.0f;
long current_step_output = 0;
long last_reported_step = -99999;
float motor_shaft_offset = 0.0f;

// --- Commander Callback Function Prototypes ---
void doSwitchPresetMode(char* cmd);
void doCalibrate(char* cmd);
void doSetNumDetents(char* cmd);
void doSetDetentStrength(char* cmd);
void doSetStepsPerRev(char* cmd);
void doSetBounded(char* cmd); // 0 for false, 1 for true
void doSetMinAngle(char* cmd);
void doSetMaxAngle(char* cmd);
void doReportSettings(char* cmd); // Command to print current settings
void applyCurrentSettingsToMotor(); // Added prototype

void setup() {
  Serial.begin(115200);
  SimpleFOCDebug::enable(&Serial);

  // --- Define Preset Knob Modes ---
  strcpy(preset_modes[0].name, "Unbounded Smooth");
  preset_modes[0].is_bounded = false; 
  preset_modes[0].min_angle_rad = 0; 
  preset_modes[0].max_angle_rad = 0;
  preset_modes[0].num_detents = 0; 
  preset_modes[0].detent_strength_P = 10.0f; 
  preset_modes[0].steps_per_revolution = 360;

  strcpy(preset_modes[1].name, "Unbounded 12 Detents");
  preset_modes[1].is_bounded = false; 
  preset_modes[1].min_angle_rad = 0; 
  preset_modes[1].max_angle_rad = 0;
  preset_modes[1].num_detents = 12; 
  preset_modes[1].detent_strength_P = 25.0f;
  preset_modes[1].steps_per_revolution = 12;

  strcpy(preset_modes[2].name, "Bounded 0-180deg, 8 Det");
  preset_modes[2].is_bounded = true; 
  preset_modes[2].min_angle_rad = 0; 
  preset_modes[2].max_angle_rad = _PI;
  preset_modes[2].num_detents = 8; 
  preset_modes[2].detent_strength_P = 15.0f; 
  preset_modes[2].steps_per_revolution = 8;

  strcpy(preset_modes[3].name, "Volume Knob (0-100)");
  preset_modes[3].is_bounded = true; 
  preset_modes[3].min_angle_rad = 0; 
  preset_modes[3].max_angle_rad = _2PI; // Full turn for 100 steps
  preset_modes[3].num_detents = 100; 
  preset_modes[3].detent_strength_P = 25.0f; 
  preset_modes[3].steps_per_revolution = 100;

  strcpy(preset_modes[4].name, "Fine Adjust Unbounded");
  preset_modes[4].is_bounded = false; 
  preset_modes[4].min_angle_rad = 0; 
  preset_modes[4].max_angle_rad = 0;
  preset_modes[4].num_detents = 72; 
  preset_modes[4].detent_strength_P = 30.0f; 
  preset_modes[4].steps_per_revolution = 72;

  strcpy(preset_modes[5].name, "Switch");
  preset_modes[5].is_bounded = true; 
  preset_modes[5].min_angle_rad = 0; 
  preset_modes[5].max_angle_rad = 1.57;
  preset_modes[5].num_detents = 1; 
  preset_modes[5].detent_strength_P = 30.0f; 
  preset_modes[5].steps_per_revolution = 1;

  // Sensor initialization
  sensor.init();
  motor.linkSensor(&sensor);

  // Driver configuration
  driver.voltage_power_supply = 12;
  driver.init();
  motor.linkDriver(&driver);

  // FOC modulation
  motor.foc_modulation = FOCModulationType::SpaceVectorPWM;
  motor.controller = MotionControlType::angle;

  // Common motor parameters
  motor.PID_velocity.P = 0.2f;
  motor.PID_velocity.I = 0;
  motor.PID_velocity.D = 0;
  motor.voltage_limit = 4; //  4 for cooler operation
  motor.LPF_velocity.Tf = 0.05f;
  motor.velocity_limit = 30; // rad/s

  // motor.useMonitoring(Serial); // Comment out for less spam

  motor.init();
  Serial.println(F("Aligning motor and sensor..."));
  motor.initFOC();
  Serial.println(F("Motor alignment complete."));

  // Load initial mode settings (Preset Mode 0) into current_knob_settings
  current_preset_mode_idx = 0;
  current_knob_settings = preset_modes[current_preset_mode_idx];
  applyCurrentSettingsToMotor(); // Apply these settings to the motor

  // Add Commander commands
  command.add('M', doSwitchPresetMode, "switch preset (0-N)"); // M for Mode (preset)
  command.add('C', doCalibrate, "calibrate center (unbnd)");
  command.add('S', doReportSettings, "show current settings"); // S for Show

  // Commands for individual parameter adjustments
  command.add('d', doSetNumDetents, "num detents (int)");        // 'd' for detents
  command.add('p', doSetDetentStrength, "detent P (float)");    // 'p' for P-gain
  command.add('r', doSetStepsPerRev, "steps/rev (int)");        // 'r' for resolution/steps
  command.add('b', doSetBounded, "is bounded (0/1)");         // 'b' for bounded
  command.add('n', doSetMinAngle, "min angle rad (float)");    // 'n' for miN
  command.add('x', doSetMaxAngle, "max angle rad (float)");    // 'x' for maX

  Serial.println(F("Smart Knob Ready. Send '?' for help."));
  doReportSettings(nullptr); // Show initial settings
  _delay(1000);
}

// Replace your existing void loop() with this entire function
void loop() {
  motor.loopFOC(); // Run the FOC algorithm
  command.run();   // Process serial commands

  // Get the raw shaft angle from the sensor
  float raw_shaft_angle = motor.shaft_angle;

  // This will be the target angle we command the motor to
  float calculated_foc_target_angle = raw_shaft_angle; // Default to hold current

  // --- Apply Knob Logic based on current_knob_settings ---
  if (current_knob_settings.is_bounded) {
    // --- BOUNDED MODE LOGIC ---
    float min_b = current_knob_settings.min_angle_rad;
    float max_b = current_knob_settings.max_angle_rad;
    float bounded_span = max_b - min_b;

    // Ensure bounded_span is valid, otherwise, it might behave like free rotation within clamp
    if (bounded_span <= 0.001f) { // Use a small epsilon for float comparison
      // Invalid or zero span: just clamp to min_b (or max_b if raw is beyond)
      calculated_foc_target_angle = constrain(raw_shaft_angle, min_b, max_b);
    } else {
      // Valid bounded span
      if (current_knob_settings.num_detents > 0) {
        // BOUNDED MODE WITH DETENTS
        // Detents are now *within* the bounded_span.

        // 1. Current angle relative to the start of the bound
        float angle_relative_to_min = raw_shaft_angle - min_b;

        // 2. Calculate detent spacing *within the bounded_span*
        //    Ensure num_detents is at least 1 to avoid division by zero if misconfigured
        int num_effective_detents = (current_knob_settings.num_detents > 0) ? current_knob_settings.num_detents : 1;
        float detent_spacing_in_bound = bounded_span / num_effective_detents;

        // 3. Find the nearest detent index (0 to num_detents)
        long detent_index = round(angle_relative_to_min / detent_spacing_in_bound);

        // 4. Constrain detent_index to be from 0 to num_detents
        //    (If num_detents is 4, indices are 0,1,2,3,4 allowing snap to both ends)
        detent_index = constrain(detent_index, 0, num_effective_detents);

        // 5. Calculate the target angle for this detent *relative to min_b*
        float target_relative_to_min = detent_index * detent_spacing_in_bound;

        // 6. Convert back to absolute angle for motor.move()
        calculated_foc_target_angle = min_b + target_relative_to_min;

        // 7. Final precise clamp to ensure it's within the hard bounds
        calculated_foc_target_angle = constrain(calculated_foc_target_angle, min_b, max_b);

      } else {
        // BOUNDED MODE, NO DETENTS
        // Motor resists moving beyond bounds, holds current position within bounds.
        calculated_foc_target_angle = constrain(raw_shaft_angle, min_b, max_b);
      }
    }
  } else {
    // --- UNBOUNDED MODE LOGIC ---
    // Use the angle relative to the calibrated offset for detent calculations
    float angle_relative_to_offset = raw_shaft_angle - motor_shaft_offset;

    if (current_knob_settings.num_detents > 0) {
      // UNBOUNDED MODE WITH DETENTS
      // Detents are spread over a full _2PI rotation
      int num_effective_detents = (current_knob_settings.num_detents > 0) ? current_knob_settings.num_detents : 1;
      float detent_spacing_unbounded = _2PI / num_effective_detents;

      // Calculate target relative to the offset zero
      float target_relative_to_offset = round(angle_relative_to_offset / detent_spacing_unbounded) * detent_spacing_unbounded;
      
      // Convert back to absolute angle for motor.move()
      calculated_foc_target_angle = target_relative_to_offset + motor_shaft_offset;
    } else {
      // UNBOUNDED MODE, NO DETENTS
      // Motor holds its current physical position.
      calculated_foc_target_angle = raw_shaft_angle;
    }
  }

  // Apply the appropriate P-gain for the current mode/situation
  // (This could be more complex if you had separate P-gains for end-stops vs detents vs free)
  motor.P_angle.P = current_knob_settings.detent_strength_P;

  // Command the motor to the calculated target angle
  motor.move(calculated_foc_target_angle);


  // --- Calculate Step Output for Serial Reporting ---
  long new_step_output = 0; // Initialize

  if (current_knob_settings.steps_per_revolution > 0) {
    if (current_knob_settings.is_bounded) {
      // BOUNDED MODE STEP CALCULATION
      float min_b = current_knob_settings.min_angle_rad;
      float max_b = current_knob_settings.max_angle_rad;
      float bounded_span = max_b - min_b;

      if (bounded_span > 0.001f) {
        // Angle relative to min_b, clamped within the 0 to bounded_span range
        float angle_in_span_for_steps = constrain(raw_shaft_angle - min_b, 0.0f, bounded_span);

        // steps_per_revolution for bounded mode means total steps *within the bounded_span*
        float step_angle_rad_in_bound = bounded_span / current_knob_settings.steps_per_revolution;
        
        new_step_output = round(angle_in_span_for_steps / step_angle_rad_in_bound);
        
        // Constrain step output to be 0 to steps_per_revolution
        // (or steps_per_revolution - 1, depending on how you count. round() might yield N steps for N divs)
        new_step_output = constrain(new_step_output, 0, current_knob_settings.steps_per_revolution);
      } else {
        new_step_output = 0; // Or 0 if bounds are invalid
      }
    } else {
      // UNBOUNDED MODE STEP CALCULATION
      // Angle relative to the calibrated offset
      float angle_relative_to_offset_for_steps = raw_shaft_angle - motor_shaft_offset;

      // For unbounded, steps_per_revolution means steps over a full _2PI rotation
      float step_angle_rad_unbounded = _2PI / current_knob_settings.steps_per_revolution;
      new_step_output = round(angle_relative_to_offset_for_steps / step_angle_rad_unbounded);
    }
  } else { // steps_per_revolution is 0 or invalid
    new_step_output = 0;
  }

  // Update global current_step_output
  current_step_output = new_step_output;

  // Send Step Output via Serial (only if it has changed)
  if (current_step_output != last_reported_step) {
    Serial.print("STEP:");
    Serial.println(current_step_output);
    last_reported_step = current_step_output;
  }
} // End of void loop()

// --- Helper to apply current_knob_settings to the motor ---
void applyCurrentSettingsToMotor() {
  motor.P_angle.P = current_knob_settings.detent_strength_P;
  // Add any other motor parameters here that might be part of KnobSettings
  // e.g., motor.velocity_limit = current_knob_settings.some_velocity_limit;

  // When settings change, especially bounds or detents, it's good to reset
  // the step reporting to ensure the visualization updates correctly.
  last_reported_step = -99999; // Force a new report
}

// --- Commander Callback Implementations ---
void doSwitchPresetMode(char* cmd) {
  int new_mode_idx = atoi(cmd);
  if (new_mode_idx >= 0 && new_mode_idx < MAX_PRESET_MODES) {
    current_preset_mode_idx = new_mode_idx;
    current_knob_settings = preset_modes[current_preset_mode_idx]; // Load preset
    // strcpy(current_knob_settings.name, preset_modes[current_preset_mode_idx].name); // Name is part of struct, direct copy is fine
    applyCurrentSettingsToMotor();
    Serial.print(F("Switched to Preset Mode ")); Serial.print(current_preset_mode_idx);
    Serial.print(F(": ")); Serial.println(current_knob_settings.name);
    doReportSettings(nullptr); // Show the new settings
  } else {
    Serial.println(F("Invalid preset mode number."));
  }
}

void doCalibrate(char* cmd) {
  if (!current_knob_settings.is_bounded) {
    motor_shaft_offset = motor.shaft_angle;
    current_step_output = 0; // Assume current position is 0 after calibration
    last_reported_step = -99999; // Force report of new 0 step
    Serial.print(F("Unbounded mode calibrated. Current position is step 0. Offset: "));
    Serial.println(motor_shaft_offset);
  } else {
    Serial.println(F("Calibration ('C') is for unbounded modes."));
  }
}

void doReportSettings(char* cmd) {
  Serial.println(F("--- Current Knob Settings ---"));
  Serial.print(F("Name: ")); Serial.println(current_knob_settings.name);
  Serial.print(F("Bounded: ")); Serial.println(current_knob_settings.is_bounded ? "YES" : "NO");
  if (current_knob_settings.is_bounded) {
    Serial.print(F("Min Angle (rad): ")); Serial.println(current_knob_settings.min_angle_rad);
    Serial.print(F("Max Angle (rad): ")); Serial.println(current_knob_settings.max_angle_rad);
  }
  Serial.print(F("Num Detents: ")); Serial.println(current_knob_settings.num_detents);
  Serial.print(F("Detent Strength (P): ")); Serial.println(current_knob_settings.detent_strength_P);
  Serial.print(F("Steps/Revolution: ")); Serial.println(current_knob_settings.steps_per_revolution);
  Serial.println(F("-----------------------------"));
}

// --- Individual Parameter Setting Callbacks ---
void doSetNumDetents(char* cmd) {
  int val = atoi(cmd);
  if (val >= 0) { // Allow 0 for no detents
    current_knob_settings.num_detents = val;
    strcpy(current_knob_settings.name, "Custom"); // Mark as custom
    applyCurrentSettingsToMotor();
    Serial.print(F("Num Detents set to: ")); Serial.println(current_knob_settings.num_detents);
    doReportSettings(nullptr);
  } else {
    Serial.println(F("Invalid detent number."));
  }
}

void doSetDetentStrength(char* cmd) {
  float val = atof(cmd);
  if (val >= 0) {
    current_knob_settings.detent_strength_P = val;
    strcpy(current_knob_settings.name, "Custom");
    applyCurrentSettingsToMotor();
    Serial.print(F("Detent Strength (P) set to: ")); Serial.println(current_knob_settings.detent_strength_P);
    doReportSettings(nullptr);
  } else {
    Serial.println(F("Invalid strength value."));
  }
}

void doSetStepsPerRev(char* cmd) {
  int val = atoi(cmd);
  if (val > 0) { // Steps per rev should be positive
    current_knob_settings.steps_per_revolution = val;
    strcpy(current_knob_settings.name, "Custom");
    applyCurrentSettingsToMotor();
    Serial.print(F("Steps/Revolution set to: ")); Serial.println(current_knob_settings.steps_per_revolution);
    doReportSettings(nullptr);
  } else {
    Serial.println(F("Invalid steps/rev value (must be > 0)."));
  }
}

void doSetBounded(char* cmd) {
  int val = atoi(cmd);
  if (val == 0 || val == 1) {
    current_knob_settings.is_bounded = (val == 1);
    strcpy(current_knob_settings.name, "Custom");
    applyCurrentSettingsToMotor();
    Serial.print(F("Bounded set to: ")); Serial.println(current_knob_settings.is_bounded ? "YES" : "NO");
    if (!current_knob_settings.is_bounded) {
      Serial.println(F("Switched to unbounded. Consider 'C' to calibrate zero."));
    }
    doReportSettings(nullptr);
  } else {
    Serial.println(F("Invalid bounded value (0 or 1)."));
  }
}

void doSetMinAngle(char* cmd) {
  float val = atof(cmd);
  // Consider adding validation: val < current_knob_settings.max_angle_rad
  current_knob_settings.min_angle_rad = val;
  strcpy(current_knob_settings.name, "Custom");
  applyCurrentSettingsToMotor();
  Serial.print(F("Min Angle (rad) set to: ")); Serial.println(current_knob_settings.min_angle_rad);
  doReportSettings(nullptr);
}

void doSetMaxAngle(char* cmd) {
  float val = atof(cmd);
  // Consider adding validation: val > current_knob_settings.min_angle_rad
  current_knob_settings.max_angle_rad = val;
  strcpy(current_knob_settings.name, "Custom");
  applyCurrentSettingsToMotor();
  Serial.print(F("Max Angle (rad) set to: ")); Serial.println(current_knob_settings.max_angle_rad);
  doReportSettings(nullptr);
}

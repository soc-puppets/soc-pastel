// SPDX-License-Identifier: MIT
// Internal interface between PASTEL simulation C++ components.
#ifndef PASTEL_SIM_H
#define PASTEL_SIM_H

// Add an entry to the HELLO "info" object. json_value must be valid JSON.
// Only effective before the host connects.
void pastel_sim_info(const char* key, const char* json_value);

// Waveform dumping. The simulator main calls pastel_sim_waves_init() once if
// it can dump waves, giving the initial state; the host then turns dumping on
// and off through the pastel.platform endpoint, and the main dumps a timestep
// only while pastel_sim_waves_on() is true.
void pastel_sim_waves_init(bool on);
bool pastel_sim_waves_on();

#endif

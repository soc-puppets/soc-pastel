// SPDX-License-Identifier: MIT
// Internal interface between PASTEL simulation C++ components.
#ifndef PASTEL_SIM_H
#define PASTEL_SIM_H

// Add an entry to the HELLO "info" object. json_value must be valid JSON.
// Only effective before the host connects.
void pastel_sim_info(const char* key, const char* json_value);

#endif

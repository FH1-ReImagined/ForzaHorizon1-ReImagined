#pragma once
#include "data_out_packet.h"
namespace forza::data_out {
bool Enabled();
// Replaces one bounded latest sample; never waits for network I/O.
void Publish(const Sample& sample);
void Stop();
}

#pragma once

namespace glue2gpu
{
// Logs every other thread's host stack (Host::logThreadStacks), and appends it to
// regpu_stacks.txt in ReGPU's user folder (Config::userDir).
void LogThreadStacks(const char* why);
} // namespace glue2gpu

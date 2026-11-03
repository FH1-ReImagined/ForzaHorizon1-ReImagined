#pragma once

namespace glue2gpu
{
// Logs every other thread's host stack (Host::logThreadStacks), and appends it to
// regpu_stacks.txt in the working directory.
void LogThreadStacks(const char* why);
} // namespace glue2gpu

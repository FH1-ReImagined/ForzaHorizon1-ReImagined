#pragma once
// The physical CPU cores this process may run on (its affinity; the SMT siblings of a core count
// once), for defaults that only pay with spare cores (src/main.cpp: gpu_split_command_processor).
// 0 when unknown.
#include <cstdint>
#include <functional>
#include <set>
#include <string>
#include <vector>
#if defined(_WIN32)
#include <windows.h>
#else
#include <fstream>
#include <sched.h>
#endif

namespace forza::cpu_cores {

// The number of distinct cores among CPUS, CORE_OF naming the core of a CPU (its SMT siblings
// give the same name; empty when unknown, then the CPU counts as a core of its own).
inline unsigned CountCores(const std::vector<unsigned>& cpus,
                           const std::function<std::string(unsigned)>& core_of) {
  std::set<std::string> cores;
  for (unsigned cpu : cpus) {
    std::string core = core_of(cpu);
    cores.insert(core.empty() ? "cpu" + std::to_string(cpu) : core);
  }
  return unsigned(cores.size());
}

inline unsigned AvailablePhysicalCores() {
#if defined(_WIN32)
  DWORD_PTR process_mask = 0, system_mask = 0;
  if (!GetProcessAffinityMask(GetCurrentProcess(), &process_mask, &system_mask)) return 0;
  USHORT groups[4] = {};
  USHORT group_count = 4;
  if (!GetProcessGroupAffinity(GetCurrentProcess(), &group_count, groups) || group_count != 1)
    return 0;
  DWORD size = 0;
  GetLogicalProcessorInformationEx(RelationProcessorCore, nullptr, &size);
  if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || !size) return 0;
  std::vector<char> buffer(size);
  if (!GetLogicalProcessorInformationEx(
          RelationProcessorCore,
          reinterpret_cast<SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX*>(buffer.data()), &size))
    return 0;
  unsigned cores = 0;
  for (DWORD offset = 0; offset < size;) {
    const auto* entry =
        reinterpret_cast<const SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX*>(buffer.data() + offset);
    if (!entry->Size) break;
    for (WORD g = 0; g < entry->Processor.GroupCount; ++g) {
      const GROUP_AFFINITY& affinity = entry->Processor.GroupMask[g];
      if (affinity.Group == groups[0] && (affinity.Mask & process_mask)) {
        ++cores;
        break;
      }
    }
    offset += entry->Size;
  }
  return cores;
#else
  cpu_set_t set;
  CPU_ZERO(&set);
  if (sched_getaffinity(0, sizeof(set), &set) != 0) return 0;
  std::vector<unsigned> cpus;
  for (unsigned cpu = 0; cpu < CPU_SETSIZE; ++cpu)
    if (CPU_ISSET(cpu, &set)) cpus.push_back(cpu);
  return CountCores(cpus, [](unsigned cpu) {
    const std::string topology = "/sys/devices/system/cpu/cpu" + std::to_string(cpu) + "/topology/";
    std::string siblings;
    for (const char* name : {"core_cpus_list", "thread_siblings_list"}) {
      std::ifstream file(topology + name);
      if (file >> siblings) return siblings;
    }
    return std::string();
  });
#endif
}

}  // namespace forza::cpu_cores

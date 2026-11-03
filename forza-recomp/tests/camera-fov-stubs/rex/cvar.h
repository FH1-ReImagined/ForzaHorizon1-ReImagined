#pragma once
#include <cstdint>
#define REXCVAR_DEFINE_INT32(name, value, category, description) \
  int32_t& FLAGS_##name##_storage_() { static int32_t flag = value; return flag; }
#define REXCVAR_DECLARE(type, name) type& FLAGS_##name##_storage_()
#define REXCVAR_GET(name) FLAGS_##name##_storage_()

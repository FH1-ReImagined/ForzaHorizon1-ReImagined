cmake_minimum_required(VERSION 3.20)
project(thumbnail_sync_contracts LANGUAGES CXX)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
find_package(Threads REQUIRED)
set(source "${CMAKE_CURRENT_SOURCE_DIR}/../../src/hooks/thumbnail_sync.cpp")
set(helper "${CMAKE_CURRENT_BINARY_DIR}/actual_thumbnail_sync.inc")
add_custom_command(OUTPUT "${helper}"
  COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/extract_helper.py"
          "${source}" "${helper}"
  DEPENDS "${source}" "${CMAKE_CURRENT_SOURCE_DIR}/extract_helper.py"
  VERBATIM)
add_executable(thumbnail_sync_test thumbnail_sync_test.cpp "${helper}")
target_include_directories(thumbnail_sync_test PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
target_compile_features(thumbnail_sync_test PRIVATE cxx_std_20)
target_link_libraries(thumbnail_sync_test PRIVATE Threads::Threads)
enable_testing()
foreach(mode race expiry photo disabled native)
  add_test(NAME thumbnail_sync_${mode} COMMAND thumbnail_sync_test "${mode}")
  set_tests_properties(thumbnail_sync_${mode} PROPERTIES TIMEOUT 5)
endforeach()

# The streamed texture arena's test (../fm4_stream_textures_test.cpp, #824) as a project of its
# own, so CI builds and runs it with each platform's compiler (MSVC on Windows).
cmake_minimum_required(VERSION 3.20)
project(fm4_stream_textures_tests LANGUAGES CXX)
enable_testing()
add_executable(fm4_stream_textures_test ../fm4_stream_textures_test.cpp)
target_include_directories(fm4_stream_textures_test PRIVATE ../../src)
target_compile_features(fm4_stream_textures_test PRIVATE cxx_std_20)
if(NOT MSVC)
  target_compile_options(fm4_stream_textures_test PRIVATE -Wall -Wextra -Werror)
endif()
add_test(NAME fm4_stream_textures COMMAND fm4_stream_textures_test)

# forza - ReXGlue Recompiled Project (Forza Horizon, 4D5309C9)
#
# SDK boilerplate lives in generated/rexglue.cmake. The codegen-on-first-configure,
# cross-build and patched-GPU-plugin blocks are adapted from RecompiledHaloReach.

cmake_minimum_required(VERSION 3.25)
project(forza LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Which executable: the disc's (0) or Title Update 4 (4): its own manifest, hints
# (hints_tu4/) and codegen output (generated_tu4/), docs/dlc.md.
set(FORZA_TITLE_UPDATE 0 CACHE STRING "Game version to build: 0 (base) or 4 (Title Update 4)")
set_property(CACHE FORZA_TITLE_UPDATE PROPERTY STRINGS 0 4)
if(FORZA_TITLE_UPDATE STREQUAL "4")
    set(FORZA_MANIFEST forza_manifest_tu4.toml)
    set(FORZA_GENERATED generated_tu4)
elseif(FORZA_TITLE_UPDATE STREQUAL "0")
    set(FORZA_MANIFEST forza_manifest.toml)
    set(FORZA_GENERATED generated)
else()
    message(FATAL_ERROR "FORZA_TITLE_UPDATE must be 0 or 4")
endif()

include(cmake/NativePause.cmake)
forza_validate_native_pause()

# Builds handed a finished generated/ (CI building from the private copy of the codegen
# output) have no default.xex to run codegen on.
option(FORZA_SKIP_CODEGEN "Compile generated/ as it is, never run codegen" OFF)
if(FORZA_SKIP_CODEGEN AND NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${FORZA_GENERATED}/default/sources.cmake")
    message(FATAL_ERROR "FORZA_SKIP_CODEGEN is on but ${FORZA_GENERATED}/default has no codegen output")
endif()

# Codegen runs the patched SDK overlay's rexglue when there is one: tools/build_rexglue_sdk.sh
# builds it from ReIGlue (the SDK fork), and some of its changes alter the generated code (0025:
# Xenon's dot products give QNaN on overflow, which the collision GJK depends on).
if(APPLE)
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "arm64|aarch64")
        set(_forza_sdk_platform mac-arm64)
    else()
        set(_forza_sdk_platform mac-amd64)
    endif()
else()
    set(_forza_sdk_platform linux-amd64)
endif()
set(FORZA_REXGLUE "$ENV{HOME}/rexglue-sdk-patched-forza/0.10.0.24/${_forza_sdk_platform}/bin/rexglue"
    CACHE FILEPATH "Patched rexglue to run codegen with instead of the SDK's")

# generated/rexglue.cmake lists the generated sources at configure time, so on a fresh
# checkout (no generated/default yet) codegen runs here once; otherwise the first build
# would run it but compile none of its output.
if(NOT CMAKE_CROSSCOMPILING AND NOT REXSDK_DIR AND
   NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${FORZA_GENERATED}/default/sources.cmake")
    find_package(rexglue 0.10.0 REQUIRED CONFIG)
    get_target_property(_rexglue rex::rexglue LOCATION)
    if(EXISTS "${FORZA_REXGLUE}")
        set(_rexglue "${FORZA_REXGLUE}")
    endif()
    message(STATUS "First configure: running codegen (${_rexglue}), this takes a while")
    execute_process(
        COMMAND "${_rexglue}" codegen "${CMAKE_CURRENT_SOURCE_DIR}/${FORZA_MANIFEST}"
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
        COMMAND_ERROR_IS_FATAL ANY)
endif()

# generated/rexglue.cmake is rewritten by every codegen run, with the paths of the manifest
# that ran (generated/ or generated_tu4/); this build includes a copy pointed at its own.
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/generated/rexglue.cmake" _rexglue_cmake)
string(REGEX REPLACE "generated(_tu4)?/default" "${FORZA_GENERATED}/default" _rexglue_cmake "${_rexglue_cmake}")
string(REGEX REPLACE "forza_manifest(_tu4)?\\.toml" "${FORZA_MANIFEST}" _rexglue_cmake "${_rexglue_cmake}")
file(CONFIGURE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/rexglue_project.cmake" CONTENT "${_rexglue_cmake}" @ONLY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/generated/rexglue.cmake")
include("${CMAKE_CURRENT_BINARY_DIR}/rexglue_project.cmake")

# A cross build compiles the generated code the native build made; it must not run
# codegen itself (the SDK's rexglue for the target cannot run here, and regenerating
# would rewrite generated/ under the native build). The build-time codegen step stays
# (rexglue.cmake orders the generated sources after it) but runs true instead.
if((CMAKE_CROSSCOMPILING OR FORZA_SKIP_CODEGEN) AND TARGET rex::rexglue)
    # A no-op that takes any arguments; Windows has no /usr/bin/true.
    if(CMAKE_HOST_WIN32)
        set(_codegen_noop "${CMAKE_CURRENT_BINARY_DIR}/codegen_skipped.cmd")
        # Written only when it differs: a rewrite is a new file time, and the custom commands
        # that run the tool depend on it.
        set(_codegen_noop_text "@exit /b 0\r\n")
        set(_codegen_noop_old "")
        if(EXISTS "${_codegen_noop}")
            file(READ "${_codegen_noop}" _codegen_noop_old)
        endif()
        if(NOT _codegen_noop_old STREQUAL _codegen_noop_text)
            file(WRITE "${_codegen_noop}" "${_codegen_noop_text}")
        endif()
    else()
        set(_codegen_noop /usr/bin/true)
    endif()
    set_target_properties(rex::rexglue PROPERTIES
        IMPORTED_LOCATION "${_codegen_noop}" IMPORTED_LOCATION_RELEASE "${_codegen_noop}")
    message(STATUS "Codegen is skipped: compiling generated/ as it is")
elseif(TARGET rex::rexglue AND EXISTS "${FORZA_REXGLUE}")
    get_target_property(_configs rex::rexglue IMPORTED_CONFIGURATIONS)
    set_target_properties(rex::rexglue PROPERTIES IMPORTED_LOCATION "${FORZA_REXGLUE}")
    foreach(_config IN LISTS _configs)
        set_target_properties(rex::rexglue PROPERTIES
            IMPORTED_LOCATION_${_config} "${FORZA_REXGLUE}")
    endforeach()
    # A new tool reruns codegen (its depfile lists only the inputs).
    add_custom_command(OUTPUT "${CMAKE_CURRENT_SOURCE_DIR}/${FORZA_GENERATED}/default/codegen.build.stamp"
        APPEND DEPENDS "${FORZA_REXGLUE}")
    message(STATUS "Codegen tool: ${FORZA_REXGLUE}")
elseif(TARGET rex::rexglue)
    message(WARNING "Patched rexglue not found at ${FORZA_REXGLUE}; codegen uses the SDK's, "
                    "whose dot products differ from Xenon's. Run tools/build_rexglue_sdk.sh.")
endif()

# Sources
set(FORZA_SOURCES
    src/main.cpp
    src/debug/audio_level.cpp
    src/hooks/radio_station.cpp
    src/hooks/radio_events.cpp
    src/debug/cpu_profile.cpp
    src/debug/codriver_log.cpp
    src/debug/dlc_log.cpp
    src/debug/fault_guard.cpp
    src/debug/fiber_trace.cpp
    src/debug/lobby_log.cpp
    src/debug/stall_watch.cpp
    src/debug/frame_stats.cpp
    src/debug/thread_trace.cpp
    src/debug/frame_dump.cpp
    src/hooks/compressed_stream_stall.cpp
    src/hooks/ffcb_wake.cpp
    src/hooks/database_query.cpp
    src/hooks/crowd_anim.cpp
    src/hooks/movie_texture.cpp
    src/hooks/movie_memory.cpp
    src/hooks/map_alpha.cpp
    src/hooks/minimap_icons.cpp
    src/hooks/minimap_camera.cpp
    src/hooks/map_entity_time.cpp
    src/hooks/cockpit_gauges.cpp
    src/hooks/garage_camera.cpp
    src/hooks/garage_capacity.cpp
    src/hooks/ultrawide_projection.cpp
    src/hooks/vr_culling.cpp
    src/hooks/vr_head_pose.cpp
    src/hooks/camera_fov.cpp
    src/hooks/vr_cockpit_space.cpp
    src/hooks/frame_time.cpp
    src/hooks/graphics_menu.cpp
    src/hooks/pause_menu.cpp
    src/hooks/native_pause_scene.cpp
    src/hooks/native_pause_edges.cpp
    src/hooks/fh1_map_preview.cpp
    src/hooks/offline_cars.cpp
    src/hooks/festival_travel.cpp
    src/hooks/car_recovery.cpp
    src/hooks/native_car_tuning.cpp
    src/hooks/native_car_tuning_menu.cpp
    src/hooks/gpu_wait.cpp
    src/hooks/render_idle.cpp
    src/hooks/gamma_transition.cpp
    src/hooks/scene_resolution.cpp
    src/hooks/scene_tiling.cpp
    src/hooks/world_detail.cpp
    src/hooks/car_detail.cpp
    src/hooks/shadow_filter.cpp
    src/hooks/ai_catchup.cpp
    src/hooks/physics_loader_check.cpp
    src/hooks/resource_data_release.cpp
    src/hooks/resource_cache_eviction.cpp
    src/hooks/resource_stream_trace.cpp
    src/hooks/bigshot_readback.cpp
    src/hooks/online_photo.cpp
    src/hooks/thumbnail_sync.cpp
    src/hooks/world_challenge.cpp
    src/hooks/ambient_leaderboard.cpp
    src/hooks/teleport_velocity.cpp
    src/hooks/custom_race.cpp
    src/hooks/coop_map_event.cpp
    src/hooks/smashables_lookup.cpp
    src/hooks/challenge_hud.cpp
    src/hooks/car_placeholder.cpp
    src/hooks/lobby_buckets.cpp
    src/hooks/world_drop_in.cpp
    src/hooks/world_events.cpp
    src/hooks/telemetry.cpp
    src/hooks/discord_details.cpp
    src/kernel/content_manifest.cpp
    src/kernel/critical_section.cpp
    src/kernel/file_delete_on_close.cpp
    src/kernel/file_size_refresh.cpp
    src/kernel/input_autopress.cpp
    src/kernel/identity.cpp
    src/kernel/live_lsp.cpp
    src/kernel/live_tcp.cpp
    src/kernel/live_auth.cpp
    src/kernel/live_friends.cpp
    src/kernel/live_invites.cpp
    src/kernel/live_xmsg.cpp
    src/kernel/mod_overlay.cpp
    src/kernel/module_resident.cpp
    src/kernel/net.cpp
    src/kernel/proc_address_cache.cpp
    src/kernel/xam_enumerate.cpp
    src/kernel/xam_keyboard.cpp
    src/kernel/xam_loader.cpp
    src/kernel/xam_nui.cpp
    src/kernel/xam_signin.cpp
    src/kernel/xecrypt_rsa.cpp
    src/kernel/xekeys.cpp
    src/platform/achievement_toast.cpp
    src/platform/guest_cpu.cpp
    src/platform/discord_presence.cpp
    src/platform/cursor_idle.cpp
    src/platform/auto_pause.cpp
    src/platform/cpu_target.cpp
    src/platform/ultrawide.cpp
    src/platform/benchmark.cpp
    src/platform/benchmark_session.cpp
    src/platform/hud_overlay.cpp
    src/platform/motion_blur_fix.cpp
    src/platform/world_ao.cpp
    src/platform/native_quit.cpp
    src/platform/resume.cpp
    src/platform/sdk_import.cpp
    src/platform/socket.cpp
    src/platform/telemetry.cpp
    src/platform/vulkan_hooks.cpp
    src/platform/wheel_bridge.cpp
    src/platform/xr.cpp
)

# fiber_trace.cpp interposes the SDK's rexcrt_SwitchToFiber with dlsym(RTLD_NEXT): Linux only.
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    list(REMOVE_ITEM FORZA_SOURCES src/debug/fiber_trace.cpp)
endif()
if(WIN32)
    # The icon is optional: a toolchain without a resource compiler (no llvm-rc next to
    # clang-cl, no rc.exe on PATH) still builds the game, just without it.
    include(CheckLanguage)
    check_language(RC)
    set(FORZA_ICON_RC "")
    if(CMAKE_RC_COMPILER)
        enable_language(RC)
        set(FORZA_ICON_RC "${CMAKE_CURRENT_BINARY_DIR}/forza-icon.rc")
        configure_file(resources/app.rc.in "${FORZA_ICON_RC}" @ONLY)
        set_source_files_properties("${FORZA_ICON_RC}" PROPERTIES
            OBJECT_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/resources/forza.ico;${CMAKE_CURRENT_SOURCE_DIR}/resources/forza.manifest")
    else()
        message(STATUS "No resource compiler found: forza.exe is built without its icon")
    endif()
    add_executable(forza WIN32 ${FORZA_SOURCES} ${FORZA_ICON_RC})
    target_compile_definitions(forza PRIVATE _CRT_SECURE_NO_WARNINGS)
    target_link_libraries(forza PRIVATE ws2_32)
    # Per-monitor-v2 DPI awareness from the first instruction (#418), from resources/forza.manifest.
    # With a resource compiler it is the exe's RT_MANIFEST resource (app.rc.in) and the linker's own
    # default manifest is off (it would collide); without one the linker merges the file itself.
    if(FORZA_ICON_RC)
        target_link_options(forza PRIVATE "/MANIFEST:NO")
    else()
        target_link_options(forza PRIVATE "/MANIFEST:EMBED"
            "/MANIFESTINPUT:${CMAKE_CURRENT_SOURCE_DIR}/resources/forza.manifest")
    endif()
else()
    add_executable(forza ${FORZA_SOURCES})
endif()

rexglue_setup_target(forza GPU_PLUGINS xenos)

# ReGPU's Direct3D 12 renderer as a GPU plugin beside forza.exe (--gpu_plugin=regpu), Windows only.
# Off by default until it is built and tested in the release builds. Its shaders ship pre-built, so it needs
# no fxc or py (FORZA_REGPU_FSR1_FROM_SOURCE=ON compiles them again).
if(WIN32)
    option(FORZA_REGPU_PLUGIN "Build the ReGPU GPU plugin (rexgpu-regpu.dll)" OFF)
    if(FORZA_REGPU_PLUGIN)
        add_subdirectory(regpu-plugin)
    endif()
endif()
include(cmake/discord.cmake)

# forza-texcompress: texture packs to BC7/BC1 DDS files, run by the launcher's Make HD pack and
# Compress PNGs (#413, docs/texture_packs.md). Its own executable next to forza.
add_subdirectory(texcompress)

add_custom_command(TARGET forza POST_BUILD COMMAND ${CMAKE_COMMAND} -E copy_if_different
    "${CMAKE_CURRENT_SOURCE_DIR}/resources/forza-telemetry.json" "$<TARGET_FILE_DIR:forza>/forza-telemetry.json")
target_compile_definitions(forza PRIVATE FORZA_TITLE_UPDATE=${FORZA_TITLE_UPDATE})

# The build the game names to the Forza Live server on its title service calls (X-Forza-Build,
# src/kernel/live_auth.cpp RequestLines), which the server keeps with each leaderboard row (#517). The
# launcher passes its version; a source build takes the commit it was configured at.
set(FORZA_BUILD_ID "" CACHE STRING "Build name sent to the Forza Live server (empty: git-<commit>, or dev)")
set(_forza_build_id "${FORZA_BUILD_ID}")
if(_forza_build_id STREQUAL "")
    set(_forza_build_id dev)
    find_package(Git QUIET)
    if(GIT_FOUND)
        execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${CMAKE_CURRENT_SOURCE_DIR}" rev-parse --short=10 HEAD
                        OUTPUT_VARIABLE _forza_commit OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET
                        RESULT_VARIABLE _forza_git)
        if(_forza_git EQUAL 0 AND _forza_commit MATCHES "^[0-9a-f]+$")
            set(_forza_build_id "git-${_forza_commit}")
        endif()
    endif()
endif()
string(REGEX REPLACE "[^A-Za-z0-9._+-]" "_" _forza_build_id "${_forza_build_id}")
string(SUBSTRING "${_forza_build_id}" 0 40 _forza_build_id)
set_source_files_properties(src/kernel/live_auth.cpp PROPERTIES
    COMPILE_DEFINITIONS "FORZA_BUILD_ID=\"${_forza_build_id}\"")
forza_apply_native_pause(forza)

# Experimental CPU optimization, independent of the GPU/driver. Keep the host
# bootstrap at the SDK baseline so it can reject an incompatible CPU safely.
include(cmake/GuestFma.cmake)
forza_configure_guest_fma(forza)

# The CPU the generated code is compiled for (baseline or x86-64-v3, chosen by the launcher).
include(cmake/CpuTarget.cmake)
forza_configure_cpu_target(forza)

# src/platform/vulkan_hooks.cpp (and the app header) include the SDK's Vulkan headers,
# which include <renderdoc_app.h> from this interface target and the X11/Wayland client
# headers (stand-ins in src/platform/wsi_stubs, so no window-system packages are needed).
# From RecompiledHaloReach.
if(TARGET rex::renderdoc)
    target_link_libraries(forza PRIVATE rex::renderdoc)
endif()
# VR (src/platform/xr.cpp) also includes them, and the vendored OpenXR headers; the OpenXR
# loader is opened at run time only when vr_mode is on, so the game neither links it nor needs
# it to start.
set(_forza_xr_includes "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/openxr/include")
if(UNIX AND NOT APPLE)
    set_source_files_properties(src/platform/vulkan_hooks.cpp PROPERTIES
        INCLUDE_DIRECTORIES "${CMAKE_CURRENT_SOURCE_DIR}/src/platform/wsi_stubs")
    list(APPEND _forza_xr_includes "${CMAKE_CURRENT_SOURCE_DIR}/src/platform/wsi_stubs")
endif()
set_source_files_properties(src/platform/xr.cpp PROPERTIES INCLUDE_DIRECTORIES "${_forza_xr_includes}")
add_custom_command(TARGET forza POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:forza>/licenses"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/openxr/LICENSE-Apache-2.0.txt"
        "$<TARGET_FILE_DIR:forza>/licenses/openxr-sdk.txt"
    VERBATIM)

# The stock SDK Xenos plugin has rendering bugs (e.g. it reads the texture-fetch
# exponent bias from the wrong fetch-constant word, which darkens textures 256x).
# tools/build_rexglue_sdk.sh builds a patched plugin; this copies it over the SDK
# one that rexglue_setup_target() stages next to the binary.
# This project's overlay first; RecompiledHaloReach's (same patch set so far) otherwise.
set(_forza_plugin_name "${CMAKE_SHARED_LIBRARY_PREFIX}rexgpu-xenos${CMAKE_SHARED_LIBRARY_SUFFIX}")
set(_forza_plugin "$ENV{HOME}/rexglue-sdk-patched-forza/0.10.0.24/${_forza_sdk_platform}/lib/${_forza_plugin_name}")
if(NOT EXISTS "${_forza_plugin}")
    set(_forza_plugin "$ENV{HOME}/rexglue-sdk-patched/0.10.0.24/${_forza_sdk_platform}/lib/${_forza_plugin_name}")
endif()
set(FORZA_XENOS_PLUGIN "${_forza_plugin}"
    CACHE FILEPATH "Patched Xenos GPU plugin to ship instead of the SDK's")
if(UNIX)
    if(EXISTS "${FORZA_XENOS_PLUGIN}")
        add_custom_command(TARGET forza POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${FORZA_XENOS_PLUGIN}" "$<TARGET_FILE_DIR:forza>/${_forza_plugin_name}"
            VERBATIM)
    else()
        message(WARNING "Patched GPU plugin not found at ${FORZA_XENOS_PLUGIN}; "
                        "textures will render dark. Run tools/build_rexglue_sdk.sh.")
    endif()
endif()

# The SDK runtime dlopen()s guest-DLL modules by bare target name on POSIX
# (no "lib" prefix or platform suffix), so emit the libraries under that name.
if(UNIX)
    foreach(_module forza_XMediaFacade_default forza_SpeechFacade_default)
        if(TARGET ${_module})
            set_target_properties(${_module} PROPERTIES PREFIX "" SUFFIX "")
        endif()
    endforeach()
endif()

# Retain the notice for the adapted ultrawide presentation strategy with the binary.
add_custom_command(TARGET forza POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE-Pinyon-Shift"
        "$<TARGET_FILE_DIR:forza>/LICENSE-Pinyon-Shift"
    VERBATIM)

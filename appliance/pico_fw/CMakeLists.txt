cmake_minimum_required(VERSION 3.13)

set(CMAKE_C_STANDARD 17)

# RP2350 port (Pico 2 W) of PiKVM's Pico HID -- pico-sdk 2.2.0 bundles a current TinyUSB
# with RP2350 device support natively (src/rp2_common/tinyusb), so no separate .tinyusb
# checkout / PICO_TINYUSB_PATH is needed (that was only required on the pre-2.0 SDK the
# upstream Makefile pins, which predates RP2350 support entirely).
if(NOT DEFINED PICO_SDK_PATH)
    set(PICO_SDK_PATH ${CMAKE_CURRENT_LIST_DIR}/deps/pico-sdk)
endif()
if(NOT DEFINED PS2_PATH)
    set(PS2_PATH ${CMAKE_CURRENT_LIST_DIR}/deps/ps2x2pico/src)
endif()
set(PICO_BOARD pico2_w)

# For TinyUSB
set(FAMILY rp2350)

# Include pico_sdk_import.cmake from pico-sdk (instead of copying)
include(${PICO_SDK_PATH}/external/pico_sdk_import.cmake)

# Generic setup
set(PROJECT hid)
project(${PROJECT})

# Initialize Pico-SDK
pico_sdk_init()

# Set the path to the source code to build
set(SRC_TO_BUILD_PATH ${CMAKE_CURRENT_LIST_DIR}/src)
add_subdirectory(${SRC_TO_BUILD_PATH})

# Metadata
pico_set_program_name(hid "org.pikvm.hid.v1.${FAMILY}")
pico_set_program_version(hid "1")

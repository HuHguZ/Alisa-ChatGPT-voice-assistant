cmake_minimum_required(VERSION 3.21)
project(alisa_standalone LANGUAGES C CXX)
set(CMAKE_LINK_DEPENDS_USE_LINKER FALSE)
set(CMAKE_CXX_STANDARD 17)
set(DEPS "${CMAKE_SOURCE_DIR}/research/upstream")
set(ARM "${CMAKE_SOURCE_DIR}/build/arm")
set(PREFIX "${ARM}/prefix")
add_executable(alisa-standalone native/main.cpp)
option(ALISA_USE_NETEQ "Use the upstream WebRTC audio decoder/jitter buffer" ON)
if(ALISA_USE_NETEQ)
    add_subdirectory(cmake/neteq)
    target_compile_definitions(alisa-standalone PRIVATE ALISA_USE_NETEQ=1)
    target_link_libraries(alisa-standalone PRIVATE alisa-neteq)
endif()
option(ALISA_TRANSPORT_STUDY "Enable isolated RTCP/fixed-tempo diagnostic probes" OFF)
if(ALISA_TRANSPORT_STUDY)
    target_compile_definitions(alisa-standalone PRIVATE ALISA_TRANSPORT_STUDY=1)
endif()
if(NOT ALISA_USE_NETEQ)
    add_library(speech-tempo STATIC "${DEPS}/sonic/sonic.c")
    target_include_directories(speech-tempo PUBLIC "${DEPS}/sonic")
    target_compile_options(speech-tempo PRIVATE -O2 -ffunction-sections -fdata-sections)
    target_link_libraries(alisa-standalone PRIVATE speech-tempo)
endif()
target_include_directories(alisa-standalone PRIVATE "${PREFIX}/include" "${PREFIX}/include/opus"
    "${DEPS}/libdatachannel/include" "${DEPS}/libdatachannel/deps/json/single_include")
target_compile_definitions(alisa-standalone PRIVATE RTC_ENABLE_WEBSOCKET=0 RTC_ENABLE_MEDIA=1)
target_compile_options(alisa-standalone PRIVATE -Os -ffunction-sections -fdata-sections -Wall -Wextra)
target_link_options(alisa-standalone PRIVATE -Wl,--gc-sections -Wl,-s)
target_link_libraries(alisa-standalone PRIVATE -Wl,--start-group
    "${ARM}/libdatachannel/libdatachannel.a"
    "${ARM}/libdatachannel/deps/usrsctp/usrsctplib/libusrsctp.a"
    "${ARM}/libdatachannel/deps/libsrtp/libsrtp2.a"
    "${ARM}/libdatachannel/deps/libjuice/libjuice.a"
    "${PREFIX}/lib/libcurl.a" "${PREFIX}/lib/libssl.a" "${PREFIX}/lib/libcrypto.a"
    "${PREFIX}/lib/libopus.a" -Wl,--end-group pthread dl m)

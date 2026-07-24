RUNS_DIR ?= $(abspath ../../runs)
DEPS_DIR ?= $(abspath deps)
RUN_ID ?= pico_fw_build_$(shell date +%Y%m%d_%H%M%S)
RUN_DIR ?= $(RUNS_DIR)/$(RUN_ID)
BUILD_DIR ?= $(RUN_DIR)/build
PICO_SDK_PATH ?= $(DEPS_DIR)/pico-sdk
PS2X2PICO_PATH ?= $(DEPS_DIR)/ps2x2pico

all: deps
	mkdir -p $(RUN_DIR)
	cmake -S . -B $(BUILD_DIR) \
		-DPICO_SDK_PATH=$(PICO_SDK_PATH) \
		-DPS2_PATH=$(PS2X2PICO_PATH)/src
	cmake --build $(BUILD_DIR) --config Release -- -j
	cp $(BUILD_DIR)/src/hid.uf2 $(RUN_DIR)/hid.uf2
	@echo "firmware and build output: $(RUN_DIR)"

# pico-sdk 2.2.0+ bundles a current TinyUSB (src/rp2_common/tinyusb) with RP2350
# device support natively -- no separate TinyUSB checkout needed (that was only
# required against upstream's pre-2.0 SDK pin, which predates RP2350 support).
$(PICO_SDK_PATH):
	mkdir -p $(DEPS_DIR)
	git clone --branch 2.2.0 --depth 1 https://github.com/raspberrypi/pico-sdk.git $(PICO_SDK_PATH)
	cd $(PICO_SDK_PATH) && git submodule update --init --depth 1

$(PS2X2PICO_PATH):
	mkdir -p $(DEPS_DIR)
	git clone --depth 1 https://github.com/No0ne/ps2x2pico.git $(PS2X2PICO_PATH)

deps: $(PICO_SDK_PATH) $(PS2X2PICO_PATH)

.PHONY: deps all

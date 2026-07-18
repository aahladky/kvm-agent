all: deps
	rm -f hid.uf2
	cmake -B .build
	cmake --build .build --config Release -- -j
	ln -f .build/src/hid.uf2 .

clean:
	rm -rf .build hid.uf2
clean-all: clean
	rm -rf .pico-sdk .ps2x2pico

# pico-sdk 2.2.0+ bundles a current TinyUSB (src/rp2_common/tinyusb) with RP2350
# device support natively -- no separate TinyUSB checkout needed (that was only
# required against upstream's pre-2.0 SDK pin, which predates RP2350 support).
.pico-sdk:
	git clone --branch 2.2.0 --depth 1 https://github.com/raspberrypi/pico-sdk.git .pico-sdk
	cd .pico-sdk && git submodule update --init --depth 1

.ps2x2pico:
	git clone --depth 1 https://github.com/No0ne/ps2x2pico.git .ps2x2pico

deps: .pico-sdk .ps2x2pico

.PHONY: deps all clean clean-all

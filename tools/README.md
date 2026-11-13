# Tools: rebuild the PIX-LINK LV-AC06 image

These scripts turn the **official** OpenWrt image for the ASUS RT-AC1200 into the PIX-LINK LV-AC06 image described in the main guide. They need only **Python 3** (standard library) and a Linux machine that can mount SquashFS.

| File | What it does |
|---|---|
| `build_image.py` | The image repacker. Splits the official image, applies the changes, and writes a new sysupgrade image with all checksums recomputed |
| `fdt.py` | Device tree (DTB) reader/writer, used to edit the board description inside the kernel |
| `mksqfs.py` | Minimal SquashFS 4.0 (xz) builder, used to rebuild the root filesystem |

## Usage

```bash
# 1. Get the official image
wget https://archive.openwrt.org/releases/24.10.5/targets/ramips/mt76x8/openwrt-24.10.5-ramips-mt76x8-asus_rt-ac1200-squashfs-sysupgrade.bin
sha256sum openwrt-24.10.5-ramips-mt76x8-asus_rt-ac1200-squashfs-sysupgrade.bin
# 6cd905ca92153f9a2e19b519c0c65e8404e087142e28b079b824de54def3e255

# 2. Extract and mount its root filesystem
./build_image.py extract openwrt-24.10.5-ramips-mt76x8-asus_rt-ac1200-squashfs-sysupgrade.bin rootfs.sqfs
sudo mkdir -p /mnt/owrt
sudo mount -t squashfs -o loop,ro rootfs.sqfs /mnt/owrt

# 3. Build (as root, so every file in the mounted tree can be read)
sudo ./build_image.py build openwrt-24.10.5-ramips-mt76x8-asus_rt-ac1200-squashfs-sysupgrade.bin /mnt/owrt pixlink-ac06-sysupgrade.bin
sudo umount /mnt/owrt
```

The build is reproducible. With OpenWrt 24.10.5 as input, the output should have this SHA-256:

```text
b698c35087d0e88b6b6df795950247b179e07a3ad6878765f20d49770df7c6bc
```

## What `build_image.py` changes

**Device tree** (inside the kernel):

- model name `PIX-LINK LV-AC06`
- reset button on GPIO 11, WPS button on GPIO 38
- status LED on GPIO 37, 2.4 GHz LED on GPIO 44
- ASUS-only LED hog and USB LED removed, USB disabled
- pin groups `refclk`, `wdt`, `wled_an` set to GPIO mode
- switch port map `0x2f` (ports 0–3 LAN, port 4 WAN)

**Root filesystem** (`/etc/board.d/02_network` only):

- one VLAN per switch port
- `br-lan` with members `eth0.1` to `eth0.4`
- `wan` on `eth0.5`, with the MAC address from factory offset `0x2E`

The pins and ports are constants at the top of `build_image.py`, so they're easy to change.

## Notes

- The script checks every checksum in the input and re-parses its own output. It stops with an error if the input isn't what it expects, for example a different OpenWrt version whose `02_network` differs.
- The image keeps the board name `asus,rt-ac1200`, so OpenWrt's own upgrade checks accept it. Don't flash an unmodified ASUS image afterwards: it brings back the wrong pins and ports.
- `mksqfs.py` handles regular files, directories, symlinks and device nodes. It doesn't support hard links or extended attributes, neither of which the OpenWrt root filesystem uses.
- This is for the **16 MB flash mod** only. The image doesn't fit the original 4 MB chip.

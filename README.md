# OpenWrt on the PIX-LINK LV-AC06: from 4 MB of flash to a working router

![OpenWrt](https://img.shields.io/badge/OpenWrt-24.10.5-00B5E2)
![SoC](https://img.shields.io/badge/SoC-MT7628DAN-555)
![Wi-Fi](https://img.shields.io/badge/Wi--Fi-MT7612EN%205%20GHz-555)
![Status](https://img.shields.io/badge/status-working-success)

This guide shows how I turned a cheap, unsupported **PIX-LINK LV-AC06** AC1200 router into a working **OpenWrt 24.10.5** router with the **LuCI** web interface. That took a flash chip upgrade, some reverse engineering of the stock firmware, a custom device tree, and one very misleading power supply.

The PIX-LINK LV-AC06 is **not** officially supported by OpenWrt. Everything here was worked out for this board.

> [!WARNING]
> This involves soldering, writing to flash chips and running modified firmware. You can brick your router. Back up everything, and follow along at your own risk.

---

## Table of contents

1. [The result](#the-result)
2. [The hardware](#the-hardware)
3. [The problem: 4 MB of flash](#the-problem-4-mb-of-flash)
4. [Tools you need](#tools-you-need)
5. [Step 1: Back up the original flash](#step-1-back-up-the-original-flash)
6. [Step 2: Prepare the 16 MB chip](#step-2-prepare-the-16-mb-chip)
7. [Step 3: Swap the chips](#step-3-swap-the-chips)
8. [Step 4: Connect to the serial console](#step-4-connect-to-the-serial-console)
9. [Step 5: The stock firmware's flash lock](#step-5-the-stock-firmwares-flash-lock)
10. [Step 6: Boot OpenWrt from RAM over TFTP](#step-6-boot-openwrt-from-ram-over-tftp)
11. [Step 7: The mystery reboots](#step-7-the-mystery-reboots)
12. [Step 8: Build a firmware image for this board](#step-8-build-a-firmware-image-for-this-board)
13. [Step 9: Flash it permanently](#step-9-flash-it-permanently)
14. [Recovery](#recovery)
15. [Lessons learned](#lessons-learned)
16. [Open items](#open-items)

---

## The result

| Feature | Status |
|---|---|
| OpenWrt 24.10.5 with LuCI | ✅ |
| 16 MB flash, about 10 MB free for settings and packages | ✅ |
| 2.4 GHz Wi-Fi (MT7628, built in) | ✅ |
| 5 GHz Wi-Fi (MT7612EN) | ✅ |
| Separate WAN port | ✅ |
| LAN bridge, each port controllable on its own | ✅ |
| Status and Wi-Fi LEDs | ✅ |
| Reset button (short press reboots, hold 5 s to factory reset) | ✅ |
| WPS button | ✅ (needs the full `wpad` package to do anything) |

---

## The hardware

I identified it by opening the case and reading the chip markings.

| Part | Details |
|---|---|
| Board | `LV-AC06-V2.0 2019-03-13` |
| SoC | MediaTek **MT7628DAN**, MIPS 24KEc at 580 MHz, **64 MB DDR2 built in** |
| 2.4 GHz Wi-Fi | Built into the MT7628 |
| 5 GHz Wi-Fi | MediaTek **MT7612EN** on PCIe |
| Flash | XMC **XM25QH32B**, 32 Mbit = **4 MB** |
| Ethernet | 5 × 10/100 Mbit/s (the MT7628's built-in switch) |
| Bootloader | Ralink U-Boot 1.1.3 (2016) |
| Serial console | 57600 baud, 8N1 |

> [!NOTE]
> The LV-AC05 and LV-AC09 appear to use the same board, according to PIX-LINK's FCC filing. This guide may apply to them too, but I haven't tested them.

---

## The problem: 4 MB of flash

Both chips are well supported by OpenWrt, but **4 MB of flash is far too small** for current OpenWrt. The project stopped building 4 MB images years ago.

The fix is to **replace the flash chip with a 16 MB one**. I used a **Winbond W25Q128JVSQ**, which:

- is a common 3.3 V SPI NOR flash chip, costing about €1;
- is recognised by the stock U-Boot (its flash table includes the `W25Q128BV`, which reports the same ID);
- doesn't need 4-byte addressing, because it's exactly 16 MB.

> [!TIP]
> The "S" in W25Q128JV**S**Q means a wide 208-mil SOIC-8 package. If the original chip is the narrow 150-mil kind, the legs won't line up perfectly. Bending them slightly inward works.

---

## Tools you need

- A **CH341A USB programmer** and a **SOIC-8 test clip**. Use a 3.3 V version: many cheap black CH341A boards put 5 V on the data lines.
- A **USB-to-serial (UART) adapter** set to 3.3 V logic.
- A **soldering iron** or hot-air station, flux and a magnifier.
- A **W25Q128JVSQ**, or any other 16 MB SPI NOR flash chip.
- A **Linux PC**. I used Linux Mint.
- An **adequate power supply**. Seriously, read [Step 7](#step-7-the-mystery-reboots).

---

## Step 1: Back up the original flash

Clip the programmer onto the original XM25QH32B and **read it at least twice**.
I used AsProgrammer for reading the chips and backing up the firmwares. 


> [!IMPORTANT]
> This backup is your only way back to the stock firmware. It also holds the **Wi-Fi calibration data and MAC addresses**, which are unique to your board. Without them Wi-Fi won't work properly. Keep it safe, and **don't publish it**: it contains your stock Wi-Fi password and admin login in plain text.

### What's inside the 4 MB

| Offset | Size | Contents |
|---|---|---|
| `0x000000` | 192 KB | Bootloader (Ralink U-Boot 1.1.3) |
| `0x030000` | 64 KB | Config: the stock firmware's settings |
| `0x040000` | 64 KB | **Factory**: Wi-Fi calibration and MAC addresses |
| `0x050000` | about 3.7 MB | Firmware: Linux 2.6.36 kernel with a built-in ramdisk |

Inside the factory partition:

| Offset | Contents |
|---|---|
| `0x0000` | 2.4 GHz calibration block (starts with `28 76`, the MT7628 ID) |
| `0x0004` | 2.4 GHz MAC address |
| `0x0028` / `0x002E` | LAN / WAN MAC addresses |
| `0x8000` | 5 GHz calibration block (starts with `62 76`, the MT7612 ID) |

This is the standard MediaTek layout, which makes the OpenWrt port much easier.

---

## Step 2: Prepare the 16 MB chip

The new chip gets an exact copy of the old 4 MB, **padded with `0xFF`** (blank flash) up to 16 MB:

```bash
python3 - <<'EOF'
d = open('backup1.bin', 'rb').read()
assert len(d) == 4 * 1024 * 1024
open('backup_16MB.bin', 'wb').write(d + b'\xff' * (16 * 1024 * 1024 - len(d)))
EOF
```

> [!TIP]
> Don't use `truncate -s 16M`. It pads with zeros, but blank flash is `0xFF`.

Write it to the Winbond chip, then read it back and compare:


> [!NOTE]
> My spare W25Q128 turned out to come from an old and scavenged **ZTE ZXHN H108N** (a Vodafone ADSL router). Reading a donor chip first is a good habit: it confirms that the chip and the programmer both work. Its old contents get overwritten anyway.

---

## Step 3: Swap the chips

1. Desolder the XM25QH32B, using hot air or lots of flux with a soldering iron.
2. Solder in the W25Q128, with **pin 1 (the dot)** matching the board's marking.
3. Inspect the joints with a magnifier: look for solder bridges, and check that no nearby small parts have moved.
4. **Keep the original chip** as a physical backup.

---

## Step 4: Connect to the serial console

Find the 4-pin UART header (VCC, GND, TX, RX) and connect **only three wires**:

| Adapter | Board |
|---|---|
| GND | GND |
| TX | RX |
| RX | TX |

> [!CAUTION]
> **Don't connect VCC.** A USB serial adapter can't power the board, and if the board's own power adapter is also plugged in, the two supplies fight each other.

Open the console at **57600 baud, 8N1**:

```bash
picocom -b 57600 /dev/ttyUSB0
```

The first boot from the new chip showed that the swap had worked:

```text
flash manufacture id: ef, device id 40 18
find flash: W25Q128BV
...
Please choose the operation:
   1: Load system code to SDRAM via TFTP.
   2: Load system code then write to Flash via TFTP.
   3: Boot system code via Flash (default).
   4: Entr boot command line interface.
   9: Load Boot Loader code then write to Flash via TFTP.
```

The bootloader found the 16 MB chip, and it has a **TFTP boot menu**. From here on, no more programmer is needed.

---

## Step 5: The stock firmware's flash lock

The stock firmware booted, but the network never came up, and the console printed `#` once a second, forever:

```text
brd: module loaded
#
#
#
```

My first guess was the stock firmware's saved settings. Erasing them made no difference:

```text
MT7628 # spi erase 30000 10000
```

So I unpacked the stock kernel and disassembled its flash driver. It turned out to contain a **vendor lock on the flash chip**:

1. It sends the SPI "read unique ID" command (`0x4B`) to the flash chip.
2. It compares four bytes of the answer against hard-coded values (`C8 01 35 B5`).
3. If they match, it prints `Read GD Flash ID!` and carries on booting.
4. If they don't, it prints `#` every second and **never finishes booting**.

The original chip passes this check; the Winbond doesn't. The bootloader has no such check, which is why U-Boot was happy and the kernel wasn't.

Bypassing it takes **two MIPS instructions**, at kernel address `0x8014d214`:

```asm
; before:  addiu v0,zero,200        ; compare with 0xC8 ...
;          beq   v1,v0,...
; after:   j     0x8014d284         ; jump straight to the "OK" path
;          addiu s0,zero,1
```

I recompressed the patched kernel (LZMA), recalculated the uImage checksums, and RAM-booted it over TFTP. It printed `Read GD Flash ID!` and booted. The lock isn't needed for OpenWrt; I used this patched build only as a diagnostic tool (see Step 7).

---

## Step 6: Boot OpenWrt from RAM over TFTP

No official image exists for this router, so I looked for a supported device with **the same chips and flash layout**. The **ASUS RT-AC1200** matched:

| | ASUS RT-AC1200 | PIX-LINK LV-AC06 (16 MB) |
|---|---|---|
| Chips | MT7628 + MT7612E | MT7628 + MT7612EN |
| Flash | 16 MB | 16 MB |
| Bootloader / config / factory / firmware | `0x0` / `0x30000` / `0x40000` / `0x50000` | **identical** |
| Wi-Fi calibration | factory `0x0` and `0x8000` | **identical** |

### TFTP server on Linux Mint

```bash
sudo apt install tftpd-hpa
cd /srv/tftp
sudo wget -O openwrt.bin https://archive.openwrt.org/releases/24.10.5/targets/ramips/mt76x8/openwrt-24.10.5-ramips-mt76x8-asus_rt-ac1200-initramfs-kernel.bin
sha256sum openwrt.bin
# ea8c3afc8167886f89d6d062cb88548e6a820383d63d23ea0601e580a5e215a4
```

Give the laptop a static address on the wired port. Replace `enp3s0` with your interface name, from `ip -br link`:

```bash
sudo nmcli device set enp3s0 managed no
sudo ip addr flush dev enp3s0
sudo ip addr add 192.168.1.50/24 dev enp3s0
sudo ip link set enp3s0 up
```

### Boot it from U-Boot

Power on, press `4` during the countdown, then:

```text
setenv ipaddr 192.168.1.10
setenv serverip 192.168.1.50
tftpboot 0x82000000 openwrt.bin
bootm 0x82000000
```

This loads OpenWrt **into RAM only**. Nothing is written to flash, and pulling the power brings back the previous firmware.

---


### 8.3 Network defaults

I edited `/etc/board.d/02_network` inside the root filesystem so that a fresh install creates:

| Interface | Device | Switch port |
|---|---|---|
| `wan` / `wan6` (DHCP client) | `eth0.5` | 4 |
| `lan` (192.168.1.1) | bridge `br-lan` | |
| ↳ member | `eth0.1` | 0 |
| ↳ member | `eth0.2` | 1 |
| ↳ member | `eth0.3` | 2 |
| ↳ member | `eth0.4` | 3 |

Each LAN port is its own VLAN and bridge member, so you can **add or remove each port individually** in LuCI, or move one to a different network. The WAN MAC address comes from factory offset `0x2E`.

> [!NOTE]
> The MT7628 switch has no separate network device per port in OpenWrt, so VLANs are the only way to control ports individually. The trade-off is that LAN-to-LAN traffic goes through the CPU, not the switch hardware.

---

## Step 9: Flash it permanently

From the RAM-booted OpenWrt, either use **LuCI → System → Backup / Flash Firmware**, untick **Keep settings**, and upload the image, or:

```bash
scp -O pixlink-ac06-openwrt-24.10.5-sysupgrade.bin root@192.168.1.1:/tmp/
ssh root@192.168.1.1 sysupgrade -n /tmp/pixlink-ac06-openwrt-24.10.5-sysupgrade.bin
```

```text
upgrade: Upgrade completed
upgrade: Rebooting system...
...
MIPS: machine is PIX-LINK LV-AC06
...
 OpenWrt 24.10.5, r29087-d9c5716d1d
```

After the first boot:

1. Wait a minute or two for the settings area to be formatted (`mount | grep overlay` should show `jffs2`).
2. Set a root password: `passwd`, or System → Administration in LuCI.
3. Configure Wi-Fi in Network → Wireless: set the country code, WPA2/WPA3 and a password.

---

## Recovery

Something went wrong? None of this touches U-Boot, so you can always get back in:

| Situation | Fix |
|---|---|
| OpenWrt won't boot | U-Boot menu → `4` → TFTP-boot `openwrt.bin` into RAM → flash a working image |
| You want the stock firmware back | Program `backup1.bin` onto the **original 4 MB chip** and solder it back in |
| Settings are broken | Hold reset for more than 5 s, or run `firstboot && reboot` |

---

## Lessons learned

- **Always read a flash chip twice** and compare before changing anything.
- **Check the power supply early.** I spent hours on a "software" problem that was really a power supply problem.
- **Look for a donor device.** A supported router with the same chips and flash layout saves you from writing a port from scratch.
- **Vendor firmware can contain surprises,** like a lock that only accepts the original flash chip.
- **RAM-boot before you flash.** U-Boot's TFTP boot made every experiment risk-free.

---

## Open items

- [ ] Map the physical LAN sockets to switch ports 0–3, and renumber if needed


---

## Credits and references

- [OpenWrt](https://openwrt.org/) and the `ramips/mt76x8` target maintainers
- [ASUS RT-AC1200 support commit](https://git.chaotikum.org/freifunk-luebeck/openwrt/-/commit/3c4810fecc65dfc36bf43438c07749e9efe1bcb5.diff), the reference device
- [OpenWrt 24.10.5 mt76x8 images](https://archive.openwrt.org/releases/24.10.5/targets/ramips/mt76x8/)
- Reverse engineering, image building and analysis done with help from Claude (Anthropic)

---

*If this helped you, give the repo a ⭐ or open an issue with your results on other PIX-LINK models.*

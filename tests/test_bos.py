from usbinspect.bos import parse_bos

# Captured from real devices on a Framework 16.
GL_USB32_HUB = bytes.fromhex(  # 05e3:0625 Genesys USB3.2 hub, 10 Gbps
    "050f490005071002060000000a1003000e000108be001c100a00230000000011000030000500b000"
    "050031400a00b1400a0003100b14100400dfbd624c8c2b4c09ad0d8fe6de43e49c"
)
RTL8153_LAN = bytes.fromhex("050f160002071002020000000a1003020e00020aff07")  # 0bda:8153, 5 Gbps
VIA_BILLBOARD = bytes.fromhex(  # 2109:8817 dock billboard, DisplayPort configured
    "050f51000330100d030100000003000000000000000000000000000000000000000000000000000000"
    "000000002101000001ff010308100f00000400cd1410040000000000000000000000000000000000"
)
VIA_USB3_HUB = bytes.fromhex(  # 2109:0817, container id shared with its USB2 half
    "050f2a0003071002020000000a1003000e000104e700141004005cf3ee30d5074925b001802d79434c30"
)


def test_superspeed_plus_hub_max_10g():
    info = parse_bos(GL_USB32_HUB)
    assert info is not None
    assert info.ss_speeds == [5000]
    assert info.ssp_lane_mbps == [5000, 10000]
    assert info.max_mbps == 10000
    assert info.lpm is True


def test_gen1_device_max_5g():
    info = parse_bos(RTL8153_LAN)
    assert info.max_mbps == 5000
    assert info.ssp_lane_mbps == []


def test_billboard_reports_displayport_configured():
    info = parse_bos(VIA_BILLBOARD)
    assert info.billboard is not None
    assert len(info.billboard) == 1
    m = info.billboard[0]
    assert (m.svid, m.mode, m.state) == ("ff01", 1, "configured")


def test_billboard_failed_state():
    data = bytearray(VIA_BILLBOARD)
    data[5 + 8] = 0x02  # bmConfigured mode 0 -> "attempted but unsuccessful"
    assert parse_bos(bytes(data)).billboard[0].state == "failed"


def test_container_id():
    assert parse_bos(VIA_USB3_HUB).container_id == "30eef35c-07d5-2549-b001-802d79434c30"


def test_garbage_and_empty_input():
    assert parse_bos(None) is None
    assert parse_bos(b"") is None
    assert parse_bos(b"\x05\x02\x00\x00\x00") is None
    # truncated capability must not raise
    assert parse_bos(GL_USB32_HUB[:20]) is not None

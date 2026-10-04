"""AI-generated with assistance from GitHub Copilot.
Model: GitHub Copilot; the underlying model identifier was not exposed.
Human review and hardware validation were performed for this integration.
"""

import struct
from pathlib import Path

out_path = Path("analysis_out.txt")

with open(out_path, "w", encoding="utf-8") as out:
    out.write("Self-contained analysis script running...\n")
    
    for filename in ["dump002.pcapng", "error01.pcapng"]:
        filepath = Path(r"C:\GIT\DLNA32\Dumps") / filename
        out.write(f"\n==========================================\n")
        out.write(f"File: {filepath} (Exists: {filepath.exists()})\n")
        if not filepath.exists():
            continue
            
        data = filepath.read_bytes()
        out.write(f"Total size: {len(data)} bytes\n")
        
        # Simple pcapng block reader
        offset = 0
        endian = "<"
        packets = []
        
        while offset + 12 <= len(data):
            block_type = struct.unpack_from(endian + "I", data, offset)[0]
            block_len = struct.unpack_from(endian + "I", data, offset + 4)[0]
            if block_type == 0x0A0D0D0A: # SHB
                magic = data[offset + 8 : offset + 12]
                if magic == b"\x1a\x2b\x3c\x4d":
                    endian = ">"
                elif magic == b"\x4d\x3c\x2b\x1a":
                    endian = "<"
                block_len = struct.unpack_from(endian + "I", data, offset + 4)[0]
            
            if block_len < 12 or offset + block_len > len(data):
                break
                
            if block_type == 0x00000006: # EPB
                body = data[offset + 8 : offset + block_len - 4]
                if len(body) >= 20:
                    cap_len = struct.unpack_from(endian + "I", body, 12)[0]
                    pkt_data = body[20 : 20 + cap_len]
                    packets.append((len(packets) + 1, cap_len, pkt_data))
                    
            offset += block_len
            
        out.write(f"Parsed EPB packets: {len(packets)}\n")
        
        for frame, cap_len, pkt in packets:
            # Look for 0x0a commands or bulk payloads in packet data
            # Application data in USBPcap usually starts at offset 27 or 35 or contains 0x0a 0x17...
            pos = pkt.find(b"\x0a")
            if pos != -1 and pos + 10 <= len(pkt):
                out.write(f"  Frame {frame:4d} | len={cap_len:5d} | pos={pos} | hex: {pkt[pos:pos+32].hex(' ')}\n")
            elif cap_len >= 1000:
                out.write(f"  Frame {frame:4d} | BULK DATA len={cap_len:5d} | hex: {pkt[:16].hex(' ')}\n")

out.write("\nFinished successfully.\n")

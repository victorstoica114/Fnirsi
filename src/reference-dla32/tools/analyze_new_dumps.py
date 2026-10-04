"""AI-generated with assistance from GitHub Copilot.
Model: GitHub Copilot; the underlying model identifier was not exposed.
Human review and hardware validation were performed for this integration.
"""

import sys
from pathlib import Path

# Add overlay DLNA32 path to python path
sys.path.append(str(Path("DLNA32").resolve()))
from analyze_usb_capture import parse_pcapng, bulk_payload, usbpcap_candidates

def analyze_pcapng_file(pcap_path: Path):
    print(f"\n==========================================")
    print(f"Analyzing {pcap_path}")
    if not pcap_path.exists():
        print("File does not exist!")
        return

    interfaces, packets, warnings = parse_pcapng(pcap_path)
    print(f"Interfaces: {len(interfaces)}, Packets: {len(packets)}, Warnings: {len(warnings)}")
    
    cmd_transfers = []
    stream_chunks = []
    
    for p in packets:
        fields = usbpcap_candidates(p.data)
        ep = fields.get("endpoint_candidate")
        ep_dir = fields.get("direction")
        _, payload = bulk_payload(p.data)
        
        if payload:
            if ep == "0x02" or (payload.startswith(b"\x0a")):
                cmd_transfers.append((p.frame, ep, len(payload), payload[:32].hex(" ")))
            elif ep == "0x81" or len(payload) >= 1000:
                stream_chunks.append((p.frame, ep, len(payload), payload[:16].hex(" ")))
    
    print(f"\nCommand transfers (EP OUT 0x02 or 0x0a...): {len(cmd_transfers)}")
    for frame, ep, length, hex_prefix in cmd_transfers[:20]:
        print(f"  Frame {frame:4d} | EP {ep} | len {length:4d} | hex: {hex_prefix}")

    print(f"\nStream/Bulk transfers (EP IN 0x81 or large): {len(stream_chunks)}")
    for frame, ep, length, hex_prefix in stream_chunks[:10]:
        print(f"  Frame {frame:4d} | EP {ep} | len {length:5d} | hex: {hex_prefix}")

if __name__ == "__main__":
    analyze_pcapng_file(Path(r"C:\GIT\DLNA32\Dumps\dump002.pcapng"))
    analyze_pcapng_file(Path(r"C:\GIT\DLNA32\Dumps\error01.pcapng"))

"""AI-generated with assistance from GitHub Copilot.
Model: GitHub Copilot; the underlying model identifier was not exposed.
Human review and hardware validation were performed for this integration.
"""

import sys
from pathlib import Path

out_file = Path("analysis_out.txt")

with open(out_file, "w", encoding="utf-8") as f:
    f.write("Starting script...\n")
    try:
        sys.path.append(str(Path("DLNA32").resolve()))
        from analyze_usb_capture import parse_pcapng, bulk_payload, usbpcap_candidates

        for dump_name in ["dump002.pcapng", "error01.pcapng"]:
            pcap_path = Path(r"C:\GIT\DLNA32\Dumps") / dump_name
            f.write(f"\n==========================================\n")
            f.write(f"Analyzing {pcap_path}\n")
            if not pcap_path.exists():
                f.write("File does not exist!\n")
                continue

            interfaces, packets, warnings = parse_pcapng(pcap_path)
            f.write(f"Interfaces: {len(interfaces)}, Packets: {len(packets)}, Warnings: {len(warnings)}\n")
            
            cmd_transfers = []
            stream_chunks = []
            
            for p in packets:
                fields = usbpcap_candidates(p.data)
                ep = fields.get("endpoint_candidate")
                _, payload = bulk_payload(p.data)
                
                if payload:
                    if ep == "0x02" or payload.startswith(b"\x0a"):
                        cmd_transfers.append((p.frame, ep, len(payload), payload[:32].hex(" ")))
                    elif ep == "0x81" or len(payload) >= 1000:
                        stream_chunks.append((p.frame, ep, len(payload), payload[:16].hex(" ")))
            
            f.write(f"\nCommand transfers: {len(cmd_transfers)}\n")
            for frame, ep, length, hex_prefix in cmd_transfers[:30]:
                f.write(f"  Frame {frame:4d} | EP {ep} | len {length:4d} | hex: {hex_prefix}\n")

            f.write(f"\nStream/Bulk transfers: {len(stream_chunks)}\n")
            for frame, ep, length, hex_prefix in stream_chunks[:10]:
                f.write(f"  Frame {frame:4d} | EP {ep} | len {length:5d} | hex: {hex_prefix}\n")

    except Exception as e:
        f.write(f"Error: {e}\n")

    f.write("\nDone.\n")

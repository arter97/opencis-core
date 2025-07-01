from collections import Counter, defaultdict
from pprint import pprint
import sys
import matplotlib
import matplotlib.pyplot as plt

from typing import cast
from enum import Enum
from opencis.cxl.transport.transaction import (
    BasePacket,
    CxlMemBasePacket,
    CxlMemM2SReqPacket,
    CxlMemM2SRwDPacket,
)
from scapy.all import PcapReader


class DIRECTION(Enum):
    HOST_TO_SWITCH = 0
    SWITCH_TO_HOST = 1
    SWITCH_TO_DEVICE = 2
    DEVICE_TO_SWITCH = 3
    UNKNOWN = 4


# TODO: Make these arguments
pcap_file = sys.argv[1]
trace_ports = {
    8300: "host",
    8000: "switch",
    8100: "mctp",
}

addresses = []
counts = Counter()

with PcapReader(pcap_file) as pr:
    for n, packet in enumerate(pr):
        if packet.haslayer("TCP") and packet["TCP"].flags == 0x18:
            tcp = packet.getlayer("TCP")
            data_bytes = bytes(tcp.payload)
            data = int.from_bytes(data_bytes)

            # print(f"Packet {n}: {tcp.sport} -> {tcp.dport}, Data: 0x{data:x}")

            packet = BasePacket()
            packet.reset(data_bytes)

            connections = []
            with open("connections.txt", "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) == 2:
                        label, local_port_str = parts
                        try:
                            local_port = int(local_port_str)
                            connections.append((label, local_port))
                        except ValueError:
                            print(f"Invalid port number in line: {line.strip()}")

            direction = "null"

            port_match = -1
            class_name = f"unknown_{tcp.sport}_{tcp.dport}"
            for port in trace_ports:
                tup = [t for t in connections if t[0] == port]
                if tup:
                    port_match = tup[0]
                    class_name = tup[1]

            source = f"unknown_{tcp.sport}"
            destination = f"unknown_{tcp.dport}"
            for port, label in trace_ports.items():
                if port == tcp.sport:
                    source = label
                    break
                if port == tcp.dport:
                    destination = label
                    break
            for label, local_port in connections:
                if local_port == tcp.sport:
                    source = label
                if local_port == tcp.dport:
                    destination = label

            if source.startswith("unknown_") and destination.startswith("unknown_"):
                continue  # not ours

            direction = f"{source}-to-{destination}"
            counts[direction] += 1

            # if tcp.sport == port_match and tcp.dport == port:
            #     direction = f"{class_name}-to-{trace_ports[tcp.dport]}"
            #     counts[direction] += 1
            # elif tcp.dport == port_match and tcp.sport == port:
            #     direction = f"{trace_ports[tcp.sport]}-to-{class_name}"
            #     counts[direction] += 1

            # for label, local_port in connections:
            #     if tcp.sport == local_port:
            #         if tcp.dport in trace_ports:
            #             direction = f"{label}-to-{trace_ports[tcp.dport]}"
            #             counts[direction] += 1
            #     elif tcp.dport == local_port:
            #         if tcp.sport in trace_ports:
            #             direction = f"{trace_ports[tcp.sport]}-to-{label}"
            #             counts[direction] += 1

            # if packet.is_cxl_io():
            #     print(f"Packet {n} is a CXL IO packet: {packet}, direction: {direction}")
            if packet.is_cxl_mem():
                # print(f"Packet {n} is a CXL MEM packet: {packet}, direction: {direction}")

                cxl_mem_packet = CxlMemBasePacket()
                cxl_mem_packet.reset(data_bytes)

                if cxl_mem_packet.is_m2sreq():
                    # print(
                    #     f"Packet {n} is a CXL MEM M2S Request packet: {cxl_mem_packet}, direction: {direction}"
                    # )
                    m2s_packet = CxlMemM2SReqPacket()
                    m2s_packet.reset(data_bytes)
                    address = m2s_packet.get_address()
                    addresses.append(address)
                    print(
                        f"  M2S Request Packet: {m2s_packet}, Address: 0x{address:x}, "
                        f"rd: {m2s_packet.is_mem_rd()}, inv: {m2s_packet.is_mem_inv()}, direction: {direction}"
                    )
                    if m2s_packet.is_mem_rd():
                        counts["CxlMemM2SRead"] += 1
                    if m2s_packet.is_mem_inv():
                        counts["CxlMemM2SInv"] += 1
                elif cxl_mem_packet.is_m2srwd():
                    print(
                        f"Packet {n} is a CXL MEM M2S Request+Data packet: {cxl_mem_packet}, direction: {direction}"
                    )
                    m2srwd_packet = CxlMemM2SRwDPacket()
                    m2srwd_packet.reset(data_bytes)
                    address = m2srwd_packet.get_address()
                    addresses.append(address)
                    print(
                        f"  M2S Request+Data Packet: {m2srwd_packet}, Address: 0x{address:x}, "
                        f"wr: {m2srwd_packet.is_mem_wr()}, direction: {direction}"
                    )
                    if m2srwd_packet.is_mem_wr():
                        counts["CxlMemM2SDataWrite"] += 1
                    else:
                        counts["CxlMemM2SData"] += 1

                # elif cxl_mem_packet.is_m2sbirsp():
                #     print(
                #         f"Packet {n} is a CXL MEM M2S BI Response packet: {cxl_mem_packet}, direction: {direction}"
                #     )
                # elif cxl_mem_packet.is_s2mbisnp():
                #     print(
                #         f"Packet {n} is a CXL MEM S2M BI Snoop packet: {cxl_mem_packet}, direction: {direction}"
                #     )
            #     elif cxl_mem_packet.is_s2mndr():
            #         print(
            #             f"Packet {n} is a CXL MEM S2M NDR packet: {cxl_mem_packet}, direction: {direction}"
            #         )
            #     elif cxl_mem_packet.is_s2mdrs():
            #         print(
            #             f"Packet {n} is a CXL MEM S2M DRS packet: {cxl_mem_packet}, direction: {direction}"
            #         )
            # if packet.is_cxl_cache():
            #     print(f"Packet {n} is a CXL CACHE packet: {packet}, direction: {direction}")
            # if packet.is_cci():
            #     print(f"Packet {n} is a CXL CCI packet: {packet}, direction: {direction}")
            # if packet.is_sideband():
            #     print(f"Packet {n} is a CXL Sideband packet: {packet}, direction: {direction}")

    pprint(counts)

    # matplotlib.use("QtAgg")

    # plt.hist(addresses, bins=20, edgecolor="black")
    # plt.xlabel("Addresses")
    # plt.ylabel("Frequency")
    # plt.title("Address hotspots")

    # # Show the plot
    # plt.show()

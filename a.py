#!/usr/bin/env python

import sys
import struct
import os


def split_pcap_file(input_filename, num_splits=8):
    """
    Splits a pcap file into a specified number of smaller pcap files
    without using any external libraries.

    Args:
        input_filename (str): The path to the input pcap file.
        num_splits (int): The number of output files to create.
    """
    try:
        with open(input_filename, "rb") as f_in:
            # 1. Read the 24-byte PCAP global header.
            global_header = f_in.read(24)
            if len(global_header) < 24:
                print(f"Error: '{input_filename}' is not a valid pcap file or is corrupted.")
                return

            # 2. Create output files and write the global header to each.
            output_files = []
            try:
                # Get the base name and extension for output file naming
                base_name, ext = os.path.splitext(input_filename)
                for i in range(num_splits):
                    out_name = f"{base_name}_part_{i + 1}{ext}"
                    f_out = open(out_name, "wb")
                    f_out.write(global_header)
                    output_files.append(f_out)

                print(f"Created {num_splits} output files.")

                # 3. Distribute packets in a round-robin fashion.
                packet_count = 0
                while True:
                    # Read the 16-byte packet record header.
                    pkt_header_data = f_in.read(16)
                    if not pkt_header_data:
                        break  # End of file

                    # Unpack the header to find the length of the packet data.
                    # The 3rd integer (4 bytes) in the header is 'incl_len'.
                    # We only need this value, so we ignore the others.
                    # The endianness of the file does not affect the length reading
                    # for this purpose, as we write the exact same bytes.
                    incl_len = struct.unpack_from("<I", pkt_header_data, 8)[0]

                    # Read the actual packet data based on its length.
                    pkt_data = f_in.read(incl_len)
                    if len(pkt_data) < incl_len:
                        print("Warning: Incomplete packet found at the end of the file.")
                        break

                    # Determine which output file gets this packet.
                    target_file_index = packet_count % num_splits

                    # Write the packet header and data to the target file.
                    output_files[target_file_index].write(pkt_header_data)
                    output_files[target_file_index].write(pkt_data)

                    packet_count += 1

                print(f"Successfully distributed {packet_count} packets across {num_splits} files.")

            finally:
                # 4. Ensure all output files are closed.
                for f in output_files:
                    f.close()
                print("All output files have been saved and closed.")

    except FileNotFoundError:
        print(f"Error: The file '{input_filename}' was not found.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    # Check if a command-line argument is provided
    if len(sys.argv) < 2:
        print("Usage: python split_pcap.py <path_to_your_pcap_file>")
        sys.exit(1)

    # Get the input file from the command line
    pcap_file = sys.argv[1]

    # Run the splitting function
    split_pcap_file(pcap_file, num_splits=8)

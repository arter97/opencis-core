"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task
import asyncio
import logging
import struct
from typing import cast
import pytest
import pickle

from opencxl.apps.multi_logical_device import MultiLogicalDevice
from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.cxl.component.cxl_packet_processor import CxlPacketProcessor
from opencxl.cxl.component.packet_reader import PacketReader
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.pci.component.pci import EEUM_VID, SW_MLD_DID
from opencxl.util.number_const import MB
from opencxl.util.logger import logger
from opencxl.util.pci import create_bdf
from opencxl.cxl.transport.transaction import (
    CxlIoCfgRdPacket,
    CxlIoMemRdPacket,
    CxlIoMemWrPacket,
    CxlIoCfgWrPacket,
    CciMessageHeaderPacket,
    CciMessagePacket,
    CxlIoCompletionWithDataPacket,
    GetLdInfoRequestPacket,
    GetLdInfoResponsePacket,
    GetLdAllocationsRequestPacket,
    GetLdAllocationsResponsePacket,
    SetLdAllocationsRequestPacket,
    SetLdAllocationsResponsePacket,
    is_cxl_io_completion_status_sc,
    is_cxl_io_completion_status_ur,
)
from opencxl.cxl.transport.common import PAYLOAD_TYPE


# Test ld_id
# TODO: Test ld_id value from return (read) packets
@pytest.mark.asyncio
async def test_multi_logical_device_ld_id():
    logger.setLevel(logging.DEBUG)

    # Test 4 LDs
    num_ld = 4
    # Test routing to LD-ID 2
    target_ld_id = 2
    ld_size = 256 * MB
    logger.info(f"[PyTest] Creating {num_ld} LDs, testing LD-ID routing to {target_ld_id}")

    # Create MLD instance
    cxl_connections = [CxlConnection() for _ in range(num_ld)]
    mld = MultiLogicalDevice(
        port_index=1,
        memory_sizes=[ld_size] * num_ld,
        memory_files=[f"mld_mem{i}.bin" for i in range(num_ld)],
        test_mode=True,
        cxl_connections=cxl_connections,
    )

    # Start MLD pseudo server
    async def handle_client(reader, writer):
        global mld_pseudo_server_reader, mld_pseudo_server_packet_reader, mld_pseudo_server_writer  # pylint: disable=global-variable-undefined
        mld_pseudo_server_reader = reader
        mld_pseudo_server_packet_reader = PacketReader(reader, label="test_mmio")
        mld_pseudo_server_writer = writer
        assert mld_pseudo_server_writer is not None, "mld_pseudo_server_writer is NoneType"

    server = await asyncio.start_server(handle_client, "127.0.0.1", 8000)
    # This is cleaned up via 'server.wait_closed()' below
    asyncio.create_task(server.serve_forever())

    await server.start_serving()

    # Setup CxlPacketProcessor for MLD - connect to 127.0.0.1:8000
    mld_packet_processor_reader, mld_packet_processor_writer = await asyncio.open_connection(
        "127.0.0.1", 8000
    )
    mld_packet_processor = CxlPacketProcessor(
        mld_packet_processor_reader,
        mld_packet_processor_writer,
        cxl_connections,
        CXL_COMPONENT_TYPE.LD,
        label="ClientPortMld",
    )
    mld_packet_processor_task = create_task(mld_packet_processor.run())

    memory_base_address = 0xFE000000
    bar_size = 131072  # Empirical value

    async def configure_bar(
        target_ld_id: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        packet_reader = PacketReader(reader, label="configure_bar")
        packet_writer = writer

        logger.info("[PyTest] Settting Bar Address")
        # NOTE: Test Config Space Type0 Write - BAR WRITE
        packet = CxlIoCfgWrPacket.create(
            create_bdf(0, 0, 0),
            0x10,
            4,
            value=memory_base_address,
            is_type0=True,
            ld_id=target_ld_id,
        )
        packet_writer.write(bytes(packet))
        await packet_writer.drain()
        packet = await packet_reader.get_packet()
        assert packet.tlp_prefix.ld_id == target_ld_id
        assert is_cxl_io_completion_status_sc(packet)

    async def test_config_space(
        target_ld_id: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        # pylint: disable=duplicate-code
        packet_reader = PacketReader(reader, label="test_config_space")
        packet_writer = writer

        # NOTE: Test Config Space Type0 Read - VID/DID
        logger.info("[PyTest] Testing Config Space Type0 Read (VID/DID)")
        packet = CxlIoCfgRdPacket.create(
            create_bdf(0, 0, 0), 0, 4, is_type0=True, ld_id=target_ld_id
        )
        packet_writer.write(bytes(packet))
        await packet_writer.drain()
        packet = await packet_reader.get_packet()
        assert packet.tlp_prefix.ld_id == target_ld_id
        assert is_cxl_io_completion_status_sc(packet)
        cpld_packet = cast(CxlIoCompletionWithDataPacket, packet)
        assert cpld_packet.data == (EEUM_VID | (SW_MLD_DID << 16))

        # NOTE: Test Config Space Type0 Write - BAR WRITE
        logger.info("[PyTest] Testing Config Space Type0 Write (BAR)")
        packet = CxlIoCfgWrPacket.create(
            create_bdf(0, 0, 0), 0x10, 4, 0xFFFFFFFF, is_type0=True, ld_id=target_ld_id
        )
        packet_writer.write(bytes(packet))
        await packet_writer.drain()
        packet = await packet_reader.get_packet()
        assert packet.tlp_prefix.ld_id == target_ld_id
        assert is_cxl_io_completion_status_sc(packet)

        # NOTE: Test Config Space Type0 Read - BAR READ
        logger.info("[PyTest] Testing Config Space Type0 Read (BAR)")
        packet = CxlIoCfgRdPacket.create(
            create_bdf(0, 0, 0), 0x10, 4, is_type0=True, ld_id=target_ld_id
        )
        packet_writer.write(bytes(packet))
        await packet_writer.drain()
        packet = await packet_reader.get_packet()
        assert packet.tlp_prefix.ld_id == target_ld_id
        assert is_cxl_io_completion_status_sc(packet)
        cpld_packet = cast(CxlIoCompletionWithDataPacket, packet)
        size = 0xFFFFFFFF - cpld_packet.data + 1
        assert size == bar_size

        # NOTE: Test Config Space Type1 Read - VID/DID: Expect UR
        logger.info("[PyTest] Testing Config Space Type1 Read - Expect UR")
        packet = CxlIoCfgRdPacket.create(
            create_bdf(0, 0, 0), 0, 4, is_type0=False, ld_id=target_ld_id
        )
        packet_writer.write(bytes(packet))
        await packet_writer.drain()
        packet = await packet_reader.get_packet()
        assert packet.tlp_prefix.ld_id == target_ld_id
        assert is_cxl_io_completion_status_ur(packet)

        # NOTE: Test Config Space Type1 Write - BAR WRITE: Expect UR
        logger.info("[PyTest] Testing Config Space Type1 Write - Expect UR")
        packet = CxlIoCfgWrPacket.create(
            create_bdf(0, 0, 0), 0x10, 4, 0xFFFFFFFF, is_type0=False, ld_id=target_ld_id
        )
        packet_writer.write(bytes(packet))
        await packet_writer.drain()
        packet = await packet_reader.get_packet()
        assert packet.tlp_prefix.ld_id == target_ld_id
        assert is_cxl_io_completion_status_ur(packet)

    async def setup_hdm_decoder(
        num_ld: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        # pylint: disable=duplicate-code
        packet_reader = PacketReader(reader, label="setup_hdm_decoder")
        packet_writer = writer

        register_offset = memory_base_address + 0x1014
        decoder_index = 0
        hpa_base = 0x0
        hpa_size = ld_size
        dpa_skip = 0
        interleaving_granularity = 0
        interleaving_way = 0

        for ld_id in range(num_ld):
            # NOTE: Test Config Space Type0 Write - BAR WRITE
            packet = CxlIoCfgWrPacket.create(
                create_bdf(0, 0, 0),
                0x10,
                4,
                value=register_offset,
                is_type0=True,
                ld_id=ld_id,
            )
            packet_writer.write(bytes(packet))
            await packet_writer.drain()
            packet = await packet_reader.get_packet()
            assert is_cxl_io_completion_status_sc(packet)
            assert packet.tlp_prefix.ld_id == ld_id

            # Use HPA = DPA
            logger.info(f"[PyTest] Setting up HDM Decoder for {ld_id}")

            dpa_skip_low_offset = 0x20 * decoder_index + 0x24 + register_offset
            dpa_skip_high_offset = 0x20 * decoder_index + 0x28 + register_offset
            dpa_skip_low = dpa_skip & 0xFFFFFFFF
            dpa_skip_high = (dpa_skip >> 32) & 0xFFFFFFFF

            packet = CxlIoMemWrPacket.create(dpa_skip_low_offset, 4, dpa_skip_low, ld_id=ld_id)
            writer.write(bytes(packet))
            await writer.drain()

            packet = CxlIoMemWrPacket.create(dpa_skip_high_offset, 4, dpa_skip_high, ld_id=ld_id)
            writer.write(bytes(packet))
            await writer.drain()

            decoder_base_low_offset = 0x20 * decoder_index + 0x10 + register_offset
            decoder_base_high_offset = 0x20 * decoder_index + 0x14 + register_offset
            decoder_size_low_offset = 0x20 * decoder_index + 0x18 + register_offset
            decoder_size_high_offset = 0x20 * decoder_index + 0x1C + register_offset
            decoder_control_register_offset = 0x20 * decoder_index + 0x20 + register_offset

            commit = 1

            decoder_base_low = hpa_base & 0xFFFFFFFF
            decoder_base_high = (hpa_base >> 32) & 0xFFFFFFFF
            decoder_size_low = hpa_size & 0xFFFFFFFF
            decoder_size_high = (hpa_size >> 32) & 0xFFFFFFFF

            decoder_control = (
                interleaving_granularity & 0xF | (interleaving_way & 0xF) << 4 | commit << 9
            )

            packet = CxlIoMemWrPacket.create(
                decoder_base_low_offset, 4, decoder_base_low, ld_id=ld_id
            )
            writer.write(bytes(packet))
            await writer.drain()

            packet = CxlIoMemWrPacket.create(
                decoder_base_high_offset, 4, decoder_base_high, ld_id=ld_id
            )
            writer.write(bytes(packet))
            await writer.drain()

            packet = CxlIoMemWrPacket.create(
                decoder_size_low_offset, 4, decoder_size_low, ld_id=ld_id
            )
            writer.write(bytes(packet))
            await writer.drain()

            packet = CxlIoMemWrPacket.create(
                decoder_size_high_offset, 4, decoder_size_high, ld_id=ld_id
            )
            writer.write(bytes(packet))
            await writer.drain()

            packet = CxlIoMemWrPacket.create(
                decoder_control_register_offset, 4, decoder_control, ld_id=ld_id
            )
            writer.write(bytes(packet))
            await writer.drain()

            register_offset += 0x200000

        logger.info("[PyTest] HDM Decoder setup complete")

    async def test_mmio(
        target_ld_id: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        packet_reader = PacketReader(reader, label="test_mmio")
        packet_writer = writer

        logger.info("[PyTest] Accessing MMIO register")

        # NOTE: Write 0xDEADBEEF
        data = 0xDEADBEEF
        packet = CxlIoMemWrPacket.create(memory_base_address, 4, data=data, ld_id=target_ld_id)
        packet_writer.write(bytes(packet))
        await packet_writer.drain()

        # NOTE: Confirm 0xDEADBEEF is written
        packet = CxlIoMemRdPacket.create(memory_base_address, 4, ld_id=target_ld_id)
        packet_writer.write(bytes(packet))
        await packet_writer.drain()
        packet = await packet_reader.get_packet()
        assert is_cxl_io_completion_status_sc(packet)
        assert packet.tlp_prefix.ld_id == target_ld_id
        cpld_packet = cast(CxlIoCompletionWithDataPacket, packet)
        logger.info(f"[PyTest] Received CXL.io packet: {cpld_packet}")
        assert cpld_packet.data == data

        # NOTE: Write OOB (Upper Boundary), Expect No Error
        packet = CxlIoMemWrPacket.create(
            memory_base_address + bar_size, 4, data=data, ld_id=target_ld_id
        )
        packet_writer.write(bytes(packet))
        await packet_writer.drain()

        # NOTE: Write OOB (Lower Boundary), Expect No Error
        packet = CxlIoMemWrPacket.create(memory_base_address - 4, 4, data=data, ld_id=target_ld_id)
        packet_writer.write(bytes(packet))
        await packet_writer.drain()

        # NOTE: Read OOB (Upper Boundary), Expect 0
        packet = CxlIoMemRdPacket.create(memory_base_address + bar_size, 4, ld_id=target_ld_id)
        packet_writer.write(bytes(packet))
        await packet_writer.drain()
        packet = await packet_reader.get_packet()
        # assert is_cxl_io_completion_status_sc(packet)
        # assert packet.tlp_prefix.ld_id == target_ld_id
        # cpld_packet = cast(CxlIoCompletionWithDataPacket, packet)
        # assert cpld_packet.data == 0

        # NOTE: Read OOB (Lower Boundary), Expect 0
        packet = CxlIoMemRdPacket.create(memory_base_address - 4, 4, ld_id=target_ld_id)
        packet_writer.write(bytes(packet))
        await packet_writer.drain()
        packet = await packet_reader.get_packet()
        assert is_cxl_io_completion_status_sc(packet)
        assert packet.tlp_prefix.ld_id == target_ld_id
        cpld_packet = cast(CxlIoCompletionWithDataPacket, packet)
        assert cpld_packet.data == 0

    def list_to_bytes(num_list):
        # 각 숫자를 2바이트로 변환하고 구분자 b'\xFF\xFF'를 삽입
        return b"".join(num.to_bytes(2, "big") + b"\xFF\xFF" for num in num_list)[:-2]

    def bytes_to_list(byte_data):
        # 구분자 b'\xFF\xFF'로 분리하고, 각 조각을 정수로 변환
        byte_chunks = byte_data.split(b"\xFF\xFF")
        return [int.from_bytes(chunk, "big") for chunk in byte_chunks if chunk]

    async def send_packets(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        packet_reader = PacketReader(reader, label="send_packets")
        packet_writer = writer

        logger.info(
            "[PyTest] Sending tunnel management command request packets from switch to MLD Start"
        )

        # logger.info(f"[PyTest]  Get LD info  Start")
        # get_ld_info_request_packet = GetLdInfoRequestPacket.create(port_or_ldid=0x3, message_catecory=0)
        # packet_writer.write(bytes(get_ld_info_request_packet))
        # await packet_writer.drain()
        # packet = await packet_reader.get_packet()
        # logger.info(f"[PyTest] Received Get LD Info Response: {packet}")
        # get_ld_info_response_packet = cast(GetLdInfoResponsePacket, packet)
        # ld_count = get_ld_info_response_packet.payload.ld_count
        # memory_size = get_ld_info_response_packet.payload.memory_size
        # # logger.info(f"[PyTest] ld_count: {ld_count}, memory_size: {memory_size} ")
        # # logger.info(f"[PyTest]  Get LD info  Finish")

        # logger.info(f"[PyTest]  ccimessage: {get_ld_info_response_packet.header_data}")

        request_cci_header = CciMessageHeaderPacket()
        request_cci_header.message_category = 0
        request_cci_header.message_tag = 0
        request_cci_header.command_opcode = 0x5400
        request_cci_header.set_message_payload_length(0)
        request_cci_header.background_operation = 0
        request_cci_header.return_code = 0
        request_cci_header.vendor_specific_extended_status = 0
        request_cci_header.background_operation = 0

        request_cci_message = CciMessagePacket.create(
            request_cci_header, b"\x00\x00\x00@\x00\x00\x00\x00\x04\x00\x00"
        )
        get_ld_info_request_packet = GetLdInfoRequestPacket.create_from_ccimessage(
            port_or_ldid=0x3, ccimessage=request_cci_message
        )
        packet_writer.write(bytes(get_ld_info_request_packet))
        await packet_writer.drain()
        packet = await packet_reader.get_packet()
        logger.info(f"[PyTest] Received Get LD Info Response: {packet}")
        get_ld_info_response_packet = cast(GetLdInfoResponsePacket, packet)

        await asyncio.sleep(1)

        cci_message = get_ld_info_response_packet.create_ccimessage()

        # print the

        # cci_message._data._data 를 8 bytes, 2 bytes, 1 bytes 로 나누어서 각각의 변수에 저장
        data = int.to_bytes(cci_message.data, 11, "little")
        memory_size = data[0:8]
        ld_count = data[8:9]
        qos_telemetry_capability = data[10]
        logger.info(
            f"[PyTest]  CCI Message memory_size: {int.from_bytes(bytes(memory_size), 'little'):x}"
        )
        logger.info(f"[PyTest]  CCI Message ld_count: {int.from_bytes(bytes(ld_count), 'little')}")
        logger.info(
            f"[PyTest]  CCI Message qos_telemetry_capability: {int.from_bytes(bytes(qos_telemetry_capability), 'little')}"
        )

        # Get Ld Allocations Packet
        # logger.info(f"[PyTest]  Get LD Allocations Start")
        # get_ld_allocations_request_packet = GetLdAllocationsRequestPacket.create(port_or_ldid=0x3, start_ld_id=1, ld_allocation_list_limit=3)
        # packet_writer.write(bytes(get_ld_allocations_request_packet))
        # await packet_writer.drain()

        # packet = await packet_reader.get_packet()
        # logger.info(f"[PyTest] Received Get LD Allocations Response: {type(packet)}")
        # get_ld_allocations_response_packet = cast(GetLdAllocationsResponsePacket, packet)
        # number_of_lds = get_ld_allocations_response_packet.get_ld_allocations_response_payload.number_of_lds
        # memory_granularity = get_ld_allocations_response_packet.get_ld_allocations_response_payload.memory_granularity
        # start_ld_id = get_ld_allocations_response_packet.get_ld_allocations_response_payload.start_ld_id
        # ld_allocation_list_length = get_ld_allocations_response_packet.get_ld_allocations_response_payload.ld_allocation_list_length
        # ld_allocation_list = list(bytes(get_ld_allocations_response_packet.ld_allocation_list))
        # logger.info(f"[PyTest] number_of_lds: {number_of_lds}, memory_granularity: {memory_granularity},start_ld_id: {start_ld_id}, ld_allocation_list_length: {ld_allocation_list_length}, ld_allocation_list: {ld_allocation_list}")
        # # logger.info(f"[PyTest]  get ld allocations field: {get_ld_allocations_response_packet._fields}")
        # logger.info(f"[PyTest]  Get LD Allocations Finish")
        # await asyncio.sleep(1)

        # # Set Ld Allocations Packet
        # logger.info(f"[PyTest]  Set LD Allocations Start")
        # set_ld_allocations_request_packet = SetLdAllocationsRequestPacket.create(port_or_ldid=0x3, number_of_lds=4, start_ld_id=0, ld_allocation_list=bytes([0, 1]))
        # packet_writer.write(bytes(set_ld_allocations_request_packet))
        # await packet_writer.drain()
        # packet = await packet_reader.get_packet()
        # logger.info(f"[PyTest] Received Set LD Allocations Response: {packet}")
        # set_ld_allocations_response_packet = cast(SetLdAllocationsResponsePacket, packet)
        # number_of_lds = set_ld_allocations_response_packet.set_ld_allocations_response_payload.number_of_lds
        # start_ld_id = set_ld_allocations_response_packet.set_ld_allocations_response_payload.start_ld_id
        # ld_allocation_list = list(bytes(set_ld_allocations_response_packet.ld_allocation_list))
        # logger.info(f"[PyTest] number_of_lds: {number_of_lds}, start_ld_id: {start_ld_id}, ld_allocation_list: {ld_allocation_list}")
        # logger.info(f"[PyTest]  Set LD Allocations Finish")

        logger.info(
            "[PyTest] Sending tunnel management command request packets from switch to MLD Finish"
        )

        await asyncio.sleep(1)

    # Start MLD
    mld_task = create_task(mld.run())

    # Start the tests
    await mld.wait_for_ready()
    # Test MLD LD-ID handling
    await setup_hdm_decoder(num_ld, mld_pseudo_server_reader, mld_pseudo_server_writer)
    await configure_bar(target_ld_id, mld_pseudo_server_reader, mld_pseudo_server_writer)
    await test_config_space(target_ld_id, mld_pseudo_server_reader, mld_pseudo_server_writer)
    await test_mmio(target_ld_id, mld_pseudo_server_reader, mld_pseudo_server_writer)
    await send_packets(mld_pseudo_server_reader, mld_pseudo_server_writer)

    # Stop all devices
    await mld_packet_processor.stop()
    await mld_packet_processor_task
    await mld.stop()
    await mld_task

    # Stop pseudo server
    server.close()
    await server.wait_closed()

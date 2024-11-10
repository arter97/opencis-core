"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather
from typing import Optional, cast, List
from opencxl.util.component import RunnableComponent
from opencxl.util.logger import logger
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.cxl.transport.transaction import (
    CciRequestPacket,
    CciMessagePacket,
    GetLdInfoResponsePacket,
    GetLdAllocationsRequestPacket,
    GetLdAllocationsResponsePacket,
    SetLdAllocationsRequestPacket,
    SetLdAllocationsResponsePacket,
)
from opencxl.cxl.component.cxl_memory_device_component import CxlMemoryDeviceComponent

# import MctpPacketProcessor
from opencxl.cxl.device.cxl_type3_device import CXL_T3_DEV_TYPE


# 리스트를 하나의 int로 변환
def list_to_int(allocated_ld_list):
    result = 0
    for value in allocated_ld_list:
        result = (result << 8) | value  # 각 값을 8비트씩 왼쪽으로 이동하고 병합
    return result


# int를 다시 리스트로 변환
def int_to_list(allocated_ld_list_bytes, length):
    allocated_ld_list = []
    for _ in range(length):
        allocated_ld_list.insert(0, allocated_ld_list_bytes & 0xFF)  # 마지막 8비트를 리스트에 추가
        allocated_ld_list_bytes >>= 8  # 8비트씩 오른쪽으로 이동
    return allocated_ld_list


def list_to_bytes(num_list):
    # 각 숫자를 2바이트로 변환하고 구분자 b'\xFF\xFF'를 삽입
    return b"".join(num.to_bytes(2, "big") + b"\xFF\xFF" for num in num_list)[:-2]


# def bytes_to_list(byte_data):
#     # 구분자 b'\xFF\xFF'로 분리하고, 각 조각을 정수로 변환
#     byte_chunks = byte_data.split(b'\xFF\xFF')
#     return [int.from_bytes(chunk, 'big') for chunk in byte_chunks if chunk]


# Replaces MctpPacketProcessor
class FMLD(RunnableComponent):
    def __init__(
        self,
        upstream_fifo: FifoPair,
        ld_count: int,
        dev_type: CXL_T3_DEV_TYPE,
        downstream_fifo: Optional[FifoPair] = None,  # LD로 가는 FM API를 구한할 때 추가해야함.
        label: Optional[str] = None,
    ):
        super().__init__(label)
        self._downstream_fifo = downstream_fifo
        self._upstream_fifo = upstream_fifo
        self._ld_count = ld_count
        self._dev_type = dev_type
        self._memory_granularity = 256

        # 0 : allocated , disabled
        # 1 : not allocated , enabled
        self._ld_dict = {i: 1 for i in range(ld_count)}

    async def _process_get_ld_info_packet(self, get_ld_info_request_packet: CciRequestPacket):
        if get_ld_info_request_packet.get_command_opcode() != 0x5400:
            raise Exception("Invalid command opcode")
        logger.info(f"Get LD Info Request: {get_ld_info_request_packet}")
        memory_size = self._ld_count * 1024 * 1024 * 256
        logger.info(f"Memory Size: {memory_size:x}")
        logger.info(f"LD Count: {self._ld_count}")

        get_ld_info_response_packet = GetLdInfoResponsePacket.create(
            memory_size=memory_size, ld_count=self._ld_count
        )
        logger.info(f"Get LD Info Response: {get_ld_info_response_packet}")
        await self._upstream_fifo.target_to_host.put(get_ld_info_response_packet)
        logger.info(f"Get LD Info Response sent done")

    async def _process_get_ld_allocations_packet(
        self, get_ld_allocations_packet: GetLdAllocationsRequestPacket
    ):
        if get_ld_allocations_packet.get_command_opcode() != 0x5401:
            raise Exception("Invalid command opcode")
        logger.info(f"Get LD Allocations: {get_ld_allocations_packet}")

        start_ld_id = get_ld_allocations_packet.get_start_ld_id()
        ld_alloc_list_limit = get_ld_allocations_packet.get_ld_allocation_list_limit()

        if start_ld_id < 0 or start_ld_id >= len(self._ld_dict):
            raise Exception("Invalid start_ld_id")

        # sefl._allocated_ld_dict의 key 갯수
        max_len_ld_list = len(self._ld_dict) - start_ld_id
        if ld_alloc_list_limit < max_len_ld_list:
            ld_length = ld_alloc_list_limit
        else:
            ld_length = max_len_ld_list

        # calculate number of lds
        number_of_lds = 0
        for i in range(max_len_ld_list):
            if self._ld_dict.get(start_ld_id + i) == 1:
                number_of_lds += 1

        allocated_ld: List[int] = []
        allocated_ld_length = 0
        # make ld allocation list
        for i in range(ld_length):
            if self._ld_dict.get(start_ld_id + i) == 1:
                allocated_ld.append(1)
                allocated_ld_length += 1
            elif self._ld_dict.get(start_ld_id + i) == 0:
                break

        # allocated_ld = list_to_bytes(allocated_ld)

        print("allocated_ld@@", allocated_ld)
        get_ld_allocations_response_packet = GetLdAllocationsResponsePacket.create(
            number_of_lds=number_of_lds,
            memory_granularity=0,
            start_ld_id=start_ld_id,
            ld_allocation_list_length=allocated_ld_length,
            ld_allocation_list=bytes(allocated_ld),
        )
        print(
            "get_ld_allocations_response_packet",
            get_ld_allocations_response_packet.ld_allocation_list,
        )
        # get_ld_allocations_response_packet = GetLdAllocationsResponsePacket.create(
        #     number_of_lds= 1, memory_granularity=0, start_ld_id=1, ld_allocation_list_length=2, ld_allocation_list=12
        #     )

        await self._upstream_fifo.target_to_host.put(get_ld_allocations_response_packet)
        logger.info(f"Get LD Allocations Response sent done")

    async def _process_set_ld_allocations_packet(
        self, set_ld_allocations_packet: SetLdAllocationsRequestPacket
    ):
        if set_ld_allocations_packet.get_command_opcode() != 0x5402:
            raise Exception("Invalid command opcode")
        logger.info(f"Set LD Allocations: {set_ld_allocations_packet}")

        number_of_lds = set_ld_allocations_packet.get_number_of_lds()
        start_ld_id = set_ld_allocations_packet.get_start_ld_id()
        ld_allocation_list = int_to_list(
            set_ld_allocations_packet.get_ld_allocation_list(), number_of_lds
        )

        if len(self._ld_dict) - start_ld_id < number_of_lds:
            number_of_lds = len(self._ld_dict) - start_ld_id

        response_number_of_lds = 0
        response_ld_allocated_list = []

        # make ld allocation list
        for i in range(number_of_lds):
            if self._ld_dict.get(start_ld_id + i) >= ld_allocation_list[i]:
                response_ld_allocated_list.append(ld_allocation_list[i])
                self._ld_dict[start_ld_id + i] = (
                    self._ld_dict[start_ld_id + i] - ld_allocation_list[i]
                )
                response_number_of_lds += 1
            elif self._ld_dict.get(start_ld_id + i) == 0:
                response_ld_allocated_list.append(0)
            elif self._ld_dict.get(start_ld_id + i) < ld_allocation_list[i]:
                response_ld_allocated_list.append(self._ld_dict.get(start_ld_id + i))
                self._ld_dict[start_ld_id + i] = 0
                response_number_of_lds += 1

        # data = [0,1,1]
        # ex_data = bytes(data)
        # print(f"ex_data : {ex_data}")
        # # make a ori_data from the ex_data
        # ori_data = list(ex_data)
        # print(f"ori_data : {ori_data}")

        print("response_ld_allocated_list", response_ld_allocated_list)
        set_ld_allocations_response_packet = SetLdAllocationsResponsePacket.create(
            number_of_lds=response_number_of_lds,
            start_ld_id=start_ld_id,
            ld_allocation_list=bytes(response_ld_allocated_list),
        )
        await self._upstream_fifo.target_to_host.put(set_ld_allocations_response_packet)
        logger.info(f"Set LD Allocations Response sent done")

    async def _process_fm_to_target(self):
        logger.info(self._create_message("Started processing fm to ld packets"))
        while True:
            packet = await self._upstream_fifo.host_to_target.get()
            print("fmld received packet", packet)
            if packet is None:
                logger.info(self._create_message("Stopped fm to ld packets"))
                break
            logger.info(self._create_message("Received fm to ld Packet"))

            # packet = cast(CciRequestPacket, packet)
            if packet.get_command_opcode() == 0x5400:  # Get Ld info
                await self._process_get_ld_info_packet(packet)
            elif packet.get_command_opcode() == 0x5401:  # Get Ld Allocations
                packet = cast(GetLdAllocationsRequestPacket, packet)
                await self._process_get_ld_allocations_packet(packet)
            elif packet.get_command_opcode() == 0x5402:  # Set Ld Allocations
                await self._process_set_ld_allocations_packet(packet)

    # TODO : This function should be implemented for LD to FM API
    async def _process_target_to_fm(self):
        if self._downstream_fifo is None:
            logger.info(self._create_message("Skipped processing ld to fm packets"))
            return
        logger.info(self._create_message("Started processing ld to fm packets"))
        while True:
            packet = await self._downstream_fifo.target_to_host.get()
            if packet is None:
                logger.info(self._create_message("Stopped ld to fm packets"))
                break
            logger.info(self._create_message("Received ld to fm Packet"))
            await self._upstream_fifo.target_to_host.put(packet)

    async def _run(self):
        tasks = [
            create_task(self._process_fm_to_target()),
            create_task(self._process_target_to_fm()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        if self._downstream_fifo is not None:
            await self._downstream_fifo.target_to_host.put(None)
        await self._upstream_fifo.host_to_target.put(None)

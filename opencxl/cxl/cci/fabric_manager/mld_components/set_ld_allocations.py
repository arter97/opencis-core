"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass, field
from struct import pack, unpack
from typing import ClassVar
from opencxl.cxl.component.cci_executor import (
    CciBackgroundCommand,
    CciRequest,
    CciResponse,
    ProgressCallback,
)

from opencxl.cxl.cci.common import CCI_FM_API_COMMAND_OPCODE, CCI_RETURN_CODE
from opencxl.util.logger import logger


@dataclass
class SetLdAllocationsRequestPayload:
    number_of_lds: int = field(default=0)  # 1byte
    start_ld_id: int = field(default=0)  # 1byte
    ld_allocation_list: bytes = field(default=b"")  # variable length

    @classmethod
    def parse(cls, data: bytes):
        number_of_lds, start_ld_id = unpack("<BB", data[:2])
        ld_allocation_list = data[4:]
        return cls(number_of_lds, start_ld_id, ld_allocation_list)

    def dump(self):
        data = bytearray()
        data.extend(pack("<BB", self.number_of_lds, self.start_ld_id))
        data.extend(b"\x00\x00")  # reserved 2바이트
        for range_1, range_2 in self.ld_allocation_list:
            data.extend(pack("<QQ", range_1, range_2))
        return bytes(data)

    def get_pretty_print(self):
        allocation_str = "\n".join(
            f"  - Range 1: {range_1}, Range 2: {range_2}"
            for range_1, range_2 in self.ld_allocation_list
        )
        return (
            f"- NUMBER_OF_LDS: {self.number_of_lds}\n"
            f"- START_LD_ID: {self.start_ld_id}\n"
            f"- LD_ALLOCATION_LIST:\n{allocation_str}"
        )


@dataclass
class SetLdAllocationsResponsePayload:
    number_of_lds: int = field(default=0)  # 1byte
    start_ld_id: int = field(default=0)
    # reversed 2 bytes
    ld_allocation_list: bytes = field(default=b"")  # variable length

    @classmethod
    def parse(cls, data: bytes):
        number_of_lds, start_ld_id = unpack("<BB", data[:2])
        ld_allocation_list = data[4:]
        return cls(number_of_lds, start_ld_id, ld_allocation_list)

    def dump(self):
        data = bytearray()
        data.extend(pack("<BB", self.number_of_lds, self.start_ld_id))
        data.extend(b"\x00\x00")
        for range_1, range_2 in self.ld_allocation_list:
            data.extend(pack("<QQ", range_1, range_2))
        return bytes(data)

    def get_pretty_print(self):
        allocation_str = "\n".join(
            f"  - Range 1: {range_1}, Range 2: {range_2}"
            for range_1, range_2 in self.ld_allocation_list
        )
        return (
            f"- NUMBER_OF_LDS: {self.number_of_lds}\n"
            f"- START_LD_ID: {self.start_ld_id}\n"
            f"- LD_ALLOCATION_LIST:\n{allocation_str}"
        )


class SetLdAllocationsCommand(CciBackgroundCommand):
    OPCODE = CCI_FM_API_COMMAND_OPCODE.SET_LD_ALLOCATIONS

    def __init__(
        self,
        physical_port_manager: PhysicalPortManager,
        virtual_switch_manager: VirtualSwitchManager,
    ):
        super().__init__(self.OPCODE)
        self._physical_port_manager = physical_port_manager
        self._virtual_switch_manager = virtual_switch_manager

    async def _execute(self, request: CciRequest, callback: ProgressCallback) -> CciResponse:
        request_payload = self.parse_request_payload(request.payload)
        number_of_lds = request_payload.number_of_lds
        start_ld_id = request_payload.start_ld_id
        ld_allocation_list = request_payload.ld_allocation_list

        vcs_count = self._virtual_switch_manager.get_virtual_switch_counts()
        if vcs_id >= vcs_count:
            logger.debug(self._create_message("VCS ID is out of bound"))

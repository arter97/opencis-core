"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather
from typing import Optional, cast, List, Dict
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.mctp.mctp_connection import MctpConnection
from opencxl.cxl.component.cci_executor import (
    CciExecutor,
    CciRequest,
    CciResponse,
    CciCommand,
    CciBackgroundStatus,
)
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.switch_connection_manager import SwitchConnectionManager
from opencxl.cxl.component.cxl_component import (
    PORT_TYPE,
    PortConfig,
)
from opencxl.cxl.transport.transaction import (
    CciMessagePacket,
    CciMessageHeaderPacket,
    CCI_MCTP_MESSAGE_CATEGORY,
    CciBasePacket,
    CciPayloadPacket,
    GetLdInfoRequestPacket,
    GetLdAllocationsRequestPacket,
    SetLdAllocationsRequestPacket,
    
    # CciResponsePacket
)
from opencxl.cxl.cci.common import get_opcode_string
from opencxl.util.logger import logger


class MctpCciExecutor(RunnableComponent):
    def __init__(
        self,
        mctp_connection: MctpConnection,
        switch_connection_manager: SwitchConnectionManager,
        port_configs: List[PortConfig],
        available_ld_list: Dict[int, List[List[int | bool]]],
        label: Optional[str] = None,
    ):
        super().__init__(label)
        self._available_ld_list = available_ld_list
        self._message_tag_list = {}
        self._mctp_connection = mctp_connection
        self._cci_executor = CciExecutor(label="MCTP")
        self._switch_connection_manager = switch_connection_manager
        self._downstream_port_connections = []

        for port_index, port_config in enumerate(port_configs):
            if port_config.type == PORT_TYPE.DSP:
                self._downstream_port_connections.append(
                    self._switch_connection_manager.get_cxl_connection(port_index)
                )

    def register_cci_commands(self, commands: List[CciCommand]):
        for command in commands:
            self._cci_executor.register_command(command.get_opcode(), command)

    def _packet_to_request(self, packet: CciMessagePacket) -> CciRequest:
        return CciRequest(opcode=packet.header.command_opcode, payload=packet.get_payload())

    async def _send_response(self, response: CciResponse, message_tag: int):
        header = CciMessageHeaderPacket()
        header.message_category = CCI_MCTP_MESSAGE_CATEGORY.RESPONSE
        header.message_tag = message_tag
        header.command_opcode = 0
        header.set_message_payload_length(len(response.payload))
        header.background_operation = 1 if response.bo_flag else 0
        header.return_code = response.return_code
        header.vendor_specific_extended_status = response.vendor_specific_status
        response_packet = CciMessagePacket.create(header, response.payload)
        # wrap twice

        response_packet_tmc = CciPayloadPacket.create(
            response_packet, response_packet.get_total_size()
        )
        response_packet_tmc2 = CciPayloadPacket.create(
            response_packet_tmc, response_packet_tmc.get_total_size()
        )
        print("send response@!!!!!!!!!!!!1")
        await self._mctp_connection.ep_to_controller.put(response_packet_tmc2)

    async def _process_incoming_requests(self):
        logger.debug(self._create_message("Started processing incoming request"))
        while True:
            # Wait for incoming packets from the MCTP connection
            packet = await self._mctp_connection.controller_to_ep.get()
            if packet == None:
                logger.debug(self._create_message("Stopped processing incoming request"))
                break
            print("unpacking")
            cci_packet_tmc2 = cast(CciPayloadPacket, packet)

            # unpack
            cci_packet_tmc = cci_packet_tmc2.get_packet2()
            # unpack
            cci_packet = cci_packet_tmc.get_packet()
            print("unpacking done")

            opcode = cci_packet.header.command_opcode
            port_index = cci_packet_tmc2.cci_header.port_index

            opcodes_for_ld = [0x5400]
            if opcode in opcodes_for_ld:
                # MLD 로 내리기
                print("MCTP down to ld")
                ld_index = cci_packet_tmc.cci_header.port_index
                message_tag = cci_packet.header.message_tag
                self._message_tag_list[message_tag] = port_index

                downstream_packet = None
                if opcode == 0x5400:
                    downstream_packet = GetLdInfoRequestPacket.create_from_ccimessage(
                        port_index, cci_packet
                    )
                if opcode == 0x5401:
                    downstream_packet = GetLdAllocationsRequestPacket.create_from_ccimessage(
                        ld_index, cci_packet
                    )

                if opcode == 0x5402:
                    downstream_packet = SetLdAllocationsRequestPacket.create_from_ccimessage(
                        ld_index, cci_packet
                    )

                await self._downstream_port_connections[port_index].cci_fifo.host_to_target.put(
                    downstream_packet
                )
            else:
                # Convert packet to CciRequest and send it to CciExecutor
                request = self._packet_to_request(cci_packet)
                response = await self._cci_executor.execute_command(request)
                await self._send_response(response, cci_packet.header.message_tag)

    async def _process_outcoming_responses(self, downstream_connection: CxlConnection):
        logger.debug(self._create_message("Started processing outcoming request"))
        while True:
            # Wait for incoming packets from the MCTP connection
            packet = await downstream_connection.cci_fifo.target_to_host.get()
            if packet == None:
                logger.debug(self._create_message("Stopped processing outcoming request"))
                break

            # set LD table
            opcode = packet.get_command_opcode()
            if opcode == 0x5402:
                logger.info(self._create_message("switch received SetLdAllocationsResponsePacket"))
                number_of_lds = packet.set_ld_allocations_response_payload.number_of_lds
                start_ld_id = packet.set_ld_allocations_response_payload.start_ld_id
                port_index = self._message_tag_list.get(packet.header_data.message_tag, None)
                if port_index is None:
                    print("Error! Invalid message tag")
                    raise ValueError("Invalid message tag")
                for ld_index in range(start_ld_id, start_ld_id + number_of_lds):
                    self._available_ld_list[port_index].append([ld_index, False]) # False means not binded
                 
            self._message_tag_list.pop(packet.header_data.message_tag)
            
            cci_packet = packet.create_ccimessage()
            cci_packet_tmc = CciPayloadPacket.create(cci_packet, cci_packet.get_total_size())
            cci_packet_tmc2 = CciPayloadPacket.create(
                cci_packet_tmc, cci_packet_tmc.get_total_size()
            )

            await self._mctp_connection.ep_to_controller.put(cci_packet_tmc2)

    async def _run(self):
        tasks = [
            create_task(self._process_incoming_requests()),
            create_task(self._cci_executor.run()),
        ]
        for downstream_connection in self._downstream_port_connections:
            tasks.append(create_task(self._process_outcoming_responses(downstream_connection)))
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        # Stop the executor
        await self._mctp_connection.controller_to_ep.put(None)
        for downstream_connection in self._downstream_port_connections:
            await downstream_connection.target_to_host.put(None)
        await self._cci_executor.stop()

    async def get_background_command_status(self) -> CciBackgroundStatus:
        status = await self._cci_executor.get_background_command_status()
        return status

    async def send_notification(self, request: CciRequest):
        header = CciMessageHeaderPacket()
        header.message_category = CCI_MCTP_MESSAGE_CATEGORY.REQUEST
        header.set_message_payload_length(len(request.payload))
        header.command_opcode = request.opcode
        message_packet = CciMessagePacket.create(header, request.payload)
        opcode_str = get_opcode_string(request.opcode)
        message_packet_tmc = CciPayloadPacket.create(
            message_packet, message_packet.get_total_size()
        )
        message_packet_tmc2 = CciPayloadPacket.create(
            message_packet_tmc, message_packet_tmc.get_total_size()
        )
        logger.debug(self._create_message(f"Sending {opcode_str}"))
        await self._mctp_connection.ep_to_controller.put(message_packet_tmc2)

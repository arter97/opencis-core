"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather
from typing import Optional, Union, Callable

from typing import cast
from opencxl.util.logger import logger
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.util.component import RunnableComponent
from opencxl.cxl.transport.transaction import CciRequestPacket

# PacketProcessor can be used a relay between two FifoPairs when it is used as is.
# PacketProcessor can be inherited by another class when customized processing logics are needed.


class MctpPacketProcessor(RunnableComponent):
    def __init__(
        self,
        upstream_fifo: FifoPair,
        downstream_fifo: Optional[FifoPair] = None,
        label: Optional[Union[str, Callable]] = None,
    ):
        super().__init__(label)
        self._upstream_fifo = upstream_fifo
        self._downstream_fifo = downstream_fifo

    async def _process_fm_to_target(self):
        if self._downstream_fifo is None:
            logger.debug(self._create_message("Skipped processing fm to ld packets"))
            return
        logger.debug(self._create_message("Started processing fm to ld packets"))
        while True:
            packet = await self._upstream_fifo.host_to_target.get()
            if packet is None:
                logger.debug(self._create_message("Stopped fm to ld packets"))
                break
            logger.debug(self._create_message("Received fm to ld Packet"))
            packet = cast(CciRequestPacket, packet)
            if packet.get_command_opcode() == 0x5400:  # Get Ld info
                await self._upstream_fifo.target_to_host.put(packet)
            else:
                # Get LD allocations나 Set LD allocations에 해당함. 추후 구현 필요
                # 이거는 FMLD가 Switch로 보내는게 아니라, FMLD가 LD에게 보내는 것
                await self._downstream_fifo.host_to_target.put(packet)

    async def _process_target_to_fm(self):
        # LD가 FMLD에게 Get LD allocations나 Set LD allocations Response를 보냈을 때
        # 추후 구현 필요
        if self._downstream_fifo is None:
            logger.debug(self._create_message("Skipped processing ld to fm packets"))
            return
        logger.debug(self._create_message("Started processing ld to fm packets"))
        while True:
            packet = await self._downstream_fifo.target_to_host.get()
            if packet is None:
                logger.debug(self._create_message("Stopped ld to fm packets"))
                break
            logger.debug(self._create_message("Received ld to fm Packet"))
            await self._upstream_fifo.target_to_host.put(packet)

"""
Copyright (c) 2025, Eeum, Inc.

This software is licensed under the terms of the Revised BSD License.
See LICENSE for details.
"""

import asyncio
import sys

from opencis.util.logger import logger


class ClientConnection:
    _file_lock = asyncio.Lock()

    @staticmethod
    async def open_connection(host=None, port=None, label=None):
        if label is None:
            # Get caller function name
            # pylint: disable=protected-access
            label = sys._getframe(1).f_locals["self"].__class__.__name__
        reader, writer = await asyncio.open_connection(host, port)
        local_port = writer.get_extra_info("sockname")[1]
        logger.info(f"Connected to {host}:{port} as {label} on local port {local_port}")
        # Append to connections.txt
        async with ClientConnection._file_lock:
            with open("connections.txt", "a") as f:
                f.write(f"{label} {local_port}\n")
        return reader, writer

import argparse
import asyncio
import binascii
import json
import betterproto
import serial_asyncio
from typing import Callable, Dict, List, Awaitable, Optional, Tuple
from dtproto_receiver import dri_message_pb2, DriMessage
from raw_logger import RawLogger

class Slip:
    END = 0x0A
    ESC = 0xDB
    ESC_END = 0xDC
    ESC_ESC = 0xDD

    END_b = b"\n"
    ESC_b = b"\xdb"
    ESC_END_b = b"\xdc"
    ESC_ESC_b = b"\xdd"

    @staticmethod
    def encode(data: bytes) -> bytes:
        return (
            data.replace(Slip.ESC_b, Slip.ESC_b + Slip.ESC_ESC_b)
                .replace(Slip.END_b, Slip.ESC_b + Slip.ESC_END_b)
            + Slip.END_b
        )

    @staticmethod
    def decode(data: bytes) -> bytes:
        return (
            data.replace(Slip.ESC_b + Slip.ESC_ESC_b, Slip.ESC_b)
                .replace(Slip.ESC_b + Slip.ESC_END_b, Slip.END_b)
        ).strip(Slip.END_b)


HandlerType = Callable[[bytes], Awaitable[None]]


class SlipSerialReader(asyncio.Protocol):
    def __init__(self, handler_map: Dict[int, List[HandlerType]], raw_logger=None) -> None:
        self.buffer = bytearray()
        self.transport: Optional[asyncio.Transport] = None
        self.handler_map = handler_map
        self.raw_logger = raw_logger   # 可選:序列線上的原始 SLIP 流落地

    def connection_made(self, transport: asyncio.Transport) -> None:
        self.transport = transport
        port = transport.get_extra_info('serial')
        print(f"Connected to {port.port}")

    def data_received(self, data: bytes) -> None:
        # 先保留原料:每段序列原始 bytes(SLIP 流)即時落地,再進 SLIP 解框
        if self.raw_logger is not None:
            self.raw_logger.log(data)
            print(f"[serial raw #{self.raw_logger.count}] {len(data)}B {data.hex()}", flush=True)
        self.buffer.extend(data)
        while Slip.END in self.buffer:
            end_index = self.buffer.index(Slip.END)
            raw_packet = bytes(self.buffer[:end_index + 1])
            self.buffer = self.buffer[end_index + 1:]
            asyncio.create_task(self.process_packet(raw_packet))

    async def process_packet(self, packet: bytes) -> None:
        try:
            decoded = Slip.decode(packet)
            if not decoded:
                return
            address = decoded[0]
            payload = decoded[1:]
            handlers = self.handler_map.get(address, [])
            if handlers:
                await asyncio.gather(*(handler(payload) for handler in handlers))
            else:
                print(f"No handlers registered for address {address}")
        except Exception as e:
            print(f"Error processing packet: {e}")

class SlipDispatcher:
    def __init__(self) -> None:
        self.handler_map: Dict[int, List[HandlerType]] = {}

    def register_handler(self, address: int, handler: HandlerType) -> None:
        if address not in self.handler_map:
            self.handler_map[address] = []

        self.handler_map[address].append(handler)

    async def start(self, port: str, baudrate: int = 115200, raw_logger=None) -> Tuple[serial_asyncio.SerialTransport, SlipSerialReader]:
        loop = asyncio.get_running_loop()
        transport, protocol = await serial_asyncio.create_serial_connection(
            loop,
            lambda: SlipSerialReader(self.handler_map, raw_logger=raw_logger),
            port,
            baudrate
        )
        return transport, protocol

class ProtobufDelimitedBuffer:
    def __init__(self, consumer: Callable[[bytes], Awaitable[None]]):
        self.buffer = bytearray()
        self.consumer = consumer

    async def feed(self, data: bytes):
        self.buffer.extend(data)
        while True:
            msg, remaining = self._extract_next_message(self.buffer)
            if msg is None:
                break  # incomplete message, wait for more data
            self.buffer = remaining
            await self.consumer(msg)
    
    def _read_varint(self, buf: bytearray) -> Tuple[int, int]:
        """Returns (value, length of varint)"""
        result = 0
        shift = 0
        for i, byte in enumerate(buf):
            result |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                return result, i + 1
            shift += 7
        raise ValueError("Incomplete varint")


    def _extract_next_message(self, buf: bytearray) -> Tuple[Optional[bytes], bytearray]:
        try:
            size, size_len = self._read_varint(buf)
            if len(buf) < size_len + size:
                return None, buf  # incomplete payload
            msg_start = size_len
            msg_end = size_len + size
            msg = bytes(buf[msg_start:msg_end])
            return msg, buf[msg_end:]
        except Exception:
            return None, buf  # malformed or incomplete

# When Dronetag Internal parsing libararies are available
try:
    from dt_odid_parser import dt_odid_parser

    async def dt_dri_pb_handler(payload: bytes) -> None:
        print(f"Handled PB message with payload: {binascii.hexlify(payload)}")

        dri_message = dri_message_pb2.DriMessage.FromString(payload)
        if not dri_message.HasField("odid_payload"):
            return
    
        serDict = dt_odid_parser(dri_message)
        if(not serDict):
            print("Message could not been parsed")
        print(json.dumps(serDict, default=str))


except Exception as e:
    print(f"Dronetag parser not available dumping just the protobufs: {e}")
    
    async def dt_dri_pb_handler(payload: bytes) -> None:
        print(f"Handled PB message with payload: {binascii.hexlify(payload)}")

        try:
            dri = DriMessage().parse(payload)
            dri_dict = dri.to_dict(casing=betterproto.Casing.SNAKE)
            print(json.dumps(dri_dict))
        except ValueError as e:
            print("Could not parse dri message %s", e)
            return None
    pass

protobuf_buffer = ProtobufDelimitedBuffer(dt_dri_pb_handler)
    
async def dt_odid_pb_handler(payload: bytes) -> None:
    await protobuf_buffer.feed(payload)

async def handler1(payload: bytes) -> None:
    print(f"[Handler1] Payload: {payload!r}")

async def handler2(payload: bytes) -> None:
    print(f"[Handler2] Payload length: {len(payload)}")

async def main():
    parser = argparse.ArgumentParser(description="Async SLIP Serial Reader with Handler Dispatch")
    parser.add_argument(
        "-p", "--port", required=True, help="Serial port to open (e.g., /dev/ttyUSB0 or COM3)"
    )
    parser.add_argument(
        "-b", "--baudrate", type=int, default=115200, help="Baudrate for the serial port (default: 115200)"
    )
    parser.add_argument("--init", type=str, default="2A0A0A", help="Initial message to send as hex string (e.g., '010203aabb')")
    parser.add_argument("--no-rawlog", action="store_true", help="不要把原始序列 bytes 落地到 log 檔(預設會落地)")

    args = parser.parse_args()

    dispatcher = SlipDispatcher()

    dispatcher.register_handler(0x2A, dt_odid_pb_handler)
    # # Example handlers — you can register as needed
    # dispatcher.register_handler(0x01, handler1)
    # dispatcher.register_handler(0x01, handler2)
    # 原始 SLIP 流落地器(line-buffered,中途 Ctrl+C 也留得住)
    raw_logger = RawLogger("serial", enabled=not args.no_rawlog)
    # Start and get the protocol instance
    transport, _ = await dispatcher.start(args.port, baudrate=args.baudrate, raw_logger=raw_logger)

    if args.init:
        try:
            message = binascii.unhexlify(args.init)
            encoded = Slip.encode(message)
            transport.write(encoded)
            print(f"Sent initial hex message: {message.hex()}")
        except binascii.Error as e:
            print(f"Invalid hex string in --init: {e}")

    # 🔁 Keep running forever to receive incoming messages
    try:
        await asyncio.Event().wait()
    finally:
        raw_logger.close()
            
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped.")


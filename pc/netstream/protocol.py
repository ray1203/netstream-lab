"""프로토콜 정의. 서버랑 PC 클라이언트가 같이 씀 (자세한 건 PROTOCOL.md)

제어 채널(TCP 9000): 길이 4바이트 + JSON
데이터 패킷(TCP 9001 / UDP 9002) 헤더 17바이트:
    stream_id(2) | seq(4) | send_ms(4) | media_ms(4) | flags(1) | payload_len(2)

payload는 TS 패킷 188 * 7 = 1316바이트까지.
헤더랑 UDP/IP 헤더 합쳐도 1361이라 MTU(1500) 안 넘음
"""

from __future__ import annotations

import asyncio
import json
import struct

VERSION = 1

CONTROL_PORT = 9000
TCP_DATA_PORT = 9001
UDP_DATA_PORT = 9002
DASHBOARD_PORT = 9080

TS_PACKET_SIZE = 188
TS_PER_CHUNK = 7
CHUNK_SIZE = TS_PACKET_SIZE * TS_PER_CHUNK  # 1316

DATA_HEADER = struct.Struct("!HIIIBH")
DATA_HEADER_SIZE = DATA_HEADER.size  # 17

FLAG_RTX = 0x01
FLAG_EOS = 0x02

# UDP 주소 등록용 데이터그램 = 매직 + 토큰
UDP_ATTACH_MAGIC = b"NSL1"

MAX_FRAME_SIZE = 1 << 20
_LENGTH = struct.Struct("!I")


def pack_data(stream_id: int, seq: int, send_ms: int, media_ms: int, flags: int, payload: bytes = b"") -> bytes:
    return DATA_HEADER.pack(
        stream_id & 0xFFFF,
        seq & 0xFFFFFFFF,
        int(send_ms) & 0xFFFFFFFF,
        int(media_ms) & 0xFFFFFFFF,
        flags & 0xFF,
        len(payload),
    ) + payload


def unpack_data(packet: bytes) -> tuple[int, int, int, int, int, bytes]:
    if len(packet) < DATA_HEADER_SIZE:
        raise ValueError("데이터 패킷이 헤더보다 짧다")
    stream_id, seq, send_ms, media_ms, flags, length = DATA_HEADER.unpack_from(packet)
    payload = packet[DATA_HEADER_SIZE:DATA_HEADER_SIZE + length]
    if len(payload) != length:
        raise ValueError("payload 길이가 헤더와 다르다")
    return stream_id, seq, send_ms, media_ms, flags, payload


def encode_frame(message: dict) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _LENGTH.pack(len(body)) + body


async def read_frame(reader: asyncio.StreamReader) -> dict | None:
    # 연결 끊기면 None
    try:
        head = await reader.readexactly(_LENGTH.size)
    except asyncio.IncompleteReadError:
        return None
    (length,) = _LENGTH.unpack(head)
    if length > MAX_FRAME_SIZE:
        raise ValueError(f"프레임이 너무 크다: {length}")
    try:
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return None
    message = json.loads(body.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("프레임 내용이 JSON 객체가 아니다")
    return message


async def read_data_packet(reader: asyncio.StreamReader) -> tuple[int, int, int, int, int, bytes] | None:
    try:
        head = await reader.readexactly(DATA_HEADER_SIZE)
        stream_id, seq, send_ms, media_ms, flags, length = DATA_HEADER.unpack(head)
        payload = await reader.readexactly(length) if length else b""
    except asyncio.IncompleteReadError:
        return None
    return stream_id, seq, send_ms, media_ms, flags, payload


def last_pcr_ms(chunk: bytes) -> float | None:
    # 청크 안의 마지막 PCR(ms). PCR은 adaptation field에 있는 90kHz 시계값
    # 전송 속도 맞추는 거랑 버퍼 계산에 씀
    pcr = None
    for off in range(0, len(chunk) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        if chunk[off] != 0x47:  # sync byte
            continue
        has_adaptation = chunk[off + 3] & 0x20
        if has_adaptation and chunk[off + 4] >= 7 and chunk[off + 5] & 0x10:
            b = chunk[off + 6:off + 11]
            base = (b[0] << 25) | (b[1] << 17) | (b[2] << 9) | (b[3] << 1) | (b[4] >> 7)
            pcr = base / 90.0
    return pcr

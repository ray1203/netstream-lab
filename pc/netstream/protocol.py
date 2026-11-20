"""NetStream Lab 프로토콜 정의. 서버와 PC 클라이언트가 함께 쓴다.

채널은 세 가지다.

1. 제어 채널 (TCP 9000)
   프레임 = 길이(4바이트, big-endian) + UTF-8 JSON.
   TCP는 바이트 스트림이라 메시지 경계가 없으므로 길이를 앞에 붙여 경계를 만든다.

2. 데이터 채널, TCP 모드 (TCP 9001)
   첫 프레임만 제어 채널과 같은 형식의 ATTACH 메시지이고,
   그 뒤로는 아래의 데이터 패킷(헤더 17바이트 + payload)이 이어진다.

3. 데이터 채널, UDP 모드 (UDP 9002)
   데이터그램 하나 = 데이터 패킷 하나.

데이터 패킷 헤더 (17바이트, big-endian):

    stream_id(2) | seq(4) | send_ms(4) | media_ms(4) | flags(1) | payload_len(2)

- stream_id : 탐색, 모드 전환, 화질 변경 때마다 바뀐다. 이전 스트림의 늦은 패킷을 버리는 데 쓴다.
- seq       : 스트림마다 0부터 1씩 증가. 손실과 순서 뒤바뀜을 감지한다.
- send_ms   : 서버가 보낸 시각. 지터 계산에 쓴다.
- media_ms  : 이 패킷의 영상 시각 (MPEG-TS PCR 기준). 버퍼 잔량 계산에 쓴다.
- flags     : RTX(재전송), EOS(스트림 끝).

payload는 최대 1316바이트 = MPEG-TS 패킷 188바이트 x 7개.
1316 + 헤더 17 + UDP 8 + IP 20 = 1361바이트로 이더넷 MTU 1500보다 작아서
IP 단편화가 생기지 않는다. 또 항상 TS 패킷 경계에서 자르므로,
UDP 패킷 하나를 잃어도 나머지 스트림의 188바이트 정렬은 깨지지 않는다.
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

# UDP 모드에서 클라이언트가 서버에 자기 주소를 알리는 데이터그램: 매직 4바이트 + 토큰(ASCII)
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
    """(stream_id, seq, send_ms, media_ms, flags, payload)를 돌려준다."""
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
    """프레임 하나를 읽는다. 상대가 연결을 닫았으면 None."""
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
    """TCP 데이터 채널에서 데이터 패킷 하나를 읽는다. 연결이 닫혔으면 None."""
    try:
        head = await reader.readexactly(DATA_HEADER_SIZE)
        stream_id, seq, send_ms, media_ms, flags, length = DATA_HEADER.unpack(head)
        payload = await reader.readexactly(length) if length else b""
    except asyncio.IncompleteReadError:
        return None
    return stream_id, seq, send_ms, media_ms, flags, payload


def last_pcr_ms(chunk: bytes) -> float | None:
    """TS 청크 안에서 마지막 PCR(Program Clock Reference)을 밀리초로 돌려준다.

    PCR은 TS 패킷의 adaptation field에 들어 있는 90kHz 기준 시계 값이다.
    서버는 이 값으로 "지금 보내는 데이터가 영상의 몇 초 지점인지"를 알아내
    전송 속도를 조절하고, 클라이언트는 버퍼에 몇 초 분량이 남았는지 계산한다.
    """
    pcr = None
    for off in range(0, len(chunk) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        if chunk[off] != 0x47:  # 동기 바이트
            continue
        has_adaptation = chunk[off + 3] & 0x20
        if has_adaptation and chunk[off + 4] >= 7 and chunk[off + 5] & 0x10:
            b = chunk[off + 6:off + 11]
            base = (b[0] << 25) | (b[1] << 17) | (b[2] << 9) | (b[3] << 1) | (b[4] >> 7)
            pcr = base / 90.0
    return pcr

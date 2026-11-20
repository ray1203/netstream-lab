package com.netstreamlab.app.net

import org.json.JSONObject
import java.io.DataInputStream
import java.io.EOFException
import java.io.IOException
import java.io.OutputStream
import java.nio.ByteBuffer

// pc/netstream/protocol.py 랑 같은 내용
// 헤더(17): stream_id(2) | seq(4) | send_ms(4) | media_ms(4) | flags(1) | payload_len(2)
object Protocol {
    const val VERSION = 1
    const val CONTROL_PORT = 9000
    const val HEADER_SIZE = 17
    const val FLAG_RTX = 0x01
    const val FLAG_EOS = 0x02
    const val MAX_FRAME_SIZE = 1 shl 20
    const val MAX_DATAGRAM = 2048

    val UDP_ATTACH_MAGIC: ByteArray = "NSL1".toByteArray(Charsets.US_ASCII)

    fun writeFrame(out: OutputStream, message: JSONObject) {
        val body = message.toString().toByteArray(Charsets.UTF_8)
        val frame = ByteBuffer.allocate(4 + body.size).putInt(body.size).put(body).array()
        out.write(frame)
        out.flush()
    }

    // 연결 끊기면 null
    fun readFrame(input: DataInputStream): JSONObject? {
        val length = try {
            input.readInt()
        } catch (e: EOFException) {
            return null
        }
        if (length < 0 || length > MAX_FRAME_SIZE) throw IOException("프레임 길이가 이상합니다: $length")
        val body = ByteArray(length)
        input.readFully(body)
        return JSONObject(String(body, Charsets.UTF_8))
    }
}

class DataPacket(
    val streamId: Int,
    val seq: Long,
    val sendMs: Long,
    val mediaMs: Long,
    val flags: Int,
    val payload: ByteArray,
) {
    val isRetransmit: Boolean get() = flags and Protocol.FLAG_RTX != 0
    val isEndOfStream: Boolean get() = flags and Protocol.FLAG_EOS != 0

    companion object {
        // UDP 데이터그램 파싱. 이상하면 null
        fun parse(buffer: ByteArray, length: Int): DataPacket? {
            if (length < Protocol.HEADER_SIZE) return null
            val header = ByteBuffer.wrap(buffer, 0, Protocol.HEADER_SIZE)
            val streamId = header.short.toInt() and 0xFFFF
            val seq = header.int.toLong() and 0xFFFFFFFFL
            val sendMs = header.int.toLong() and 0xFFFFFFFFL
            val mediaMs = header.int.toLong() and 0xFFFFFFFFL
            val flags = header.get().toInt() and 0xFF
            val payloadLength = header.short.toInt() and 0xFFFF
            if (Protocol.HEADER_SIZE + payloadLength > length) return null
            val payload = buffer.copyOfRange(Protocol.HEADER_SIZE, Protocol.HEADER_SIZE + payloadLength)
            return DataPacket(streamId, seq, sendMs, mediaMs, flags, payload)
        }

        // TCP는 스트림이라 헤더의 길이만큼 읽어야 함
        fun read(input: DataInputStream): DataPacket {
            val head = ByteArray(Protocol.HEADER_SIZE)
            input.readFully(head)
            val header = ByteBuffer.wrap(head)
            val streamId = header.short.toInt() and 0xFFFF
            val seq = header.int.toLong() and 0xFFFFFFFFL
            val sendMs = header.int.toLong() and 0xFFFFFFFFL
            val mediaMs = header.int.toLong() and 0xFFFFFFFFL
            val flags = header.get().toInt() and 0xFF
            val payload = ByteArray(header.short.toInt() and 0xFFFF)
            input.readFully(payload)
            return DataPacket(streamId, seq, sendMs, mediaMs, flags, payload)
        }
    }
}

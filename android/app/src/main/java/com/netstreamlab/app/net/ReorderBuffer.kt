package com.netstreamlab.app.net

import java.util.TreeMap
import kotlin.math.abs
import kotlin.math.max

// UDP 패킷 순서 맞추기 + 손실 감지 + NACK 대상 고르기
// PC쪽 receiver.py 랑 같은 로직. 스레드 안전 X (StreamClient에서 lock 잡고 호출), 시간 단위는 초
class ReorderBuffer(private val deliver: (DataPacket) -> Unit) {

    private class Missing(val detected: Double) {
        var nacks = 0
        var lastNack = 0.0
    }

    var nextSeq = 0L  // 다음에 내보낼 번호
        private set
    private var highest = -1L
    private var total: Long? = null
    private val buffer = HashMap<Long, DataPacket>()
    private val missing = TreeMap<Long, Missing>()

    var delivered = 0L; private set
    var lost = 0L; private set
    var recovered = 0L; private set
    var reordered = 0L; private set
    var duplicates = 0L; private set
    var nacksSent = 0L; private set

    val finished: Boolean get() = total?.let { nextSeq >= it } ?: false

    fun onPacket(packet: DataPacket, now: Double) {
        val seq = packet.seq
        if (seq < nextSeq || buffer.containsKey(seq)) {
            duplicates++  // 중복이거나 포기한 뒤에 온 것
            return
        }
        if (missing.containsKey(seq)) {
            // RTX로 왔으면 복구, 아니면 순서만 바뀐 것
            if (packet.isRetransmit) recovered++ else reordered++
            missing.remove(seq)
        } else if (seq > highest) {
            markMissing(highest + 1, seq, now)
            highest = seq
        }
        if (packet.isEndOfStream) total = seq + 1
        buffer[seq] = packet
        flush()
    }

    // 제어 채널 EOS로 총 개수를 알게 됨. 끝에서 잃은 것도 빈자리 처리
    fun markEnd(totalPackets: Long, now: Double) {
        total = totalPackets
        if (totalPackets - 1 > highest) {
            markMissing(highest + 1, totalPackets, now)
            highest = totalPackets - 1
        }
    }

    private fun markMissing(start: Long, end: Long, now: Double) {
        if (end - start > MAX_GAP) {
            // 너무 크게 비면 그냥 건너뜀
            lost += end - start
            for (held in buffer.keys.sorted()) {
                delivered++
                deliver(buffer.remove(held)!!)
            }
            lost += missing.size
            missing.clear()
            nextSeq = end
            return
        }
        for (seq in start until end) missing[seq] = Missing(now)
    }

    private fun flush() {
        while (true) {
            val packet = buffer.remove(nextSeq) ?: break
            delivered++
            deliver(packet)
            nextSeq++
        }
    }

    // 10ms마다 호출. 오래된 빈자리는 포기, NACK 보낼 번호 리턴
    fun poll(now: Double, nackEnabled: Boolean, rtt: Double): List<Long> {
        val wait = if (nackEnabled) max(NACK_WAIT, 5 * rtt) else REORDER_WAIT
        while (true) {
            val head = missing[nextSeq] ?: break
            if (now - head.detected < wait) break
            missing.remove(nextSeq)
            lost++
            nextSeq++
            flush()
        }
        if (!nackEnabled) return emptyList()
        val interval = max(NACK_MIN_INTERVAL, 2 * rtt)
        val request = ArrayList<Long>()
        for ((seq, state) in missing) {
            if (now - state.detected < NACK_GRACE || state.nacks >= MAX_NACKS_PER_PACKET || now - state.lastNack < interval) continue
            state.nacks++
            state.lastNack = now
            request.add(seq)
            if (request.size >= MAX_NACK_BATCH) break
        }
        nacksSent += request.size
        return request
    }

    companion object {
        const val REORDER_WAIT = 0.06   // NACK 꺼져 있을 때 대기
        const val NACK_WAIT = 0.6       // NACK 켜져 있을 때
        const val NACK_GRACE = 0.02     // 바로 안 보내고 조금 기다림
        const val NACK_MIN_INTERVAL = 0.08
        const val MAX_NACKS_PER_PACKET = 4
        const val MAX_NACK_BATCH = 100
        const val MAX_GAP = 3000L
    }
}

// RFC 3550 지터: J += (|D| - J) / 16
class JitterMeter {
    var jitterMs = 0.0
        private set
    private var lastTransit: Double? = null

    fun onPacket(sendMs: Long, arrivalMs: Double) {
        val transit = arrivalMs - sendMs
        lastTransit?.let { jitterMs += (abs(transit - it) - jitterMs) / 16.0 }
        lastTransit = transit
    }
}

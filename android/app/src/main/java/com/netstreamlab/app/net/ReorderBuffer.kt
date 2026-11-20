package com.netstreamlab.app.net

import java.util.TreeMap
import kotlin.math.abs
import kotlin.math.max

/**
 * UDP로 받은 패킷을 seq 순서대로 내보낸다. 손실을 감지하고 NACK로 요청할 번호를 고른다.
 * TCP가 커널 안에서 해 주는 일의 일부를 직접 구현한 것이다.
 * PC 쪽 pc/netstream/client/receiver.py의 ReorderBuffer와 같은 알고리즘이다.
 *
 * 스레드 안전하지 않다. 부르는 쪽(StreamClient)이 잠금을 잡는다. 시각은 초 단위.
 */
class ReorderBuffer(private val deliver: (DataPacket) -> Unit) {

    private class Missing(val detected: Double) {
        var nacks = 0
        var lastNack = 0.0
    }

    var nextSeq = 0L          // 다음에 내보낼 번호
        private set
    private var highest = -1L // 지금까지 본 가장 큰 번호
    private var total: Long? = null
    private val buffer = HashMap<Long, DataPacket>()
    private val missing = TreeMap<Long, Missing>()

    // 누적 통계
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
            duplicates++  // 이미 받았거나, 포기한 뒤에 도착했다
            return
        }
        if (missing.containsKey(seq)) {
            // 빈자리가 채워졌다: 재전송으로 왔으면 복구, 원래 패킷이 늦게 왔으면 순서 뒤바뀜
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

    /** 제어 채널의 EOS로 총 패킷 수를 알게 됐다. 끝부분에서 잃은 패킷도 빈자리로 잡는다. */
    fun markEnd(totalPackets: Long, now: Double) {
        total = totalPackets
        if (totalPackets - 1 > highest) {
            markMissing(highest + 1, totalPackets, now)
            highest = totalPackets - 1
        }
    }

    private fun markMissing(start: Long, end: Long, now: Double) {
        if (end - start > MAX_GAP) {
            // 구멍이 너무 크면 복구를 포기하고 건너뛴다
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

    /** 10ms마다 부른다. 너무 오래 기다린 빈자리는 포기하고, NACK로 요청할 번호를 돌려준다. */
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
        const val REORDER_WAIT = 0.06   // NACK를 쓰지 않을 때: 늦게 오는 패킷을 이만큼만 기다린다
        const val NACK_WAIT = 0.6       // NACK를 쓸 때: 재전송이 올 때까지 기다리는 최대 시간
        const val NACK_GRACE = 0.02     // 빈자리를 발견하고 이만큼 지나야 NACK를 보낸다
        const val NACK_MIN_INTERVAL = 0.08
        const val MAX_NACKS_PER_PACKET = 4
        const val MAX_NACK_BATCH = 100
        const val MAX_GAP = 3000L
    }
}

/**
 * 도착 간격 지터 (RFC 3550, RTP와 같은 방식).
 * transit = 도착 시각 - 송신 시각. 두 기기의 시계 기준이 달라도 transit의 "변화량"은 의미가 있다.
 * J = J + (|D| - J) / 16
 */
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

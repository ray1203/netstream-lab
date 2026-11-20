package com.netstreamlab.app.net

import com.netstreamlab.app.player.TsPipe
import org.json.JSONArray
import org.json.JSONObject
import java.io.DataInputStream
import java.io.IOException
import java.io.OutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

data class VideoInfo(val id: Int, val name: String, val durationSec: Double?, val height: Int?)

data class PlayResult(val pipe: TsPipe, val quality: String, val position: Double, val durationSec: Double?)

/** 1초 동안의 수신 통계. 화면에 보여 주고 서버에도 보낸다. */
data class Report(
    val mode: String = "",
    val kbps: Int = 0,
    val lossRate: Double = 0.0,      // 최종 손실 (NACK 복구 후)
    val rawLossRate: Double = 0.0,   // 재전송 전 손실
    val lost: Long = 0,
    val recovered: Long = 0,
    val reordered: Long = 0,
    val jitterMs: Double = 0.0,
    val rttMs: Double? = null,
    val bufferMs: Long = 0,
)

/**
 * 서버와의 모든 통신을 맡는다.
 *
 * - 제어 채널: TCP. 요청과 응답은 id로 짝을 맞춘다.
 * - 데이터 채널: TCP 모드는 별도 TCP 연결, UDP 모드는 DatagramSocket.
 * - 받은 MPEG-TS는 TsPipe에 넣고, 플레이어가 거기서 꺼내 재생한다.
 *
 * 소켓 호출은 모두 블로킹이라서 메인 스레드에서 부르면 안 된다 (ViewModel이 별도 스레드에서 부른다).
 */
class StreamClient(private val host: String, private val port: Int, private val listener: Listener) {

    interface Listener {
        fun onOptions(nack: Boolean, qualitySetting: String, currentQuality: String?)
        fun onSwitchModeRequested(mode: String)
        fun onStreamChanged(pipe: TsPipe, quality: String, position: Double)
        fun onServerError(message: String)
        fun onDisconnected()
        fun onReport(report: Report)
    }

    // 플레이어 상태. 메인 스레드(ViewModel)가 주기적으로 채워 넣고, 보고할 때 읽는다.
    @Volatile var playerBufferMs = 0L
    @Volatile var playerPositionSec = 0.0
    @Volatile var playerStalled = false

    var serverName = ""; private set
    var qualities: List<String> = emptyList(); private set
    @Volatile var nack = false; private set
    @Volatile var qualitySetting = "720p"; private set

    private var control: Socket? = null
    private var controlOut: OutputStream? = null
    private val sendLock = Any()
    private val requestIds = AtomicInteger(1)
    private val pending = ConcurrentHashMap<Int, LinkedBlockingQueue<JSONObject>>()
    private var scheduler: ScheduledExecutorService? = null
    @Volatile private var closed = false

    private var token = ""
    private var tcpDataPort = 0
    private var udpDataPort = 0
    private var tcpData: Socket? = null
    private var udp: DatagramSocket? = null
    @Volatile private var udpAttached = CountDownLatch(1)
    @Volatile private var rttMs: Double? = null

    // 현재 스트림의 상태. lock으로 보호한다 (수신 스레드, 타이머 스레드, 제어 스레드가 함께 쓴다).
    private val lock = Any()
    private var streamId = -1
    private var mode = "tcp"
    private var pipe: TsPipe? = null
    private var reorder = ReorderBuffer(::deliver)
    private var jitter = JitterMeter()
    private var ended = false
    private var bytes = 0L
    private var tcpDelivered = 0L
    private var snapshot = LongArray(5)

    private fun now(): Double = System.nanoTime() / 1e9

    // ----- 연결 -----

    @Throws(IOException::class)
    fun connect() {
        val socket = Socket()
        socket.tcpNoDelay = true
        socket.connect(InetSocketAddress(host, port), 5000)
        control = socket
        controlOut = socket.getOutputStream()
        thread(name = "control-reader", isDaemon = true) { controlLoop(DataInputStream(socket.getInputStream().buffered())) }

        val hello = request(
            JSONObject().put("type", "HELLO").put("client", "Android ${android.os.Build.MODEL}")
                .put("version", Protocol.VERSION)
                // 화질이 바뀌면 플레이어 버퍼를 비우고 새로 시작한다는 뜻. 서버는 재생 위치부터 다시 보낸다.
                .put("flush_on_change", true)
        )
        serverName = hello.optString("server")
        token = hello.getString("token")
        tcpDataPort = hello.getInt("tcp_data_port")
        udpDataPort = hello.getInt("udp_data_port")
        nack = hello.optBoolean("nack")
        qualitySetting = hello.optString("quality", "720p")
        val list = hello.optJSONArray("qualities") ?: JSONArray()
        qualities = (0 until list.length()).map { list.getJSONObject(it).getString("name") }

        val timer = Executors.newSingleThreadScheduledExecutor { Thread(it, "client-timer").apply { isDaemon = true } }
        scheduler = timer
        timer.scheduleAtFixedRate(guard(::ping), 0, 1, TimeUnit.SECONDS)
        timer.scheduleAtFixedRate(guard(::report), 1, 1, TimeUnit.SECONDS)
        timer.scheduleAtFixedRate(guard(::poll), 10, 10, TimeUnit.MILLISECONDS)
        timer.scheduleAtFixedRate(guard(::udpKeepAlive), 5, 5, TimeUnit.SECONDS)
    }

    /** 타이머 작업에서 예외가 나면 그 뒤로 실행이 멈추므로 막아 둔다. */
    private fun guard(block: () -> Unit) = Runnable {
        try {
            block()
        } catch (e: Exception) {
            // 연결이 끊어지면 controlLoop가 알린다
        }
    }

    fun close() {
        if (closed) return
        closed = true
        scheduler?.shutdownNow()
        synchronized(lock) {
            streamId = -1
            pipe?.close()
        }
        // 소켓을 닫으면 블로킹 중인 수신 스레드들이 예외를 받고 끝난다
        runCatching { control?.close() }
        runCatching { tcpData?.close() }
        runCatching { udp?.close() }
    }

    private fun send(message: JSONObject) {
        val out = controlOut ?: throw IOException("연결되지 않았습니다")
        synchronized(sendLock) { Protocol.writeFrame(out, message) }
    }

    /** 요청을 보내고 같은 id의 응답을 기다린다. */
    @Throws(IOException::class)
    private fun request(message: JSONObject): JSONObject {
        val id = requestIds.getAndIncrement()
        val slot = LinkedBlockingQueue<JSONObject>(1)
        pending[id] = slot
        try {
            send(message.put("id", id))
            val reply = slot.poll(15, TimeUnit.SECONDS) ?: throw IOException("서버가 응답하지 않습니다")
            if (reply.optString("type") == "ERROR") throw IOException(reply.optString("message", "서버 오류"))
            return reply
        } catch (e: InterruptedException) {
            throw IOException("요청이 취소되었습니다")
        } finally {
            pending.remove(id)
        }
    }

    private fun controlLoop(input: DataInputStream) {
        try {
            while (true) {
                val message = Protocol.readFrame(input) ?: break
                val slot = if (message.has("id")) pending[message.optInt("id")] else null
                if (slot != null) slot.offer(message) else onControl(message)
            }
        } catch (e: Exception) {
            // 아래에서 끊어짐으로 처리
        }
        for (slot in pending.values) slot.offer(JSONObject().put("type", "ERROR").put("message", "서버와의 연결이 끊어졌습니다"))
        if (!closed) listener.onDisconnected()
    }

    private fun onControl(m: JSONObject) {
        when (m.optString("type")) {
            "PONG" -> {
                val sample = (System.nanoTime() - m.optLong("t")) / 1e6
                rttMs = rttMs?.let { it * 0.7 + sample * 0.3 } ?: sample
            }
            "UDP_ATTACHED" -> udpAttached.countDown()
            "OPTIONS" -> {
                nack = m.optBoolean("nack")
                qualitySetting = m.optString("quality", qualitySetting)
                val current = if (m.isNull("current_quality")) null else m.optString("current_quality")
                listener.onOptions(nack, qualitySetting, current)
            }
            "STREAM_CHANGE" -> {
                // 서버가 화질을 바꿨다: 새 stream_id의 데이터를 새 통로로 받는다
                val newPipe = beginStream(m.getInt("stream_id"), synchronized(lock) { mode })
                listener.onStreamChanged(newPipe, m.optString("quality"), m.optDouble("position", 0.0))
            }
            "SWITCH_MODE" -> listener.onSwitchModeRequested(m.optString("mode"))
            "EOS" -> synchronized(lock) {
                if (m.optInt("stream_id") == streamId && mode == "udp") reorder.markEnd(m.optLong("packets"), now())
            }
            "ERROR" -> listener.onServerError(m.optString("message", "서버 오류"))
        }
    }

    // ----- 데이터 채널 -----

    @Throws(IOException::class)
    private fun ensureTcpData() {
        tcpData?.let { if (!it.isClosed && it.isConnected) return }
        val socket = Socket()
        socket.tcpNoDelay = true
        socket.receiveBufferSize = 1 shl 20
        socket.connect(InetSocketAddress(host, tcpDataPort), 5000)
        Protocol.writeFrame(socket.getOutputStream(), JSONObject().put("type", "ATTACH").put("token", token))
        val input = DataInputStream(socket.getInputStream().buffered(64 * 1024))
        val reply = Protocol.readFrame(input)
        if (reply?.optString("type") != "ATTACH_OK") {
            socket.close()
            throw IOException("TCP 데이터 채널을 열지 못했습니다")
        }
        tcpData = socket
        thread(name = "tcp-data", isDaemon = true) {
            try {
                while (true) {
                    val packet = DataPacket.read(input)
                    onData(packet, Protocol.HEADER_SIZE + packet.payload.size)
                }
            } catch (e: Exception) {
                runCatching { socket.close() }
            }
        }
    }

    @Throws(IOException::class)
    private fun ensureUdp() {
        var socket = udp
        if (socket == null || socket.isClosed) {
            socket = DatagramSocket()
            // 처음 3초 분량이 한꺼번에 오므로 수신 버퍼를 넉넉히 잡는다. 작으면 커널이 버려서 가짜 손실이 생긴다.
            socket.receiveBufferSize = 4 shl 20
            socket.connect(InetAddress.getByName(host), udpDataPort)
            udp = socket
            udpAttached = CountDownLatch(1)
            val receiving = socket
            thread(name = "udp-data", isDaemon = true) {
                val buffer = ByteArray(Protocol.MAX_DATAGRAM)
                val datagram = DatagramPacket(buffer, buffer.size)
                try {
                    while (true) {
                        datagram.length = buffer.size
                        receiving.receive(datagram)
                        val packet = DataPacket.parse(buffer, datagram.length) ?: continue
                        onData(packet, datagram.length)
                    }
                } catch (e: Exception) {
                    // 소켓이 닫혔다
                }
            }
        }
        // 서버는 이 데이터그램의 출발지 주소로 영상을 보낸다. UDP라서 사라질 수 있으니 확인될 때까지 반복한다.
        val latch = udpAttached
        repeat(25) {
            sendUdpAttach()
            if (latch.await(200, TimeUnit.MILLISECONDS)) return
        }
        throw IOException("UDP 주소를 등록하지 못했습니다 (PC 방화벽에서 UDP $udpDataPort 포트를 확인하세요)")
    }

    private fun sendUdpAttach() {
        val socket = udp ?: return
        val data = Protocol.UDP_ATTACH_MAGIC + token.toByteArray(Charsets.US_ASCII)
        socket.send(DatagramPacket(data, data.size))
    }

    private fun udpKeepAlive() {
        if (udp != null && udpAttached.count == 0L) sendUdpAttach()
    }

    private fun beginStream(id: Int, newMode: String): TsPipe = synchronized(lock) {
        pipe?.close()
        val newPipe = TsPipe()
        pipe = newPipe
        mode = newMode
        reorder = ReorderBuffer(::deliver)
        jitter = JitterMeter()
        ended = false
        bytes = 0
        tcpDelivered = 0
        snapshot = LongArray(5)
        streamId = id
        newPipe
    }

    private fun onData(packet: DataPacket, size: Int) {
        synchronized(lock) {
            if (packet.streamId != streamId) return  // 이전 스트림의 늦은 패킷
            bytes += size
            if (!packet.isRetransmit) jitter.onPacket(packet.sendMs, System.nanoTime() / 1e6)
            if (mode == "udp") {
                reorder.onPacket(packet, now())
            } else {
                // TCP는 커널이 순서와 재전송을 이미 처리했다. 받은 그대로 넘긴다.
                tcpDelivered++
                deliver(packet)
            }
        }
    }

    /** lock을 잡은 상태에서 불린다. */
    private fun deliver(packet: DataPacket) {
        if (packet.isEndOfStream) finishStream() else pipe?.write(packet.payload)
    }

    private fun finishStream() {
        if (!ended) {
            ended = true
            pipe?.finish()
        }
    }

    private fun poll() {
        var request: List<Long> = emptyList()
        var id = -1
        synchronized(lock) {
            if (streamId < 0 || mode != "udp") return
            request = reorder.poll(now(), nack, (rttMs ?: 20.0) / 1000.0)
            if (reorder.finished) finishStream()
            id = streamId
        }
        if (request.isNotEmpty()) {
            // 잃어버린 패킷 번호를 TCP 제어 채널로 알린다. 요청 자체는 사라지면 안 되므로 TCP가 맞다.
            send(JSONObject().put("type", "NACK").put("stream_id", id).put("seqs", JSONArray(request)))
        }
    }

    private fun ping() {
        send(JSONObject().put("type", "PING").put("t", System.nanoTime()))
    }

    private fun report() {
        val (message, result) = synchronized(lock) {
            if (streamId < 0) return
            val udpMode = mode == "udp"
            val current = longArrayOf(
                bytes,
                if (udpMode) reorder.delivered else tcpDelivered,
                if (udpMode) reorder.lost else 0,
                if (udpMode) reorder.recovered else 0,
                if (udpMode) reorder.reordered else 0,
            )
            val delta = LongArray(5) { current[it] - snapshot[it] }
            snapshot = current
            val expected = delta[1] + delta[2]
            val result = Report(
                mode = mode,
                kbps = (delta[0] * 8 / 1000).toInt(),
                lossRate = if (expected > 0) delta[2].toDouble() / expected else 0.0,
                rawLossRate = if (expected > 0) (delta[2] + delta[3]).toDouble() / expected else 0.0,
                lost = delta[2],
                recovered = delta[3],
                reordered = delta[4],
                jitterMs = jitter.jitterMs,
                rttMs = rttMs,
                bufferMs = playerBufferMs,
            )
            val message = JSONObject()
                .put("type", "REPORT").put("stream_id", streamId).put("mode", mode)
                .put("kbps", result.kbps).put("packets", delta[1])
                .put("lost", result.lost).put("recovered", result.recovered).put("reordered", result.reordered)
                .put("loss_rate", result.lossRate).put("raw_loss_rate", result.rawLossRate)
                .put("jitter_ms", Math.round(result.jitterMs * 100) / 100.0)
                .put("rtt_ms", result.rttMs?.let { Math.round(it * 10) / 10.0 } ?: JSONObject.NULL)
                .put("buffer_ms", result.bufferMs)
                .put("stalled", playerStalled && !ended)
                .put("position", playerPositionSec)
            Pair(message, result)
        }
        send(message)
        listener.onReport(result)
    }

    // ----- 명령 (블로킹. 메인 스레드에서 부르지 말 것) -----

    @Throws(IOException::class)
    fun listVideos(): List<VideoInfo> {
        val videos = request(JSONObject().put("type", "LIST")).getJSONArray("videos")
        return (0 until videos.length()).map {
            val v = videos.getJSONObject(it)
            VideoInfo(
                id = v.getInt("id"),
                name = v.getString("name"),
                durationSec = if (v.isNull("duration")) null else v.getDouble("duration"),
                height = if (v.isNull("height")) null else v.getInt("height"),
            )
        }
    }

    /** 재생, 탐색, 전송 모드 전환 모두 이 요청 하나다. 새 스트림을 받을 통로를 돌려준다. */
    @Throws(IOException::class)
    fun play(videoId: Int, newMode: String, position: Double): PlayResult {
        if (newMode == "tcp") ensureTcpData() else ensureUdp()
        synchronized(lock) {
            streamId = -1
            pipe?.close()
        }
        val reply = request(
            JSONObject().put("type", "PLAY").put("video_id", videoId).put("mode", newMode).put("position", position)
        )
        val newPipe = beginStream(reply.getInt("stream_id"), newMode)
        return PlayResult(
            pipe = newPipe,
            quality = reply.optString("quality"),
            position = reply.optDouble("position", 0.0),
            durationSec = if (reply.isNull("duration")) null else reply.getDouble("duration"),
        )
    }

    @Throws(IOException::class)
    fun stop() {
        synchronized(lock) {
            streamId = -1
            pipe?.close()
        }
        request(JSONObject().put("type", "STOP"))
    }

    @Throws(IOException::class)
    fun setNack(enabled: Boolean) {
        request(JSONObject().put("type", "SET_OPTIONS").put("nack", enabled))
    }

    @Throws(IOException::class)
    fun setQuality(setting: String) {
        request(JSONObject().put("type", "SET_OPTIONS").put("quality", setting))
    }
}

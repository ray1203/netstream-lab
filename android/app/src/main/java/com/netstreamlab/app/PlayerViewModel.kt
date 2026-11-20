package com.netstreamlab.app

import android.app.Application
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.annotation.OptIn
import androidx.lifecycle.AndroidViewModel
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSource
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import androidx.media3.extractor.DefaultExtractorsFactory
import com.netstreamlab.app.net.Protocol
import com.netstreamlab.app.net.Report
import com.netstreamlab.app.net.StreamClient
import com.netstreamlab.app.net.VideoInfo
import com.netstreamlab.app.player.PipeDataSource
import com.netstreamlab.app.player.TsPipe
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import java.util.concurrent.Executors

enum class Screen { Connect, Videos, Player }

data class UiState(
    val screen: Screen = Screen.Connect,
    val host: String = "",
    val busy: Boolean = false,
    val error: String? = null,
    val serverName: String = "",
    val videos: List<VideoInfo> = emptyList(),
    val qualities: List<String> = emptyList(),
    val current: VideoInfo? = null,
    val mode: String = "tcp",
    val nack: Boolean = false,
    val qualitySetting: String = "720p",
    val quality: String? = null,
    val positionSec: Float = 0f,
    val durationSec: Float = 0f,
    val buffering: Boolean = false,
    val ended: Boolean = false,
    val report: Report = Report(),
)

// 네트워크 호출은 io 스레드 하나에서 순서대로, ExoPlayer는 메인 스레드에서만
@OptIn(UnstableApi::class)
class PlayerViewModel(app: Application) : AndroidViewModel(app), StreamClient.Listener {

    private val prefs = app.getSharedPreferences("netstream", Context.MODE_PRIVATE)
    private val _state = MutableStateFlow(UiState(host = prefs.getString("host", "") ?: ""))
    val state: StateFlow<UiState> = _state

    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private var client: StreamClient? = null

    // 서버가 3초 앞서 보내주니까 버퍼는 작게. 0.5초 쌓이면 재생 시작
    val player: ExoPlayer = ExoPlayer.Builder(app)
        .setLoadControl(
            DefaultLoadControl.Builder()
                .setBufferDurationsMs(2_000, 10_000, 500, 1_000)
                .build()
        )
        .build()

    private var pipe: TsPipe? = null
    private var basePosition = 0.0        // 스트림 시작 위치(초)
    private var playerOrigin: Long? = null // 첫 READY 때 player 위치(ms)
    private var started = false
    private var lastErrorRestart = 0L

    private val ticker = object : Runnable {
        override fun run() {
            tick()
            main.postDelayed(this, 300)
        }
    }

    init {
        player.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                if (playbackState == Player.STATE_READY && playerOrigin == null) {
                    playerOrigin = player.currentPosition
                    started = true
                }
            }

            override fun onPlayerError(error: PlaybackException) {
                // UDP 손실로 디코더가 죽는 경우가 있어서 보던 위치부터 다시 요청
                val video = _state.value.current ?: return
                val now = SystemClock.elapsedRealtime()
                if (now - lastErrorRestart < 3_000) {
                    _state.update { it.copy(error = "재생 오류: ${error.errorCodeName}") }
                    return
                }
                lastErrorRestart = now
                requestPlay(video.id, _state.value.mode, position())
            }
        })
        main.post(ticker)
    }

    private fun position(): Double {
        val origin = playerOrigin ?: return basePosition
        return basePosition + (player.currentPosition - origin).coerceAtLeast(0) / 1000.0
    }

    private fun tick() {
        val c = client ?: return
        val buffering = started && player.playbackState == Player.STATE_BUFFERING
        val ended = player.playbackState == Player.STATE_ENDED && pipe?.closed == false
        c.playerBufferMs = player.totalBufferedDuration
        c.playerPositionSec = position()
        c.playerStalled = buffering
        if (_state.value.screen == Screen.Player) {
            _state.update { it.copy(positionSec = position().toFloat(), buffering = buffering, ended = ended) }
        }
    }


    fun setHost(host: String) = _state.update { it.copy(host = host, error = null) }

    fun connect() {
        val input = _state.value.host.trim()
        if (input.isEmpty()) {
            _state.update { it.copy(error = "PC의 IP 주소를 입력하세요 (서버를 실행하면 화면에 나옵니다)") }
            return
        }
        val host = input.substringBefore(':')
        val port = input.substringAfter(':', "").toIntOrNull() ?: Protocol.CONTROL_PORT
        prefs.edit().putString("host", input).apply()
        _state.update { it.copy(busy = true, error = null) }
        io.execute {
            val c = StreamClient(host, port, this)
            try {
                c.connect()
                val videos = c.listVideos()
                main.post {
                    client = c
                    _state.update {
                        it.copy(
                            screen = Screen.Videos, busy = false, serverName = c.serverName, videos = videos,
                            qualities = c.qualities, nack = c.nack, qualitySetting = c.qualitySetting,
                        )
                    }
                }
            } catch (e: Exception) {
                c.close()
                main.post { _state.update { it.copy(busy = false, error = "연결 실패: ${e.message ?: e.javaClass.simpleName}") } }
            }
        }
    }

    fun openVideo(video: VideoInfo) {
        _state.update {
            it.copy(
                screen = Screen.Player, current = video, positionSec = 0f, ended = false, error = null,
                durationSec = (video.durationSec ?: 0.0).toFloat(), report = Report(),
            )
        }
        requestPlay(video.id, _state.value.mode, 0.0)
    }

    fun setMode(mode: String) {
        val state = _state.value
        if (mode == state.mode || (mode != "tcp" && mode != "udp")) return
        _state.update { it.copy(mode = mode) }
        state.current?.let { requestPlay(it.id, mode, position()) }
    }

    fun seekTo(seconds: Double) {
        val state = _state.value
        state.current?.let { requestPlay(it.id, state.mode, seconds) }
    }

    fun setNack(enabled: Boolean) {
        _state.update { it.copy(nack = enabled) }
        runOnIo { it.setNack(enabled) }
    }

    fun setQuality(setting: String) {
        _state.update { it.copy(qualitySetting = setting) }
        runOnIo { it.setQuality(setting) }
    }

    fun back() {
        when (_state.value.screen) {
            Screen.Player -> {
                player.stop()
                pipe = null
                _state.update { it.copy(screen = Screen.Videos, current = null, error = null) }
                runOnIo { it.stop() }
            }
            Screen.Videos -> disconnect(null)
            Screen.Connect -> Unit
        }
    }

    private fun disconnect(message: String?) {
        player.stop()
        pipe = null
        val c = client
        client = null
        io.execute { c?.close() }
        _state.update { it.copy(screen = Screen.Connect, busy = false, current = null, error = message) }
    }

    private fun runOnIo(block: (StreamClient) -> Unit) {
        val c = client ?: return
        io.execute {
            try {
                block(c)
            } catch (e: Exception) {
                main.post { _state.update { it.copy(error = e.message) } }
            }
        }
    }

    private fun requestPlay(videoId: Int, mode: String, position: Double) {
        val c = client ?: return
        player.stop()
        pipe = null
        started = false
        _state.update { it.copy(ended = false, error = null) }
        io.execute {
            try {
                val result = c.play(videoId, mode, position)
                main.post {
                    if (client !== c || _state.value.current?.id != videoId) return@post
                    _state.update {
                        it.copy(
                            quality = result.quality,
                            durationSec = result.durationSec?.toFloat() ?: it.durationSec,
                            positionSec = result.position.toFloat(),
                        )
                    }
                    attach(result.pipe, result.position)
                }
            } catch (e: Exception) {
                main.post { _state.update { it.copy(error = "재생 실패: ${e.message}") } }
            }
        }
    }

    private fun attach(newPipe: TsPipe, position: Double) {
        pipe = newPipe
        basePosition = position
        playerOrigin = null
        started = false
        val source = ProgressiveMediaSource.Factory(DataSource.Factory { PipeDataSource(newPipe) }, DefaultExtractorsFactory())
            .createMediaSource(MediaItem.fromUri("netstream://live/stream.ts"))
        player.setMediaSource(source)
        player.prepare()
        player.playWhenReady = true
    }

    // StreamClient.Listener - 네트워크 스레드에서 호출됨

    override fun onOptions(nack: Boolean, qualitySetting: String, currentQuality: String?) {
        _state.update { it.copy(nack = nack, qualitySetting = qualitySetting, quality = currentQuality ?: it.quality) }
    }

    override fun onSwitchModeRequested(mode: String) {
        main.post { setMode(mode) }  // 대시보드에서 바꾼 경우
    }

    override fun onStreamChanged(pipe: TsPipe, quality: String, position: Double) {
        main.post {
            if (_state.value.current == null) return@post
            _state.update { it.copy(quality = quality) }
            attach(pipe, position)
        }
    }

    override fun onServerError(message: String) {
        _state.update { it.copy(error = message) }
    }

    override fun onDisconnected() {
        main.post { if (client != null) disconnect("서버와의 연결이 끊어졌습니다") }
    }

    override fun onReport(report: Report) {
        _state.update { it.copy(report = report) }
    }

    override fun onCleared() {
        main.removeCallbacks(ticker)
        player.release()
        val c = client
        client = null
        io.execute { c?.close() }
        io.shutdown()
    }
}

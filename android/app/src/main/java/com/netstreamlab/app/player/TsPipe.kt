package com.netstreamlab.app.player

import android.net.Uri
import androidx.annotation.OptIn
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.BaseDataSource
import androidx.media3.datasource.DataSpec
import java.io.InterruptedIOException
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

// 수신 스레드 -> 플레이어로 TS를 넘기는 큐
class TsPipe {
    private val queue = LinkedBlockingQueue<ByteArray>()

    @Volatile
    var closed = false
        private set

    fun write(data: ByteArray) {
        if (!closed && data.isNotEmpty()) queue.put(data)
    }

    // 정상 종료: 남은 데이터 다 읽은 뒤에 끝
    fun finish() {
        queue.put(END)
    }

    // 바로 닫기 (탐색, 모드 전환 등)
    fun close() {
        closed = true
        queue.clear()
        queue.offer(END)
    }

    // 끝이면 null
    @Throws(InterruptedException::class)
    fun take(): ByteArray? {
        while (true) {
            if (closed) return null
            val data = queue.poll(200, TimeUnit.MILLISECONDS) ?: continue
            if (data === END) {
                queue.offer(END)
                return null
            }
            return data
        }
    }

    private companion object {
        val END = ByteArray(0)
    }
}

// ExoPlayer용 DataSource. HTTP 대신 우리 소켓에서 받은 데이터(TsPipe)를 읽게 함
@OptIn(UnstableApi::class)
class PipeDataSource(private val pipe: TsPipe) : BaseDataSource(true) {
    private var uri: Uri? = null
    private var current: ByteArray? = null
    private var offset = 0
    private var opened = false

    override fun open(dataSpec: DataSpec): Long {
        uri = dataSpec.uri
        transferInitializing(dataSpec)
        opened = true
        transferStarted(dataSpec)
        return C.LENGTH_UNSET.toLong()
    }

    override fun read(buffer: ByteArray, bufferOffset: Int, length: Int): Int {
        if (length == 0) return 0
        var chunk = current
        if (chunk == null || offset >= chunk.size) {
            chunk = try {
                pipe.take()
            } catch (e: InterruptedException) {
                // 플레이어가 로딩 취소함
                throw InterruptedIOException()
            } ?: return C.RESULT_END_OF_INPUT
            current = chunk
            offset = 0
        }
        val count = minOf(length, chunk.size - offset)
        System.arraycopy(chunk, offset, buffer, bufferOffset, count)
        offset += count
        bytesTransferred(count)
        return count
    }

    override fun getUri(): Uri? = uri

    override fun close() {
        if (opened) {
            opened = false
            transferEnded()
        }
        current = null
    }
}

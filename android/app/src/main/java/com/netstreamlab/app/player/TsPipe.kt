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

/**
 * 네트워크 수신 스레드와 플레이어 사이의 통로.
 * 수신 쪽이 MPEG-TS 조각을 넣고(write), 플레이어가 꺼내 간다(take).
 */
class TsPipe {
    private val queue = LinkedBlockingQueue<ByteArray>()

    @Volatile
    var closed = false
        private set

    fun write(data: ByteArray) {
        if (!closed && data.isNotEmpty()) queue.put(data)
    }

    /** 스트림이 끝났다. 남은 데이터를 다 읽고 나면 끝을 알린다. */
    fun finish() {
        queue.put(END)
    }

    /** 즉시 닫는다 (탐색, 모드 전환, 화질 변경으로 이 스트림을 버릴 때). */
    fun close() {
        closed = true
        queue.clear()
        queue.offer(END)
    }

    /** 다음 조각. 끝났으면 null. 데이터가 올 때까지 기다린다. */
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

/**
 * ExoPlayer가 데이터를 읽어 가는 입구. 보통은 HTTP나 파일이지만,
 * 여기서는 우리가 직접 만든 TCP/UDP 수신 결과(TsPipe)를 그대로 넘긴다.
 */
@OptIn(UnstableApi::class)
class PipeDataSource(private val pipe: TsPipe) : BaseDataSource(/* isNetwork = */ true) {
    private var uri: Uri? = null
    private var current: ByteArray? = null
    private var offset = 0
    private var opened = false

    override fun open(dataSpec: DataSpec): Long {
        uri = dataSpec.uri
        transferInitializing(dataSpec)
        opened = true
        transferStarted(dataSpec)
        return C.LENGTH_UNSET.toLong()  // 길이를 모르는 스트림
    }

    override fun read(buffer: ByteArray, bufferOffset: Int, length: Int): Int {
        if (length == 0) return 0
        var chunk = current
        if (chunk == null || offset >= chunk.size) {
            chunk = try {
                pipe.take()
            } catch (e: InterruptedException) {
                // 플레이어가 읽기를 취소했다 (정지, 해제)
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

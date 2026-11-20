package com.netstreamlab.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.media3.ui.PlayerView
import com.netstreamlab.app.net.Report
import com.netstreamlab.app.net.VideoInfo
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    App()
                }
            }
        }
    }
}

@Composable
private fun App(vm: PlayerViewModel = viewModel()) {
    val state by vm.state.collectAsStateWithLifecycle()
    BackHandler(enabled = state.screen != Screen.Connect) { vm.back() }
    when (state.screen) {
        Screen.Connect -> ConnectScreen(state, vm)
        Screen.Videos -> VideosScreen(state, vm)
        Screen.Player -> PlayerScreen(state, vm)
    }
}

@Composable
private fun ErrorText(message: String?) {
    if (message != null) {
        Text(message, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(vertical = 8.dp))
    }
}

@Composable
private fun ConnectScreen(state: UiState, vm: PlayerViewModel) {
    Column(modifier = Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.Center) {
        Text("NetStream Lab", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text("PC의 영상을 TCP 또는 UDP로 받아 봅니다", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(24.dp))
        OutlinedTextField(
            value = state.host,
            onValueChange = vm::setHost,
            label = { Text("서버 주소 (예: 192.168.0.10)") },
            singleLine = true,
            enabled = !state.busy,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go),
            keyboardActions = KeyboardActions(onGo = { vm.connect() }),
            modifier = Modifier.fillMaxWidth(),
        )
        ErrorText(state.error)
        Spacer(Modifier.height(16.dp))
        Button(onClick = vm::connect, enabled = !state.busy, modifier = Modifier.fillMaxWidth()) {
            Text(if (state.busy) "연결 중" else "연결")
        }
        Spacer(Modifier.height(16.dp))
        Text(
            "PC와 같은 Wi-Fi에 연결되어 있어야 합니다. 주소는 PC에서 서버를 실행하면 표시됩니다.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

private fun formatTime(seconds: Float): String {
    val total = seconds.toInt().coerceAtLeast(0)
    return if (total >= 3600) String.format(Locale.US, "%d:%02d:%02d", total / 3600, total / 60 % 60, total % 60)
    else String.format(Locale.US, "%d:%02d", total / 60, total % 60)
}

@Composable
private fun VideosScreen(state: UiState, vm: PlayerViewModel) {
    Column(modifier = Modifier.fillMaxSize()) {
        Row(modifier = Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text(state.serverName.ifEmpty { "서버" }, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Text("영상 ${state.videos.size}개", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            TextButton(onClick = vm::back) { Text("연결 끊기") }
        }
        HorizontalDivider()
        if (state.videos.isEmpty()) {
            Text("서버의 영상 폴더가 비어 있습니다.", modifier = Modifier.padding(16.dp))
        }
        LazyColumn(modifier = Modifier.fillMaxSize()) {
            items(state.videos, key = { it.id }) { video -> VideoRow(video) { vm.openVideo(video) } }
        }
    }
}

@Composable
private fun VideoRow(video: VideoInfo, onClick: () -> Unit) {
    Column(modifier = Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 12.dp)) {
        Text(video.name, maxLines = 2, overflow = TextOverflow.Ellipsis)
        val details = listOfNotNull(
            video.durationSec?.let { formatTime(it.toFloat()) },
            video.height?.let { "${it}p" },
        ).joinToString(" · ")
        if (details.isNotEmpty()) {
            Text(details, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    HorizontalDivider()
}

@Composable
private fun PlayerScreen(state: UiState, vm: PlayerViewModel) {
    Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        Box(modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f).background(Color.Black), contentAlignment = Alignment.Center) {
            AndroidView(
                factory = { context ->
                    PlayerView(context).apply {
                        useController = false
                        keepScreenOn = true
                        player = vm.player
                    }
                },
                modifier = Modifier.fillMaxSize(),
            )
            if (state.buffering) CircularProgressIndicator(color = Color.White)
            if (state.ended) Text("영상 끝", color = Color.White)
        }

        Column(modifier = Modifier.padding(16.dp)) {
            Text(state.current?.name ?: "", maxLines = 1, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Bold)

            // 손 뗄 때 seek 요청
            if (state.durationSec > 0f) {
                var dragging by remember { mutableStateOf<Float?>(null) }
                Slider(
                    value = (dragging ?: state.positionSec).coerceIn(0f, state.durationSec),
                    onValueChange = { dragging = it },
                    onValueChangeFinished = {
                        dragging?.let { vm.seekTo(it.toDouble()) }
                        dragging = null
                    },
                    valueRange = 0f..state.durationSec,
                )
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(formatTime(dragging ?: state.positionSec), style = MaterialTheme.typography.bodySmall)
                    Text(formatTime(state.durationSec), style = MaterialTheme.typography.bodySmall)
                }
            }
            ErrorText(state.error)
            Spacer(Modifier.height(8.dp))

            Text("전송 방식", style = MaterialTheme.typography.labelLarge)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ModeButton("TCP", state.mode == "tcp", Modifier.weight(1f)) { vm.setMode("tcp") }
                ModeButton("UDP", state.mode == "udp", Modifier.weight(1f)) { vm.setMode("udp") }
            }

            Row(modifier = Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text("NACK 재전송")
                    Text(
                        "UDP에서 잃은 패킷만 다시 요청합니다",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Switch(checked = state.nack, onCheckedChange = vm::setNack)
            }

            Row(modifier = Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("화질", modifier = Modifier.weight(1f))
                QualityMenu(state, vm)
            }

            Spacer(Modifier.height(12.dp))
            StatsCard(state.report, state.quality)
            Spacer(Modifier.height(8.dp))
            TextButton(onClick = vm::back) { Text("목록으로") }
        }
    }
}

@Composable
private fun ModeButton(label: String, selected: Boolean, modifier: Modifier, onClick: () -> Unit) {
    if (selected) {
        Button(onClick = onClick, modifier = modifier) { Text(label) }
    } else {
        OutlinedButton(onClick = onClick, modifier = modifier) { Text(label) }
    }
}

private fun qualityLabel(setting: String) = if (setting == "auto") "자동 (ABR)" else setting

@Composable
private fun QualityMenu(state: UiState, vm: PlayerViewModel) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick = { open = true }) { Text(qualityLabel(state.qualitySetting)) }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            for (option in listOf("auto") + state.qualities) {
                DropdownMenuItem(
                    text = { Text(qualityLabel(option)) },
                    onClick = {
                        open = false
                        vm.setQuality(option)
                    },
                )
            }
        }
    }
}

@Composable
private fun StatsCard(report: Report, quality: String?) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text("네트워크 상태", style = MaterialTheme.typography.labelLarge)
            Spacer(Modifier.height(4.dp))
            StatRow("현재 화질", quality ?: "-")
            StatRow("수신 속도", "${report.kbps} kbps")
            if (report.mode == "udp") {
                StatRow("손실률", String.format(Locale.US, "%.1f %%", report.rawLossRate * 100))
                StatRow("복구 후 손실률", String.format(Locale.US, "%.1f %%", report.lossRate * 100))
                StatRow("복구한 패킷", "${report.recovered}개/초")
                StatRow("순서 뒤바뀜", "${report.reordered}개/초")
            } else {
                StatRow("손실률", "0 % (TCP가 재전송)")
            }
            StatRow("지터", String.format(Locale.US, "%.1f ms", report.jitterMs))
            StatRow("RTT", report.rttMs?.let { String.format(Locale.US, "%.1f ms", it) } ?: "-")
            StatRow("버퍼", String.format(Locale.US, "%.1f 초", report.bufferMs / 1000.0))
        }
    }
}

@Composable
private fun StatRow(label: String, value: String) {
    Row(modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(label, modifier = Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(8.dp))
        Text(value, fontWeight = FontWeight.Medium)
    }
}

# NetStream Lab 프로토콜 (버전 1)

구현은 `pc/netstream/protocol.py`와 `android/.../net/Protocol.kt`에 있습니다. 숫자는 모두 big-endian입니다.

## 1. 채널

| 채널 | 전송 계층 | 기본 포트 | 용도 |
|---|---|---|---|
| 제어 | TCP | 9000 | 명령, 응답, NACK, 통계 보고 |
| 데이터 (TCP 모드) | TCP | 9001 | 영상 |
| 데이터 (UDP 모드) | UDP | 9002 | 영상 |

제어 메시지는 하나도 잃으면 안 되므로 TCP를 씁니다. 영상은 모드에 따라 TCP 또는 UDP로 갑니다.

## 2. 제어 채널

### 프레임

```
+----------------+----------------------+
| 길이 (4바이트) | UTF-8 JSON (길이만큼) |
+----------------+----------------------+
```

TCP는 바이트 스트림이라 메시지 경계가 없습니다. 그래서 길이를 앞에 붙입니다. 최대 1MB입니다.

모든 메시지에는 `type`이 있습니다. 요청에 `id`를 넣으면 응답에 같은 `id`가 돌아옵니다.

### 클라이언트 → 서버

| type | 필드 | 응답 | 설명 |
|---|---|---|---|
| `HELLO` | `client`, `version`, `flush_on_change` | `HELLO_OK` | 접속. `flush_on_change`가 참이면 화질 변경 시 재생 위치부터 다시 받는다 |
| `LIST` | | `LIST_OK` | 영상 목록 |
| `PLAY` | `video_id`, `mode`(`tcp`/`udp`), `position`(초) | `PLAY_OK` | 재생. 탐색과 모드 전환도 이 메시지로 한다 |
| `STOP` | | `STOP_OK` | 정지 |
| `SET_OPTIONS` | `nack`(bool), `quality`(`auto` 또는 화질 이름) | `OPTIONS` | 설정 변경 |
| `NACK` | `stream_id`, `seqs`(번호 배열, 최대 200) | 없음 | 잃은 패킷 재전송 요청 |
| `PING` | `t` | `PONG` | RTT 측정. 서버는 `t`를 그대로 돌려준다 |
| `REPORT` | 아래 참고 | 없음 | 1초마다 보내는 수신 통계 |

`REPORT` 필드 (모두 최근 1초 기준): `stream_id`, `mode`, `kbps`, `packets`, `lost`(끝내 못 받은 수), `recovered`(재전송으로 채운 수), `reordered`, `loss_rate`(최종 손실률 0~1), `raw_loss_rate`(재전송 전 손실률), `jitter_ms`, `rtt_ms`, `buffer_ms`, `stalled`, `position`.

### 서버 → 클라이언트

| type | 필드 | 설명 |
|---|---|---|
| `HELLO_OK` | `token`, `tcp_data_port`, `udp_data_port`, `qualities`, `nack`, `quality` | `token`은 데이터 채널을 이 세션에 묶는 데 쓴다 |
| `LIST_OK` | `videos`: `[{id, name, size, duration, width, height}]` | |
| `PLAY_OK` | `stream_id`, `mode`, `quality`, `position`, `duration` | 이 `stream_id`의 패킷이 곧 온다 |
| `STREAM_CHANGE` | `stream_id`, `quality`, `position`, `reason` | 서버가 화질을 바꿨다. 새 `stream_id`로 이어진다 |
| `OPTIONS` | `nack`, `quality`, `current_quality` | 설정이 바뀌었다 (대시보드에서 바꾼 경우 포함) |
| `SWITCH_MODE` | `mode` | 대시보드의 요청. 클라이언트가 보던 위치에서 `PLAY`를 다시 보낸다 |
| `EOS` | `stream_id`, `packets`(총 패킷 수) | 영상 끝 |
| `UDP_ATTACHED` | `port` | UDP 주소 등록 확인 |
| `PONG` | `t` | |
| `ERROR` | `message` | |

## 3. 데이터 패킷

TCP 모드와 UDP 모드가 같은 형식을 씁니다.

```
 0        2            6            10           14       15           17
+--------+------------+------------+------------+--------+------------+---------------+
|stream_id|    seq     |  send_ms   |  media_ms  | flags  |payload_len |    payload    |
|  (2)   |    (4)     |    (4)     |    (4)     |  (1)   |    (2)     | (최대 1316)   |
+--------+------------+------------+------------+--------+------------+---------------+
```

| 필드 | 설명 |
|---|---|
| `stream_id` | 탐색, 모드 전환, 화질 변경마다 바뀐다. 지금 것과 다르면 버린다 (이전 스트림의 늦은 패킷) |
| `seq` | 스트림마다 0부터 1씩 증가 |
| `send_ms` | 서버가 보낸 시각 (스트림 시작 기준). 지터 계산에 쓴다 |
| `media_ms` | 이 패킷의 영상 시각 (MPEG-TS PCR 기준) |
| `flags` | `0x01` RTX (재전송된 패킷), `0x02` EOS (스트림 끝, payload 없음) |
| `payload` | MPEG-TS 패킷 188바이트 × 최대 7개 |

패킷 전체는 최대 1333바이트이고, UDP와 IP 헤더를 더하면 1361바이트로 MTU 1500보다 작습니다.

## 4. 데이터 채널 연결

**TCP 모드.** 클라이언트가 9001번에 연결하고 제어 채널과 같은 프레임 형식으로 `{"type":"ATTACH","token":"..."}`을 보냅니다. 서버가 `{"type":"ATTACH_OK"}`로 답한 뒤부터는 데이터 패킷이 이어집니다. 스트림이므로 헤더의 `payload_len`으로 경계를 찾습니다.

**UDP 모드.** 클라이언트가 9002번으로 `"NSL1" + token` 데이터그램을 보냅니다. 서버는 그 데이터그램의 **출발지 주소**를 이 세션의 UDP 주소로 기억하고, 제어 채널로 `UDP_ATTACHED`를 보냅니다. 데이터그램이 사라질 수 있으므로 클라이언트는 확인될 때까지 0.2초마다 다시 보냅니다. 이후 5초마다 한 번씩 보내 경로를 유지합니다.

## 5. 흐름 예시 (UDP, NACK 사용)

```
클라이언트                                   서버
   │── HELLO ────────────────────(TCP)──────▶│
   │◀─ HELLO_OK {token} ─────────────────────│
   │── "NSL1"+token ─────────────(UDP)──────▶│   출발지 주소를 기억
   │◀─ UDP_ATTACHED ─────────────(TCP)───────│
   │── PLAY {video 0, udp} ─────────────────▶│
   │◀─ PLAY_OK {stream 1} ───────────────────│   ffmpeg 시작
   │◀═ 패킷 seq 0, 1, 2, 4, 5 ═══(UDP)═══════│   3번이 사라짐
   │   (4를 받고 3이 비었음을 앎. 20ms 대기)
   │── NACK {stream 1, seqs:[3]} ─(TCP)─────▶│
   │◀═ 패킷 seq 3, RTX ══════════(UDP)═══════│
   │   (3을 채워 3, 4, 5 순서로 플레이어에 전달)
   │── REPORT {recovered: 1, ...} ──────────▶│   대시보드와 ABR이 사용
```

## 6. 수신 측 규칙 (UDP)

- `seq`가 지금까지 본 최댓값보다 크면 그 사이 번호를 "빈자리"로 기록한다.
- 빈자리가 채워졌을 때 RTX 패킷이면 **복구**, 원래 패킷이면 **순서 뒤바뀜**으로 센다.
- 맨 앞 빈자리가 제한 시간을 넘기면 **손실**로 세고 건너뛴다. 제한 시간은 NACK가 꺼져 있으면 60ms, 켜져 있으면 `max(0.6초, 5 × RTT)`.
- NACK는 빈자리를 발견하고 20ms 뒤에 보낸다. 응답이 없으면 `max(80ms, 2 × RTT)`마다 최대 4번 보낸다.
- 지터는 RFC 3550 방식: `J = J + (|D| − J) / 16`, `D`는 연속한 두 패킷의 (도착 시각 − `send_ms`) 차이.

# 프로토콜 정리

코드는 `pc/netstream/protocol.py`, 안드로이드는 `net/Protocol.kt`. 숫자는 전부 big-endian.

## 채널

| | 포트 | 용도 |
|---|---|---|
| 제어 (TCP) | 9000 | 명령, 응답, NACK, 통계 |
| 데이터 (TCP) | 9001 | TCP 모드 영상 |
| 데이터 (UDP) | 9002 | UDP 모드 영상 |

제어 메시지는 없어지면 안 되니까 항상 TCP.

## 제어 채널

```
| 길이 (4바이트) | JSON (UTF-8) |
```

TCP는 메시지 경계가 없어서 앞에 길이를 붙였다. 최대 1MB.
모든 메시지에 `type`이 있고, 요청에 `id`를 넣으면 응답에 같은 `id`가 온다.

클라이언트 -> 서버

| type | 내용 | 응답 |
|---|---|---|
| HELLO | `client`, `version`, `flush_on_change` | HELLO_OK |
| LIST | | LIST_OK |
| PLAY | `video_id`, `mode`(tcp/udp), `position`(초) | PLAY_OK |
| STOP | | STOP_OK |
| SET_OPTIONS | `nack`, `quality`(auto 또는 720p 같은 이름) | OPTIONS |
| NACK | `stream_id`, `seqs`(최대 200개) | 없음 |
| PING | `t` | PONG (t 그대로) |
| REPORT | 1초 통계 | 없음 |

- 탐색이랑 모드 전환은 따로 메시지가 없고 PLAY를 다시 보낸다.
- `flush_on_change`가 true면 화질이 바뀔 때 서버가 재생 위치부터 다시 보내준다 (안드로이드용).
- REPORT에 들어가는 것: `stream_id`, `mode`, `kbps`, `packets`, `lost`, `recovered`, `reordered`, `loss_rate`, `raw_loss_rate`, `jitter_ms`, `rtt_ms`, `buffer_ms`, `stalled`, `position`
  - `raw_loss_rate`는 재전송 전, `loss_rate`는 복구 후 기준

서버 -> 클라이언트

| type | 내용 |
|---|---|
| HELLO_OK | `token`, `tcp_data_port`, `udp_data_port`, `qualities`, `nack`, `quality` |
| LIST_OK | `videos`: id, name, size, duration, width, height |
| PLAY_OK | `stream_id`, `mode`, `quality`, `position`, `duration` |
| STREAM_CHANGE | 서버가 화질을 바꿈. `stream_id`, `quality`, `position`, `reason` |
| OPTIONS | `nack`, `quality`, `current_quality` |
| SWITCH_MODE | 대시보드에서 모드를 바꿈. 클라이언트가 PLAY를 다시 보내야 함 |
| EOS | `stream_id`, `packets`(총 개수) |
| UDP_ATTACHED | UDP 주소 등록됨 |
| PONG, ERROR | |

## 데이터 패킷

TCP/UDP 둘 다 같은 형식.

```
stream_id(2) | seq(4) | send_ms(4) | media_ms(4) | flags(1) | payload_len(2) | payload
```

- `stream_id` : 탐색/모드 전환/화질 변경 때마다 바뀜. 현재 것과 다르면 버린다 (이전 스트림 패킷이 늦게 오는 경우)
- `seq` : 스트림마다 0부터
- `send_ms` : 서버가 보낸 시각. 지터 계산용
- `media_ms` : 영상 기준 시각 (TS의 PCR에서 읽음). 버퍼 계산용
- `flags` : 0x01 = 재전송(RTX), 0x02 = 끝(EOS)
- `payload` : TS 패킷 188바이트 * 최대 7개 = 1316

헤더 17 + 1316 + UDP 8 + IP 20 = 1361 < MTU 1500

## 데이터 채널 연결

TCP: 9001에 연결해서 `{"type":"ATTACH","token":...}`을 제어 채널이랑 같은 프레임 형식으로 보낸다. `ATTACH_OK`가 오면 그 뒤로는 데이터 패킷만 온다. 경계는 헤더의 payload_len으로 구분.

UDP: 9002로 `"NSL1" + token`을 보낸다. 서버는 이 패킷의 출발지 주소를 기억했다가 거기로 영상을 보낸다. 등록되면 제어 채널로 UDP_ATTACHED가 온다. 패킷이 없어질 수 있어서 확인될 때까지 0.2초마다 다시 보내고, 그 뒤로도 5초마다 한 번씩 보낸다.

## 예시 (UDP + NACK)

```
C -> S  HELLO                       (TCP)
S -> C  HELLO_OK {token}
C -> S  "NSL1"+token                (UDP)
S -> C  UDP_ATTACHED                (TCP)
C -> S  PLAY {video 0, udp}
S -> C  PLAY_OK {stream 1}
S -> C  seq 0, 1, 2, 4, 5           (UDP)  3번 손실
        4를 받고 3이 빈 걸 알게 됨, 20ms 기다림
C -> S  NACK {stream 1, seqs [3]}   (TCP)
S -> C  seq 3 (RTX)                 (UDP)
        3, 4, 5 순서로 플레이어에 넘김
```

## UDP 수신 규칙

- 지금까지 본 최대 seq보다 큰 게 오면 그 사이 번호를 빈자리로 기록
- 빈자리가 RTX 패킷으로 채워지면 "복구", 원래 패킷으로 채워지면 "순서 뒤바뀜"
- 맨 앞 빈자리가 시간 초과되면 "손실"로 치고 넘어감
  - NACK 꺼짐: 60ms
  - NACK 켜짐: max(0.6초, 5 * RTT)
- NACK는 빈자리 발견 20ms 뒤에 보냄. 안 오면 max(80ms, 2 * RTT) 간격으로 최대 4번
- 지터는 RFC 3550 방식. `J += (|D| - J) / 16`

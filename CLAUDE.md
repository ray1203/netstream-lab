# NetStream Lab 작업 노트

새 세션에서 이어서 작업할 때 먼저 읽을 것. 마지막 갱신: 2026-11-25

## 뭐 하는 프로젝트인지

컴퓨터네트워크 과제 4. PC 영상을 폰으로 스트리밍하면서 TCP/UDP 전송 차이를 보여주는 데모.
과제 조건: (1) TCP 또는 UDP 사용 (2) 프로토콜이 실제로 어떻게 동작하는지 보여줄 것 (3) Agentic AI 활용 (4) 시연 + GitHub 공개 가능.
원래 기획은 `기획서.md`, 마감은 과제 공지일로부터 2주.

저장소: https://github.com/ray1203/netstream-lab (공개)

## 현재 상태

기획서의 필수 + 권장 기능은 다 구현됐고 실제 폰에서 시연 순서대로 돌려봤다. 코드 작업은 사실상 끝났고 남은 건 제출 준비 쪽.

구현된 것
- 서버 (Python asyncio, 외부 패키지 없음): 제어 채널, ffmpeg 트랜스코딩, TCP/UDP 데이터 채널, NACK 재전송, ABR, 열화 시뮬레이터
- 웹 대시보드 (직접 만든 HTTP + SSE, 그래프도 라이브러리 없이 SVG)
- PC 테스트 클라이언트 (ffplay로 재생)
- 안드로이드 앱 (Kotlin, Compose, Media3 ExoPlayer)
- 통합 테스트 `pc/tests/e2e.py` (21개 체크, 전부 통과)

안 만든 것
- 서버 자동 찾기(UDP 브로드캐스트). 기획서에서 "선택"이었고 사용자가 빼기로 함
- 로그인, 여러 명 동시 접속 (기획서 범위 밖)

## 남은 일

- [x] 측정 데이터 정리 -> `report/측정결과.md` (11/24, 표만. 그래프는 사용자가 안 만들기로 함). 다시 재려면 `python -m tools.measure` 후 `python -m tools.summarize`
- [ ] (선택) 지터가 클 때 불필요한 NACK 재전송 고치기. 아래 "알려진 문제" 첫 줄
- [ ] 시연 GIF나 영상, README에 넣기
- [x] 보고서 초안 (11/24, 영어, Word). `report/NetStreamLab_1_User_Guide.docx`(소개+사용법), `report/NetStreamLab_2_Technical_Report.docx`(기술+측정)
  - 사용자가 이름/학번 채우고 내용 검토해야 함. AI가 초안을 썼다는 것도 보고서 11절에 적혀 있음
  - 그림: `python -m tools.figures` (matplotlib 필요) -> `report/figures/`
  - 문서 다시 만들기: `report/docsrc/`에서 `npm install docx` 후 `node guide.js ../figures ../NetStreamLab_1_User_Guide.docx`, `node tech.js ../figures ../NetStreamLab_2_Technical_Report.docx`
  - 이름/학번은 저장소 문서엔 안 넣음(공개될 거라서). 빌드할 때 환경변수 `NSL_AUTHOR="이름 (학번)"`을 주면 첫 쪽에 들어감. 이름/학번은 이 파일을 포함해 저장소 어디에도 적지 말 것
  - 제출용 폴더: `submit/NetStreamLab_<학번>_<이름>/` + 같은 이름 zip (gitignore됨). 문서 2개(이름 들어간 버전), APK, `source/`(git archive로 뽑은 pc, android, md 3개 + 측정 CSV). 코드나 문서를 고치면 이 폴더도 다시 만들어야 함
  - 측정을 다시 하면 tech.js 안의 표 숫자는 손으로 고쳐야 함 (CSV에서 자동으로 안 읽음)
  - 렌더링 확인: 이 PC엔 LibreOffice/pdftoppm이 없어서 Word COM으로 쪽을 이미지로 뽑아 확인했음
- [x] 저장소 공개 전환 (11/27 확인 시 이미 공개)
- [x] GitHub Release v1.0에 APK 올림 (11/27). 앱을 고치면 새로 빌드해서 Release에 다시 올릴 것 (`gh release upload v1.0 <apk> --clobber`, 파일명 NetStreamLab.apk 유지. README의 다운로드 링크가 이 이름을 씀)
- [ ] 과제 제출: 저장소 링크 + 스크린샷 1장 (공지 기준). zip은 예비용
- [ ] 사용자가 `AI_LOG.md` 끝의 "내가 따로 공부해야 할 것" 항목들 직접 설명할 수 있게 준비
- [ ] (선택) 긴 실제 영상으로 테스트. 지금까지는 60초짜리 테스트 패턴만 씀
- [ ] (선택) 아래 "알려진 문제" 수정

## 알려진 문제

- 지터가 크면(50ms) 손실이 없어도 NACK가 나감. 빈자리 발견 20ms 뒤(`NACK_GRACE`)에 보내는데 그냥 늦게 오는 패킷까지 재요청함. 측정에서 재전송 비율이 상한인 25%까지 참. 고치려면 대기 시간을 지터에 맞춰 늘려야 하고 receiver.py, ReorderBuffer.kt 둘 다 고쳐야 함
- PC 클라이언트의 "멈춘 시간"/버퍼는 추정치라 손실이 심해도 안 멈춘 걸로 나옴. 또 ABR로 화질이 바뀔 때마다 버퍼 추정치가 3초씩 늘어남 (PC는 버퍼를 안 비우는데 서버가 새 스트림마다 3초 앞서 보내서)
- 손실이 심한 상태(60% 이상)로 영상이 끝나면 폰이 버퍼를 28~40초로 보고함. 원인 확인 안 함. 정상 조건에서는 안 나옴
- 앱 화질 메뉴에 원본보다 높은 화질도 보임. 골라도 서버가 원본 해상도로 제한해서 실제로는 안 바뀜 (`Session._max_level`)
- 재생 중 STOP 직후 1~2초 동안 대시보드에 이전 통계가 남아 보임
- 클라이언트가 정상적으로 나가도 서버 로그에 "제어 채널 오류 WinError 64"가 찍힘. 동작엔 문제 없음
- PLAY_OK를 받기 전에 데이터 패킷이 먼저 오면 버려짐. 서버가 PLAY_OK 보낸 뒤에 ffmpeg를 띄워서 실제로는 안 생김
- 안드로이드에서 화질 바뀔 때 플레이어를 다시 준비해서 잠깐 멈춤 (설계상 그렇게 함)
- 윈도우 타이머 때문에 시뮬레이터 지연이 15ms쯤 오차 (`pc/netstream/timing.py`)

## 구조

```
pc/netstream/
  protocol.py        메시지 프레임, 데이터 패킷 헤더(17바이트), PCR 파싱
  timing.py          윈도우 타이머 보정
  server/
    __main__.py      실행 진입점, 인자 파싱
    app.py           NetStreamServer / Session / Stream. 핵심
    transcoder.py    ffmpeg 명령 만들기, 화질 사다리(LADDER), ffmpeg 찾기
    impair.py        열화 시뮬레이터. UdpLink(실제 drop), TcpLink(모델)
    abr.py           자동 화질 조절
    library.py       영상 폴더 스캔, ffprobe
    dashboard.py     HTTP + SSE, 1초마다 통계 샘플
    web/index.html   대시보드 페이지 (한 파일)
  client/
    core.py          Client 클래스 (제어/데이터 채널)
    receiver.py      ReorderBuffer, JitterMeter, PlayClock (소켓 없는 순수 로직)
    __main__.py      CLI, ffplay 연결
pc/tests/e2e.py      통합 테스트
android/app/src/main/java/com/netstreamlab/app/
  net/Protocol.kt        protocol.py 대응
  net/ReorderBuffer.kt   receiver.py 대응 (같은 알고리즘, 고치면 둘 다 고칠 것)
  net/StreamClient.kt    core.py 대응, 스레드 기반
  player/TsPipe.kt       TsPipe(큐) + PipeDataSource(ExoPlayer용)
  PlayerViewModel.kt     상태, 플레이어 제어
  MainActivity.kt        Compose 화면 3개 (연결 / 목록 / 재생)
```

프로토콜 형식은 `PROTOCOL.md`에 다 있음. 프로토콜을 바꾸면 protocol.py, Protocol.kt, PROTOCOL.md 세 군데를 같이 고쳐야 한다.

## 알아둘 설계 결정

- 포트: 제어 TCP 9000, 데이터 TCP 9001, 데이터 UDP 9002, 대시보드 9080(기본은 127.0.0.1에만 열림)
- payload 1316바이트 = TS 188 * 7. MTU 안 넘게
- 서버가 TS의 PCR을 읽어서 실시간보다 3초 앞서 보냄(`LEAD_MS`). ffmpeg는 `-re` 없이 최대 속도, 파이프 backpressure로 조절됨. 처음 0.5초는 바로, 그 뒤는 2배속까지(`CATCH_UP_RATE`)
- 탐색, 모드 전환은 전용 메시지 없이 PLAY를 다시 보냄. 그때마다 stream_id가 바뀜
- 화질 변경은 서버가 ffmpeg를 재시작하고 STREAM_CHANGE로 알림. 안드로이드는 HELLO에 `flush_on_change: true`를 보내서 재생 위치부터 다시 받음
- UDP 주소 등록: 클라이언트가 `"NSL1"+token`을 보내면 서버가 출발지 주소를 기억
- TCP 모드 손실은 모델임 (HOL blocking + Mathis 공식). 앱에서 TCP 세그먼트를 못 버리기 때문. 발표 때 질문 나올 수 있는 부분이라 README에 적어둠
- ABR은 복구 후 손실률(`loss_rate`) 기준. 2번 연속 나쁘면 수신속도 70%에 맞춰 내리고, 10번 연속 좋으면 한 칸 올림
- NACK 재전송은 토큰 버킷으로 제한 (데이터의 25%, 최대 64KB 적립). 폰 테스트에서 재전송 폭주(12Mbps)가 나와서 넣음
- 스트림 전환(재생/탐색/화질변경)은 `Session._stream_lock`으로 직렬화. ffmpeg는 task 만들기 전에 먼저 띄움 (spawn 중 cancel되면 프로세스가 남음)
- 화질 사다리: 144p/240p/360p/480p/720p/1080p. 원본 해상도보다 위로는 안 올림

## 실행 / 테스트

```
cd pc
python -m netstream.server --media "영상 폴더"      # 서버
python -m netstream.client --host 127.0.0.1 --video 0  # PC 클라이언트 (--no-play면 통계만)
python -m tests.e2e                                    # 통합 테스트, 2분쯤 걸림

cd android
.\gradlew.bat assembleDebug    # apk: app/build/outputs/apk/debug/app-debug.apk
```

- e2e는 포트 19000~19002, 19080을 써서 서버가 떠 있어도 같이 돌릴 수 있다
- 테스트 영상은 e2e가 처음 돌 때 `samples/testsrc_720p_60s.mp4`로 만든다 (gitignore됨)
- 서버 코드를 고치면 e2e를 돌려볼 것. 안드로이드 쪽을 고치면 빌드 + 에뮬레이터 확인

## 이 PC 환경 (작업할 때 걸리는 것들)

- Windows 11, Python 3.10.5, JDK 18, RTX 3050 Ti (NVENC 됨, 서버가 자동으로 고름)
- ffmpeg는 winget으로 설치돼 있음 (Gyan.FFmpeg). PATH에 안 잡히는 셸이 있어서 `transcoder.find_tool`이 winget 경로도 찾아봄
- 프로젝트 경로에 한글이 있음
  - 안드로이드 빌드: `android/gradle.properties`의 `android.overridePathCheck=true` 필요 (이미 있음)
  - `android/local.properties`는 `sdk.dir=C:/Users/<사용자>/AppData/Local/Android/Sdk` 처럼 슬래시로 쓸 것. 역슬래시는 깨짐. 이 파일은 gitignore됨
- 파이썬 출력에 한글이 있어서 bash에서 돌릴 땐 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`을 붙임
- Android SDK: `%LOCALAPPDATA%\Android\Sdk` (플랫폼 34, 빌드도구 34 설치됨). adb는 그 안의 `platform-tools\adb.exe`, PATH엔 없음
- 에뮬레이터 AVD: `Pixel_4_API_27`, `Pixel_4_API_24`. 에뮬레이터에서 서버 주소는 `10.0.2.2`
  - 띄우기: `emulator.exe -avd Pixel_4_API_27 -no-snapshot-save -no-boot-anim -gpu swiftshader_indirect -no-audio`
  - 부팅 직후 잠금화면이라 `adb shell input keyevent 82` 한 번 필요
- 폰 테스트: 갤럭시 S23 울트라, 학교 와이파이에서 기기 간 통신 됨. PC IP는 서버 켜면 표시됨
- Python 방화벽 수신 허용 규칙은 이미 있음
- 서버를 백그라운드로 띄웠으면 끌 때 커맨드라인으로 찾아서 종료:
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.CommandLine -match 'netstream\.server' } | % { Stop-Process -Id $_.ProcessId -Force }`
  강제 종료하면 ffmpeg가 남을 수 있으니 같이 확인
- 대시보드 통계를 스크립트로 보려면 `curl -s -m 3 http://127.0.0.1:9080/events`의 첫 `data:` 줄에 최근 180초 history가 JSON으로 들어 있음. 폰 테스트 결과는 이걸로 확인했음

## 작업 규칙 (사용자가 정한 것)

- 주석이랑 문서는 AI가 쓴 티 안 나게. 짧은 메모체("~함", "~라서"), 당연한 설명 주석 금지, `# ----- 제목 -----` 같은 구분선 금지, 긴 독스트링 금지. README도 굵은 소제목 + 표 위주로 딱딱하게 쓰지 말고 평어체로
- 단, AI 사용 사실 자체는 숨기지 않는다. 과제 조건이라 `AI_LOG.md`랑 README의 "AI 사용" 항목은 유지
- `AI_LOG.md`는 사용자 1인칭으로 쓰되 실제로 있었던 일만 적을 것. 사용자의 느낌이나 생각을 지어내지 말 것. 작업이 진행되면 날짜별로 이어서 추가
- 커밋과 푸시는 사용자가 하라고 할 때만. 커밋 메시지는 짧은 한국어 ("주석, 문서 정리" 같은 식)
- 뭔가 설치하거나 내려받기 전에는 물어볼 것
- 직접 확인 가능한 건(환경, 로그) 묻지 말고 확인하고, 사용자가 정해야 하는 것만 질문

## 커밋 이력

`git log` 참고

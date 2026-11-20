"""PC 테스트 클라이언트 실행:  python -m netstream.client --host 127.0.0.1

받은 MPEG-TS를 ffplay의 표준 입력으로 넘겨 재생한다. 명령은 터미널에 입력한다.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import threading

from .. import protocol as P
from ..server.transcoder import find_tool
from ..timing import use_precise_clock
from .core import Client, NullSink

HELP = """명령:
  list              영상 목록
  play <번호>       재생 (현재 모드)
  tcp | udp         전송 모드 전환 (보던 위치에서 이어서)
  seek <초>         탐색
  nack on|off       UDP 손실 패킷 재전송 요청
  quality <이름>    auto, 144p, 240p, 360p, 480p, 720p, 1080p
  stop              정지
  quit              종료"""


class FfplaySink:
    """받은 TS를 ffplay로 넘긴다. 파이프 쓰기가 막혀도 수신이 멈추지 않도록 별도 스레드에서 쓴다."""

    def __init__(self, ffplay: str):
        self.ffplay = ffplay
        self._proc: subprocess.Popen | None = None
        self._queue: list[bytes] = []
        self._cond = threading.Condition()
        self._generation = 0

    def restart(self) -> None:
        self.close()
        with self._cond:
            self._generation += 1
            generation = self._generation
            self._queue = []
        self._proc = subprocess.Popen(
            [self.ffplay, "-hide_banner", "-loglevel", "error", "-window_title", "NetStream Lab",
             "-f", "mpegts", "-i", "pipe:0"],
            stdin=subprocess.PIPE,
        )
        threading.Thread(target=self._pump, args=(self._proc, generation), daemon=True).start()

    def write(self, data: bytes) -> None:
        with self._cond:
            self._queue.append(data)
            self._cond.notify()

    def _pump(self, proc: subprocess.Popen, generation: int) -> None:
        while True:
            with self._cond:
                while not self._queue and generation == self._generation:
                    self._cond.wait(0.5)
                if generation != self._generation:
                    return
                data = b"".join(self._queue)
                self._queue = []
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, ValueError):
                return  # 사용자가 ffplay 창을 닫았다

    def close(self) -> None:
        with self._cond:
            self._generation += 1
            self._cond.notify_all()
        if self._proc:
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None


def format_report(client: Client, r: dict) -> str:
    return (
        f"[{(client.mode or '-').upper():3} {client.quality or '-':>5}] "
        f"{r['kbps']:5d} kbps | 손실 {r['raw_loss_rate'] * 100:4.1f}% → 복구 후 {r['loss_rate'] * 100:4.1f}% "
        f"| 뒤바뀜 {r['reordered']:3d} | 지터 {r['jitter_ms']:5.1f} ms | RTT {r['rtt_ms'] or 0:5.1f} ms "
        f"| 버퍼 {r['buffer_ms'] / 1000:4.1f} s{' (멈춤)' if r['stalled'] else ''}"
    )


async def run(args: argparse.Namespace) -> int:
    use_precise_clock()
    if args.no_play:
        sink = NullSink()
    else:
        ffplay = find_tool("ffplay")
        if not ffplay:
            print("ffplay를 찾지 못했습니다. --no-play로 통계만 볼 수 있습니다.", file=sys.stderr)
            return 1
        sink = FfplaySink(ffplay)

    client = Client(args.host, args.port, sink)
    client.on_report = lambda r: print(format_report(client, r))

    def on_message(m: dict) -> None:
        if m.get("type") == "STREAM_CHANGE":
            print(f"* 화질 변경: {m.get('quality')} ({m.get('reason')})")
        elif m.get("type") == "ERROR":
            print(f"* 서버 오류: {m.get('message')}")
        elif m.get("type") == "EOS":
            print("* 영상 끝")

    client.on_message = on_message
    try:
        hello = await client.connect()
    except OSError as error:
        print(f"서버에 연결하지 못했습니다: {error}", file=sys.stderr)
        return 1
    print(f"{hello['server']}에 연결했습니다.")
    videos = await client.list_videos()
    for video in videos:
        duration = f"{video['duration']:.0f}초" if video.get("duration") else "?"
        print(f"  [{video['id']}] {video['name']} ({duration})")
    print(HELP)

    mode = args.mode
    if args.nack:
        await client.set_options(nack=True)
    if args.video is not None:
        await client.play(args.video, mode, args.position)

    loop = asyncio.get_running_loop()
    commands: asyncio.Queue = asyncio.Queue()

    def read_stdin() -> None:
        for line in sys.stdin:
            loop.call_soon_threadsafe(commands.put_nowait, line.strip())
        loop.call_soon_threadsafe(commands.put_nowait, "quit")

    threading.Thread(target=read_stdin, daemon=True).start()

    while True:
        words = (await commands.get()).split()
        if not words:
            continue
        command, rest = words[0].lower(), words[1:]
        try:
            if command in ("quit", "exit", "q"):
                break
            elif command == "list":
                for video in await client.list_videos():
                    print(f"  [{video['id']}] {video['name']}")
            elif command == "play" and rest:
                await client.play(int(rest[0]), mode)
            elif command in ("tcp", "udp"):
                mode = command
                if client.video_id is not None:
                    await client.play(client.video_id, mode, client.position(), restart_sink=False)
            elif command == "seek" and rest and client.video_id is not None:
                await client.play(client.video_id, mode, float(rest[0]))
            elif command == "nack" and rest:
                await client.set_options(nack=rest[0].lower() == "on")
            elif command == "quality" and rest:
                await client.set_options(quality=rest[0])
            elif command == "stop":
                await client.stop()
            else:
                print(HELP)
        except (RuntimeError, ValueError, asyncio.TimeoutError) as error:
            print(f"* 실패: {error}")
    await client.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="netstream.client", description="NetStream Lab PC 테스트 클라이언트")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=P.CONTROL_PORT)
    parser.add_argument("--mode", default="tcp", choices=["tcp", "udp"])
    parser.add_argument("--video", type=int, help="바로 재생할 영상 번호")
    parser.add_argument("--position", type=float, default=0.0)
    parser.add_argument("--nack", action="store_true")
    parser.add_argument("--no-play", action="store_true", help="재생하지 않고 통계만 출력")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

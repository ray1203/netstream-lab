"""서버 실행:  python -m netstream.server --media "D:\\Videos" """

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
from pathlib import Path

from .. import protocol as P
from ..timing import use_precise_clock
from .app import NetStreamServer
from .dashboard import Dashboard
from .library import Library
from .transcoder import LADDER, Transcoder, find_tool


def lan_address() -> str:
    """이 PC의 LAN 주소. 실제로 패킷을 보내지는 않고 라우팅 결과만 확인한다."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


async def run(args: argparse.Namespace) -> int:
    use_precise_clock()
    ffmpeg = find_tool("ffmpeg")
    ffprobe = find_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        print("ffmpeg와 ffprobe를 찾지 못했습니다. 설치: winget install Gyan.FFmpeg", file=sys.stderr)
        return 1
    roots = [Path(p).expanduser() for p in args.media]
    for root in roots:
        if not root.exists():
            print(f"영상 경로가 없습니다: {root}", file=sys.stderr)
            return 1

    library = Library(roots, ffprobe)
    await asyncio.get_running_loop().run_in_executor(None, library.scan)
    transcoder = Transcoder(ffmpeg, args.encoder)
    encoder = await transcoder.detect_encoder()

    app = NetStreamServer(
        library, transcoder, host=args.host, control_port=args.control_port,
        tcp_data_port=args.tcp_port, udp_data_port=args.udp_port, default_quality=args.quality,
    )
    dashboard = Dashboard(app, args.dashboard_host, args.dashboard_port)
    await app.start()
    await dashboard.start()
    probing = asyncio.create_task(library.probe_all())

    address = lan_address()
    print()
    print("NetStream Lab 서버가 실행 중입니다")
    print(f"  영상       {len(library.videos)}개")
    print(f"  인코더     {encoder}")
    print(f"  서버 주소  {address}  (제어 TCP {args.control_port}, 데이터 TCP {args.tcp_port}, 데이터 UDP {args.udp_port})")
    print(f"  대시보드   http://127.0.0.1:{args.dashboard_port}")
    print("  끝내려면 Ctrl+C")
    print()
    try:
        await asyncio.Event().wait()
    finally:
        probing.cancel()
        await dashboard.stop()
        await app.stop()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="netstream.server", description="NetStream Lab 스트리밍 서버")
    parser.add_argument("--media", action="append", required=True, help="영상 폴더 또는 파일 (여러 번 지정 가능)")
    parser.add_argument("--host", default="0.0.0.0", help="스트리밍 포트를 열 주소")
    parser.add_argument("--control-port", type=int, default=P.CONTROL_PORT)
    parser.add_argument("--tcp-port", type=int, default=P.TCP_DATA_PORT)
    parser.add_argument("--udp-port", type=int, default=P.UDP_DATA_PORT)
    parser.add_argument("--dashboard-host", default="127.0.0.1", help="대시보드를 열 주소 (기본: 이 PC에서만 접속)")
    parser.add_argument("--dashboard-port", type=int, default=P.DASHBOARD_PORT)
    parser.add_argument("--quality", default="720p", choices=["auto"] + [q.name for q in LADDER], help="처음 화질")
    parser.add_argument("--encoder", default="auto", choices=["auto", "h264_nvenc", "libx264"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
    )
    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

const { p, h1, bullet, step, code, figure, table, imageRow, titleBlock, build } = require("./lib");

const REPO = "https://github.com/ray1203/netstream-lab";

const doc = [
  titleBlock("NetStream Lab", "User Guide", [
    "Computer Networks, Assignment 4",
    process.env.NSL_AUTHOR || "Name / Student ID: (fill in)",
    "Repository: " + REPO,
  ]),

  h1("1. What it is"),
  p("NetStream Lab streams video files from a PC to a phone on the same Wi-Fi. The PC converts the video to a lower bitrate in real time, so it plays without buffering even when the original file is too big for the Wi-Fi."),
  p("The video can be sent with TCP or with UDP, and you can switch while watching. A dashboard page on the PC shows the network statistics (speed, packet loss, jitter, RTT, buffer) and has buttons to make the network worse on purpose, so you can compare how TCP and UDP react."),
  p("This document only explains how to use it. How it works and the measurement results are in the technical report."),

  h1("2. What you need"),
  bullet("PC: Windows 10 or 11, Python 3.10 or later (no extra packages)"),
  bullet("ffmpeg on the PC: `winget install Gyan.FFmpeg`"),
  bullet("Phone: Android 7.0 or later, on the same Wi-Fi as the PC"),
  bullet("To build the app: Android Studio, or JDK 17 or later"),

  h1("3. Start the server"),
  code(["cd pc", "python -m netstream.server --media \"D:\\Videos\""]),
  p("`--media` is the folder with the videos. The server prints its IP address and the dashboard address (the text is in Korean, translated here):"),
  code([
    "NetStream Lab server is running",
    "  videos      12",
    "  encoder     h264_nvenc",
    "  address     192.168.0.10  (control TCP 9000, data TCP 9001, data UDP 9002)",
    "  dashboard   http://127.0.0.1:9080",
  ]),
  p("When it runs for the first time Windows shows a firewall popup for Python. Allow it, or the phone cannot connect."),

  h1("4. Install and use the app"),
  p("Open the `android` folder in Android Studio and run it, or build the APK:"),
  code(["cd android", ".\\gradlew.bat assembleDebug"]),
  p("The APK is in `android/app/build/outputs/apk/debug/`. Copy it to the phone and install it."),
  step("Open the app and type the IP address that the server printed. Press connect."),
  step("Choose a video from the list."),
  step("While it plays you can seek, switch between TCP and UDP, turn NACK on or off, and change the quality."),
  imageRow([
    ["app_connect.png", "Figure 1. Connect"],
    ["app_tcp.png", "Figure 2. Playing with TCP"],
    ["app_udp_nack.png", "Figure 3. UDP with NACK"],
  ], 172),
  p("The box under the controls shows the current speed, loss, jitter, RTT and buffer."),

  h1("5. Dashboard"),
  p("Open `http://127.0.0.1:9080` in a browser on the PC while a video is playing."),
  figure("dashboard.jpg", "Figure 4. Dashboard (the interface is in Korean)", 600, [799, 544]),
  table(["Control", "What it does"], [
    ["TCP / UDP", "Switch the transport mode. The video continues from the same position."],
    ["NACK", "Resend lost UDP packets, on or off."],
    ["Quality", "Fixed quality (144p to 1080p) or automatic."],
    ["Presets", "Normal, 5% loss, 5% loss + 100 ms delay, 50 ms jitter, 1 Mbps bandwidth."],
    ["Sliders", "Set loss, delay, jitter and bandwidth limit by hand."],
  ], [22, 78]),

  h1("6. Demo"),
  p("Open the dashboard and play a video on the phone. Then follow this order."),
  table(["Step", "What to do", "What happens"], [
    ["1", "Play with TCP", "Plays normally, 0% loss"],
    ["2", "Press \"5% loss + 100 ms delay\"", "TCP gets slow and the video keeps stopping"],
    ["3", "Switch to UDP", "The video does not stop, but about 5% loss and the picture breaks sometimes"],
    ["4", "Turn on NACK", "Loss is recovered and the picture is clean"],
    ["5", "Press \"Normal\", then \"1 Mbps bandwidth\" (quality fixed at 720p)", "The picture freezes"],
    ["6", "Set quality to automatic", "Quality goes down to 240p and the video plays again"],
  ], [8, 42, 50]),

  h1("7. PC client (optional)"),
  p("There is also a simple PC client that plays the video with ffplay. It is useful for testing without a phone."),
  code(["cd pc", "python -m netstream.client --host 127.0.0.1 --video 0"]),
  p("Commands while it runs: `list`, `play N`, `tcp`, `udp`, `seek S`, `nack on`, `nack off`, `quality NAME`, `stop`, `quit`. With `--no-play` it only prints statistics."),

  h1("8. If it does not work"),
  table(["Problem", "Check"], [
    ["The app cannot connect", "Same Wi-Fi? Firewall allowed for Python? Some public Wi-Fi blocks connections between devices. Then connect the PC to the phone hotspot."],
    ["TCP works but UDP does not", "UDP port 9002 is blocked by the firewall."],
    ["ffmpeg not found", "Install ffmpeg and open a new terminal."],
    ["Higher quality does not change anything", "The server does not go above the resolution of the original video."],
    ["Emulator", "Use the address `10.0.2.2`."],
  ], [34, 66]),
];

build(doc, process.argv[3], "NetStream Lab - User Guide");

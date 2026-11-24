const { p, h1, h2, bullet, step, code, figure, table, imageRow, titleBlock, link, build } = require("./lib");

const REPO = "https://github.com/ray1203/netstream-lab";

const doc = [
  titleBlock("NetStream Lab", "Project Overview and User Guide", [
    "Computer Networks, Assignment 4",
    "Name / Student ID: (fill in)",
    "Repository: " + REPO,
  ]),

  h1("1. What this project is"),
  p("NetStream Lab is a program that streams video files from my PC to my phone. The video can be sent with TCP or with UDP, and there is a dashboard page that shows throughput, packet loss, jitter, RTT and the buffer of the player while the video is playing."),
  p("I started from a problem I actually had. I have a lot of videos on my PC and when I watch them on my phone with VLC it buffers a lot. The reason is that VLC sends the original file without converting it, so the bitrate is sometimes too high for the Wi-Fi. Jellyfin and Plex already fix this by transcoding on the server, so making the same thing again did not seem useful."),
  p("So for this assignment I made a smaller streaming program and focused on comparing TCP and UDP with the same video. I did not use an HTTP library for sending. The server and the clients use a simple protocol that I made on top of TCP and UDP sockets."),

  h1("2. What it can do"),
  bullet("The server converts videos to H.264 and AAC with ffmpeg in real time, so the bitrate is low enough for Wi-Fi."),
  bullet("Video data can be sent with TCP or UDP. You can switch while watching and it continues from the same position."),
  bullet("NACK: in UDP mode the client can ask the server to send lost packets again."),
  bullet("ABR: the server changes the video quality automatically when the client reports loss or an empty buffer."),
  bullet("A simulator in the server that adds packet loss, delay, jitter or a bandwidth limit. It is needed because on a normal Wi-Fi TCP and UDP look almost the same."),
  bullet("A dashboard web page with graphs that update every second and buttons for the demo."),
  bullet("An Android app (Kotlin) and a simple PC client for testing."),

  h1("3. How it meets the assignment requirements"),
  table(["Requirement", "How this project meets it"], [
    ["Use TCP or UDP in the backend", "I used both. Control messages use TCP. Video data uses TCP or UDP depending on the mode. I did not use HTTP or a streaming library for this."],
    ["Show how the protocol works in a real application", "It is a video streaming app that really plays video on a phone. The dashboard and the app show loss, reordering, retransmission and buffering while it plays, and with the simulator you can compare TCP and UDP in the same network condition."],
    ["Use Agentic AI in development", "I used Claude Code. How I used it is written in AI_LOG.md and in section 11 of the technical report."],
    ["Solve a real problem, can be demonstrated and published", "It started from my own buffering problem. I tested it on my phone over Wi-Fi and the code is on GitHub."],
  ], [30, 70]),

  h1("4. System overview"),
  figure("architecture.png", "Figure 1. The server, the three channels and the client"),
  p("The server and the client use three connections. The control channel (TCP) is for commands and status, in JSON. The video goes through another TCP connection in TCP mode, or through UDP in UDP mode. The dashboard is a web page that I open on the PC."),

  h1("5. Requirements", true),
  table(["Part", "What is needed"], [
    ["Server and PC client", "Windows 10 or 11, Python 3.10 or later. No extra Python packages."],
    ["ffmpeg", "Any recent build. Install with `winget install Gyan.FFmpeg`. An NVIDIA GPU is used for encoding if there is one, otherwise the CPU."],
    ["Android app", "Android 7.0 or later. The phone and the PC must be on the same Wi-Fi network."],
    ["Building the app", "Android Studio, or JDK 17 or later with the included Gradle wrapper."],
  ], [28, 72]),

  h1("6. Running the server"),
  p("Open a terminal in the `pc` folder and start the server with the folder that contains the videos."),
  code(["cd pc", "python -m netstream.server --media \"D:\\Videos\""]),
  p("The server prints its IP address (to type in the phone) and the dashboard address. When it runs for the first time Windows shows a firewall popup for Python. You have to allow it or the phone cannot connect."),
  code([
    "NetStream Lab server is running",
    "  videos      12",
    "  encoder     h264_nvenc",
    "  address     192.168.0.10  (control TCP 9000, data TCP 9001, data UDP 9002)",
    "  dashboard   http://127.0.0.1:9080",
  ]),
  p("Note: the program prints these lines in Korean. They are translated here."),
  table(["Option", "Meaning"], [
    ["`--media PATH`", "Video folder or file. Can be given more than once."],
    ["`--quality 720p`", "Starting quality: auto, 144p, 240p, 360p, 480p, 720p or 1080p."],
    ["`--encoder auto`", "auto, h264_nvenc or libx264."],
    ["`--dashboard-host 127.0.0.1`", "Address the dashboard listens on. By default only the PC itself can open it."],
  ], [38, 62]),

  h1("7. The dashboard"),
  p("Open `http://127.0.0.1:9080` in a browser on the PC. The boxes at the top show the current values, the four graphs show the last 2 minutes, and the controls are on the right side."),
  figure("dashboard.jpg", "Figure 2. Dashboard during a demo run. From left to right in the charts: TCP without impairment, TCP with 5% loss and 100 ms delay (the buffer falls to zero), UDP (the rate recovers but loss appears), and UDP with NACK (the final loss returns to zero). The interface text is in Korean.", 600, [799, 544]),
  table(["Control", "What it does"], [
    ["TCP / UDP buttons", "Ask the client to switch the transport mode. Playback continues from the same position."],
    ["NACK button", "Turn retransmission of lost UDP packets on or off."],
    ["Quality menu", "Fix the quality, or choose automatic (ABR)."],
    ["Preset buttons", "Normal, 5% loss, 5% loss with 100 ms delay, 50 ms jitter, 1 Mbps bandwidth."],
    ["Sliders", "Loss (0 to 30%), delay (0 to 500 ms), jitter (0 to 200 ms), bandwidth (0 means no limit)."],
    ["Event list", "A log of what happened: connections, mode changes, quality changes."],
  ], [30, 70]),

  h1("8. The Android app"),
  p("Build the app from the `android` folder, or open that folder in Android Studio and run it."),
  code(["cd android", ".\\gradlew.bat assembleDebug"]),
  p("The APK is written to `android/app/build/outputs/apk/debug/app-debug.apk`. Copy it to the phone and install it. Then:"),
  bullet("Connect the phone to the same Wi-Fi as the PC."),
  bullet("Open the app, type the server address that the server printed, and press connect."),
  bullet("Choose a video from the list."),
  imageRow([
    ["app_connect.png", "Figure 3. Connect screen"],
    ["app_tcp.png", "Figure 4. Playing over TCP"],
    ["app_udp_nack.png", "Figure 5. UDP with NACK"],
  ], 172),
  p("On the playback screen the video is at the top. Below it there are the seek bar, TCP and UDP buttons, the NACK switch and the quality menu. The box at the bottom shows the statistics that the app sends to the server. Figure 5 was taken in UDP mode with 5% loss in the simulator. The loss was 5.9% and after retransmission it was 0.0%."),
  p("In the Android emulator the PC is reached at the address `10.0.2.2`."),

  h1("9. The PC test client", true),
  p("The PC client uses the same protocol as the app and plays the video with ffplay. It was used to test the protocol before the app was made, and also for the measurements."),
  code(["cd pc", "python -m netstream.client --host 127.0.0.1 --video 0"]),
  p("While it runs, it prints one line of statistics per second and accepts these commands:"),
  table(["Command", "Meaning"], [
    ["`list`", "Show the video list."],
    ["`play N`", "Play video number N."],
    ["`tcp` / `udp`", "Switch transport mode."],
    ["`seek S`", "Jump to S seconds."],
    ["`nack on` / `nack off`", "Turn retransmission on or off."],
    ["`quality NAME`", "auto, 144p, 240p, 360p, 480p, 720p or 1080p."],
    ["`stop`, `quit`", "Stop playback, exit."],
  ], [32, 68]),
  p("With `--no-play` the client does not open a player and only prints statistics."),

  h1("10. Demo walkthrough"),
  p("This is the order for the demo. Open the dashboard on the PC and play a video on the phone."),
  step("Play over TCP with no impairment. Loss is 0% and the buffer stays around 3 seconds."),
  step("Press \"5% loss + 100 ms delay\". TCP still shows 0% loss but the speed goes down to about 580 kbps. The buffer becomes empty and the video keeps stopping."),
  step("Switch to UDP. The speed goes back to normal and the video does not stop anymore. But now the loss graph shows about 5% and the picture is broken sometimes."),
  step("Turn on NACK. Lost packets are sent again. The loss before retransmission is still about 5% but the final loss is almost 0% and the picture is clean again."),
  step("Press \"Normal\", then \"1 Mbps bandwidth\", with the quality fixed at 720p. The video needs about 2.9 Mbps so about 65% of packets are dropped and the picture freezes. NACK does not help in this case."),
  step("Set the quality to automatic. After a few seconds the server changes to 240p, the loss becomes 0% and the video plays again."),

  h1("11. If something does not work"),
  table(["Problem", "What to check"], [
    ["The app says the connection failed", "The phone and the PC are on the same Wi-Fi. Windows Firewall allows Python on TCP 9000 and 9001 and UDP 9002. Some public Wi-Fi networks block devices from reaching each other. In that case connect the PC to the phone's hotspot."],
    ["TCP works but UDP does not start", "UDP port 9002 is blocked by the firewall."],
    ["The server says ffmpeg was not found", "Install ffmpeg and open a new terminal."],
    ["Choosing a higher quality changes nothing", "The server never scales above the resolution of the original video."],
    ["The Android build fails with a path error", "The project path contains non-ASCII characters. The line `android.overridePathCheck=true` in `gradle.properties` handles this."],
  ], [34, 66]),

  h1("12. Limitations"),
  bullet("It is only for a home network. There is no login or encryption."),
  bullet("The dashboard shows one client at a time."),
  bullet("You have to type the server address. I planned automatic discovery as an optional feature but did not make it."),
  bullet("When the quality changes, the Android player is restarted and the video pauses for a moment."),
  bullet("I tested the app on an emulator and on one phone (Galaxy S23 Ultra)."),
  p("How each part works and the measurement results are in the technical report."),

  h1("13. Source code"),
  link(REPO, REPO),
  table(["Path", "Contents"], [
    ["`pc/netstream/`", "Server, PC client and the shared protocol code"],
    ["`pc/tests/e2e.py`", "End-to-end test that runs the server and a client over real sockets"],
    ["`pc/tools/`", "Measurement and figure scripts"],
    ["`android/`", "Android app"],
    ["`PROTOCOL.md`", "Protocol reference"],
    ["`AI_LOG.md`", "Record of how the AI agent was used"],
    ["`report/`", "Measurement data and figures"],
  ], [30, 70]),
];

build(doc, process.argv[3], "NetStream Lab - Project Overview and User Guide");

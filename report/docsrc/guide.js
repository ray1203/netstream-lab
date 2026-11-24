const { p, h1, h2, bullet, step, code, figure, table, imageRow, titleBlock, link, build } = require("./lib");

const REPO = "https://github.com/ray1203/netstream-lab";

const doc = [
  titleBlock("NetStream Lab", "Project Overview and User Guide", [
    "Computer Networks, Assignment 4",
    "Name / Student ID: (fill in)",
    "Repository: " + REPO,
  ]),

  h1("1. What this project is"),
  p("NetStream Lab streams video files from a PC to a phone. The same video can be sent over TCP or over UDP, and a dashboard shows what the network is doing while it plays: throughput, packet loss, reordering, jitter, round-trip time and how much video the player has buffered."),
  p("The idea started from a real problem. I have a large video collection on my PC, and watching it on my phone through VLC kept buffering. When I looked into it, the cause was that VLC sends the original file as it is, so the bitrate can be higher than the Wi-Fi can carry. Programs such as Jellyfin and Plex already solve this by transcoding on the server, so I did not try to rebuild them."),
  p("Instead, I used the same streaming setup to answer a different question: if I send the same video over TCP and over UDP, what is actually different? To make that visible I did not hide the protocols behind an HTTP library. The server and clients talk through a small protocol that I defined directly on top of TCP and UDP sockets."),

  h1("2. What it can do"),
  bullet("**Transcoding server.** The PC server uses ffmpeg to convert any video to H.264 and AAC in real time, at a resolution and bitrate the network can handle."),
  bullet("**Two transport modes.** Video data goes over a TCP connection or as UDP datagrams. The mode can be switched during playback and the video continues from the same position."),
  bullet("**Selective retransmission (NACK).** In UDP mode the client can ask the server to resend only the packets that were lost."),
  bullet("**Automatic quality (ABR).** The server lowers or raises the video quality based on the loss and buffer level the client reports."),
  bullet("**Network impairment simulator.** The server can add packet loss, delay, jitter and a bandwidth limit on purpose, so the difference between the protocols can be shown without waiting for the Wi-Fi to become bad."),
  bullet("**Live dashboard.** A web page served by the server draws the statistics every second and has the controls for the demo."),
  bullet("**Two clients.** An Android app (Kotlin) for watching on the phone, and a small PC client for testing."),

  h1("3. How it meets the assignment requirements"),
  table(["Requirement", "How this project meets it"], [
    ["Use TCP or UDP in the backend", "Both are used. Control messages always go over TCP. Video data goes over TCP or UDP depending on the selected mode. No HTTP or streaming library is used for the transport."],
    ["Show how the protocol works in a real application", "The application is a working video streamer. The dashboard and the app show loss, reordering, retransmission and buffering live, and the simulator lets the user see how TCP and UDP react to the same network condition."],
    ["Use Agentic AI in development", "I used Claude Code for planning, protocol design, implementation, debugging and testing. The process is recorded in AI_LOG.md and summarized in the technical report."],
    ["Solve a real problem, can be demonstrated and published", "It came from my own buffering problem, it runs on a real phone over Wi-Fi, and the code is on GitHub."],
  ], [30, 70]),

  h1("4. System overview"),
  figure("architecture.png", "Figure 1. The server, the three channels and the client"),
  p("There are three connections between the server and a client. The control channel is a TCP connection that carries commands and status as JSON messages. The video itself goes over a second TCP connection in TCP mode, or over UDP in UDP mode. The dashboard is a separate web page that is only opened on the PC."),

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
  p("The server prints the address to type into the phone and the address of the dashboard. The first time it runs, Windows asks whether Python may accept connections. This must be allowed, otherwise the phone cannot connect."),
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
  p("Open `http://127.0.0.1:9080` in a browser on the PC. The top row shows the current state, the four charts show the last two minutes, and the panel on the right has the controls."),
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
  p("The playback screen has the video on top and the controls below it: a seek bar, the TCP and UDP buttons, the NACK switch and the quality menu. The card at the bottom shows the same statistics that the client reports to the server. In Figure 5 the app is receiving over UDP with 5% simulated loss: 5.9% of packets were lost before retransmission and 0.0% after."),
  p("In the Android emulator the PC is reached at the address `10.0.2.2`."),

  h1("9. The PC test client", true),
  p("The PC client uses the same protocol as the app and plays the video in an ffplay window. I used it to test the protocol before the app existed, and for the measurements."),
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
  p("This is the order I use to show the difference between the protocols. Keep the dashboard open on the PC and play a video on the phone."),
  step("**Play over TCP with no impairment.** Loss is 0% and the buffer stays around 3 seconds."),
  step("**Press \"5% loss + 100 ms delay\".** TCP still shows 0% loss, but the received rate drops to about 580 kbps, the buffer runs out and the video keeps stopping."),
  step("**Switch to UDP.** The received rate goes back to normal and playback no longer stops. The loss chart now shows about 5%, and the picture breaks up from time to time."),
  step("**Turn on NACK.** The lost packets are requested again. Loss before retransmission is still about 5%, but the final loss goes to almost 0% and the picture is clean."),
  step("**Press \"Normal\", then \"1 Mbps bandwidth\", with the quality fixed at 720p.** The video needs about 2.9 Mbps, so around 65% of the packets are dropped and the picture freezes. NACK does not help here."),
  step("**Set the quality to automatic.** Within a few seconds the server drops to 240p, the loss goes to 0% and playback continues."),

  h1("11. If something does not work"),
  table(["Problem", "What to check"], [
    ["The app says the connection failed", "The phone and the PC are on the same Wi-Fi. Windows Firewall allows Python on TCP 9000 and 9001 and UDP 9002. Some public Wi-Fi networks block devices from reaching each other. In that case connect the PC to the phone's hotspot."],
    ["TCP works but UDP does not start", "UDP port 9002 is blocked by the firewall."],
    ["The server says ffmpeg was not found", "Install ffmpeg and open a new terminal."],
    ["Choosing a higher quality changes nothing", "The server never scales above the resolution of the original video."],
    ["The Android build fails with a path error", "The project path contains non-ASCII characters. The line `android.overridePathCheck=true` in `gradle.properties` handles this."],
  ], [34, 66]),

  h1("12. Limitations"),
  bullet("It is meant for a home network. There is no login and no encryption."),
  bullet("The dashboard shows one client at a time."),
  bullet("The server address has to be typed in. Automatic discovery was planned as optional and left out."),
  bullet("When the quality changes, the Android player is restarted and the video pauses for a moment."),
  bullet("I tested the app on an emulator and on one phone (Galaxy S23 Ultra)."),
  p("The technical report explains how each part works and shows the measurements."),

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

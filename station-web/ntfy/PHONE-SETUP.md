# Ground Station alerts on your iPhone

TL;DR: install the ntfy app, add your station's address as a server, log in as `anthony`, and subscribe to your alerts topic. The password and topic name are on the Pi, not in this file.

This works once the phone can reach the server (the proxy step in the README). Until then the app will say it can't connect.

1. **Get your login off the Pi.** From the Mac:
   `ssh anthonyproctor@<pi> 'sudo cat /etc/ground-station/ntfy-credentials.txt'`
   You need `topic`, `username` and `password`. Don't paste them anywhere else.
2. **Install the app.** In the App Store, get **ntfy** (by Philipp Heckel).
3. **Add your server.** In ntfy, go to Settings, then Default server. Enter your station's home page address, the same `https://...ts.net` link you open for the station, with no path and no slash on the end.
4. **Log in.** In Settings, go to Users, then Add user. Pick the same server, enter username `anthony`, and paste the password.
5. **Subscribe.** Tap **+**, paste the `topic` value (it starts with `gs-`), make sure "Use another server" shows your station address, and tap Subscribe.
6. **Allow notifications** when iOS asks. To get emergency alerts through Focus modes, go to iOS Settings, then Notifications, then ntfy, and turn on Time Sensitive.
7. **Test it.** Ask Claude to send a test alert. One will show up in the app within a few seconds once instant push is on.

What you'll get: emergency squawks (urgent), possible GPS interference (high), military, rare types and a plane low over the house (normal). It's capped at 6 an hour. Emergencies always come through.

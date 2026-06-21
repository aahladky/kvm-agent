/*
 * r4_hid_listener.ino  —  Arduino UNO R4 WiFi
 *
 * One sketch, one loop, many jobs:
 *   - joins your LAN over WiFi (WiFiS3)
 *   - listens on a raw TCP socket for newline-delimited commands
 *   - drives USB HID mouse/keyboard against whatever the USB-C data line is plugged into
 *
 * Strategy A (relative + corner-home) absolute positioning.
 * Stock Mouse.h has no moveTo(), so we slam the cursor to the top-left
 * origin (0,0) with big negative deltas, then walk out to (x,y).
 * Pointer acceleration corrupts deltas, so on the TARGET:
 *   Windows: Settings > Mouse > turn OFF "Enhance pointer precision"
 *   then calibrate SCALE_X / SCALE_Y below until clicks land.
 *
 * Command protocol (newline-terminated):
 *   M<x>,<y>   move cursor to absolute pixel (x,y)     e.g. M842,310
 *   C          left click
 *   R          right click
 *   D          left button DOWN  (for drags)
 *   U          left button UP
 *   K<code>    write one key by ASCII/keycode          e.g. K65  -> 'A'
 *   T<text>    type a string                           e.g. THello
 *   H          home cursor to (0,0)  [debug]
 *
 * Replies "ok\n" after each command.
 */

#include <WiFiS3.h>
#include <Mouse.h>
#include <Keyboard.h>

// ---- EDIT THESE ----
const char* WIFI_SSID = "YOUR_SSID";
const char* WIFI_PASS = "YOUR_PASSWORD";
const uint16_t PORT    = 8088;

// Calibration: pixels -> mouse counts. Start at 1.0, tune after homing.
// If a 500px move overshoots, lower it; if it undershoots, raise it.
float SCALE_X = 1.0;
float SCALE_Y = 1.0;
// --------------------

WiFiServer server(PORT);
WiFiClient client;
String buf;

void mouseMoveBy(int dx, int dy) {
  // Mouse.move takes a signed char; break big moves into <=127 chunks.
  while (dx != 0 || dy != 0) {
    int sx = constrain(dx, -127, 127);
    int sy = constrain(dy, -127, 127);
    Mouse.move(sx, sy);
    dx -= sx;
    dy -= sy;
  }
}

void homeCursor() {
  // 40 * -127 = -5080 counts, guarantees pinning at (0,0) on a 1080p target.
  for (int i = 0; i < 40; i++) Mouse.move(-127, -127);
}

void moveTo(int x, int y) {
  homeCursor();
  mouseMoveBy((int)(x * SCALE_X), (int)(y * SCALE_Y));
}

void handleCommand(const String& cmd) {
  if (cmd.length() == 0) return;
  char op = cmd.charAt(0);
  String arg = cmd.substring(1);

  switch (op) {
    case 'M': {
      int comma = arg.indexOf(',');
      if (comma > 0) {
        int x = arg.substring(0, comma).toInt();
        int y = arg.substring(comma + 1).toInt();
        moveTo(x, y);
      }
      break;
    }
    case 'C': Mouse.click(MOUSE_LEFT);   break;
    case 'R': Mouse.click(MOUSE_RIGHT);  break;
    case 'D': Mouse.press(MOUSE_LEFT);   break;
    case 'U': Mouse.release(MOUSE_LEFT); break;
    case 'K': Keyboard.write((uint8_t)arg.toInt()); break;
    case 'T': Keyboard.print(arg);       break;
    case 'H': homeCursor();              break;
  }

  if (client && client.connected()) client.println("ok");
}

void setup() {
  Serial.begin(115200);
  Mouse.begin();
  Keyboard.begin();

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(300);
  }

  if (WiFi.status() == WL_CONNECTED) {
    server.begin();
    Serial.print("R4 ready at ");
    Serial.print(WiFi.localIP());
    Serial.print(":");
    Serial.println(PORT);
  } else {
    Serial.println("WiFi failed — check SSID/PASS, then reset.");
  }
}

void loop() {
  // Accept a new client if we don't have a live one.
  if (!client || !client.connected()) {
    WiFiClient incoming = server.available();
    if (incoming) {
      client = incoming;
      buf = "";
      Serial.println("client connected");
    }
  }

  // Drain whatever bytes are ready — non-blocking, one line at a time.
  while (client && client.available()) {
    char c = client.read();
    if (c == '\n') {
      handleCommand(buf);
      buf = "";
    } else if (c != '\r') {
      buf += c;
      if (buf.length() > 64) buf = "";   // guard against junk
    }
  }
}

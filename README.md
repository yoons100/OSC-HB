![Platform](https://img.shields.io/badge/platform-Windows-blue) [![Release](https://img.shields.io/badge/Release-V1.0-fc1ba6)](https://github.com/yoons100/OSC-HB/releases) [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/yoons100/OSC-HB/blob/main/LICENSE)  
  
# <img width="48" height="48" alt="hb_green" src="https://github.com/user-attachments/assets/052f7e98-5fe2-425c-803f-ebbcaac345d3" /> OSC Heartbeat
OSC Link Status check App for TouchOSC
---
This app displays the link status between **TouchOSC** and the **host PC**.  
It also allows you to check the link status between the **main PC** and the **backup PC**.  

### TouchOSC Setup
1. Copy & Paste TouchOSC Document Script.
2. Status Box(Dot) name is "status_dot" -> IMPORTANT!!!
3. OSC Host(PC) IP & Sand/Recieve Port check in Connections Tab.  
(Default Send Port : 9000, Recieve Port : 8000)
---
TouchOSC Document Script : Sample 1.  
(Red Dot ↔ Green Dot)

```
local lastSeen = 0
local lastSend = 0
local sendInterval = 1000
local timeout = 5000

function init()
  lastSeen = 0
  lastSend = 0
end

function update()
  local now = getMillis()

  -- iPad -> PC heartbeat
  if now - lastSend >= sendInterval then
    sendOSC('/heartbeat', 1)
    lastSend = now
  end

  local dot = self.children.status_dot

  if dot ~= nil then
    if lastSeen > 0 and now - lastSeen <= timeout then
      dot.color = Color(0, 1, 0, 1)
    else
      dot.color = Color(1, 0, 0, 1)
    end
  end
end

function onReceiveOSC(message, connections)
  if message[1] == '/pc/alive' then
    lastSeen = getMillis()
  end
end
```

---
TouchOSC Document Script : Sample 2.  
(Vibration and flashing when the red dot is on)
```
local lastSeen = 0
local lastSend = 0

local sendInterval = 1000
local timeout = 5000

local wasOnline = false
local vibrated = false

local blinkInterval = 400
local lastBlink = 0
local blinkOn = true

function init()
  lastSeen = 0
  lastSend = 0
  wasOnline = false
  vibrated = false
  lastBlink = 0
  blinkOn = true
end

function update()
  local now = getMillis()

  -- iPad -> PC heartbeat
  if now - lastSend >= sendInterval then
    sendOSC('/heartbeat', 1)
    lastSend = now
  end

  -- PC -> iPad /pc/alive check
  local online = lastSeen > 0 and now - lastSeen <= timeout
  local dot = self.children.status_dot

  if dot ~= nil then
    if online then
      dot.color = Color(0, 1, 0, 1)
      wasOnline = true
      vibrated = false
      blinkOn = true
    else
      -- once after connection loss
      if wasOnline and not vibrated then
        vibrate()
        vibrated = true
      end

      -- blinking red dot
      if now - lastBlink >= blinkInterval then
        blinkOn = not blinkOn
        lastBlink = now
      end

      if blinkOn then
        dot.color = Color(1, 0, 0, 1)
      else
        dot.color = Color(1, 0, 0, 0.15)
      end
    end
  end
end

function onReceiveOSC(message, connections)
  if message[1] == '/pc/alive' then
    lastSeen = getMillis()
  end
end
```

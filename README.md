![Platform](https://img.shields.io/badge/platform-Windows-blue) [![Release](https://img.shields.io/badge/Release-V1.0-fc1ba6)](https://github.com/yoons100/OSC-HB/releases) [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/yoons100/OSC-HB/blob/main/LICENSE)  
  
# <img width="48" height="48" alt="hb_green" src="https://github.com/user-attachments/assets/052f7e98-5fe2-425c-803f-ebbcaac345d3" /> OSC Heartbeat
OSC Link Status check App for TouchOSC
---
1. Copy & Paste TouchOSC Document Script.
2. Status Box(Dot) name is "status_dot" -> IMPORTANT!!!
3. OSC Host(PC) IP & Sand/Recieve Port check in Connections Tab.
(Defalt Send Port : 9000, Recieve Port : 8000)
---
TouchOSC Document Script

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

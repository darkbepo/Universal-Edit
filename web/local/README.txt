Universal Media Converter — local test version
====================================

Converts images, videos and documents inside your web browser. Everything happens on this
computer: nothing is uploaded, and the small web server only accepts connections from
this computer.

WINDOWS
  1. Extract the whole zip (don't run it from inside the zip).
  2. Double-click "Start Universal Media Converter.bat".
     If Windows SmartScreen asks, choose "More info" > "Run anyway" (the script is
     plain text — you can open serve.ps1 in Notepad to read it).
  3. Your browser opens the converter. Keep the black window open while you use it;
     close it to stop.

MAC / LINUX
  Open a terminal in this folder and run:   python3 serve.py

Works best in Chrome or Edge (needed for "Save to folder…"). Firefox and Safari also work.
The first conversion of each kind downloads its engine (5-10 MB) and takes a few seconds.
Old Office formats (DOC, XLS, PPT) need the desktop app; DOCX, XLSX, PPTX, ODT, RTF,
Markdown, HTML, TXT and EPUB work here.

Licence: AGPL-3.0-or-later. Source: https://github.com/darkbepo/Universal-Media-Converter

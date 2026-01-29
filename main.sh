sudo rpi-rgb-led-matrix/utils/led-image-viewer -D0 --led-no-hardware-pulse --led-cols=64 --led-rows=32 --led-slowdown-gpio=3 --led-rgb-sequence=RBG --led-brightness=75 -D200 matrixLed/style2/blink.gif &

sleep 1

python3 ~/PFE/ia/SpeechToText.py &


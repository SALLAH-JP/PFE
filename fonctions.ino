void initIMU() {

  Wire.begin();

  if (imu.begin(BNO08X_ADDR, Wire, BNO08X_INT, BNO08X_RST) == false) {
    Serial.println("BNO08x not detected at default I2C address. Check your jumpers and the hookup guide. Freezing...");
    while (1);
  }
  Serial.println("BNO08x found!");

  if (imu.enableRotationVector() == true) Serial.println(F("Rotation vector enabled"));
  else Serial.println("Could not enable rotation vector");

}


void remoteControl() {
    // **on décode réellement ici**  
    if (IrReceiver.decode()) {

        cmd = IrReceiver.decodedIRData.command;
        
        if ( cmd == 0x18 ) moveCmd = 1;
        else if ( cmd == 0x08 ) turnCmd = 1;
        else if ( cmd == 0x5A ) turnCmd = -1;
        else if ( cmd == 0x52 ) moveCmd = -1;

        IrReceiver.resume();
    }
}


void setMotors(double cmd, int turn) {
  double left = cmd;
  double right = cmd;

  if (turn == 1) {       // droite
    left += 150;
    right -= 150;
  } else if (turn == -1) { // gauche
    left -= 150;
    right += 150;
  }

  motorL->setSpeedInHz(abs(left));
  motorR->setSpeedInHz(abs(right));

  if (left <= 0) motorL->runForward();
  else motorL->runBackward();

  if (right <= 0) motorR->runForward();
  else motorR->runBackward();
  
}


float measureSpeed(FastAccelStepper *motor) {
  static long lastPos = 0;
  static unsigned long lastTime = 0;

  long pos = motor->getCurrentPosition();
  unsigned long now = millis();

  long deltaSteps = pos - lastPos;
  unsigned long deltaT = now - lastTime; // en ms

  lastPos = pos;
  lastTime = now;

  if (deltaT == 0) return 0; // éviter la division par 0

  float stepsPerSec = (deltaSteps * 1000.0) / deltaT;
  float revsPerSec = stepsPerSec / STEPS_REV;
  float wheelCircumference = PI * WHEEL_DIAMETER;
  return revsPerSec * wheelCircumference; // [m/s]
}

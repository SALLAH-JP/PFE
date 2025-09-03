void updateAngle() {
  unsigned long now = millis();
  double dt = (now - lastTime) / 1000.0;
  lastTime = now;

  int16_t ax, ay, az, gx, gy, gz;
  mpu.getMotion6(&ax, &ay, &az, &gx, &gy, &gz);

  double accAngle = atan2(ay, az) * 180 / PI;  // inclinaison via acc
  gyroRate = gx / 131.0;                       // gyro en °/s (sensibilité ±250dps)

  // filtre complémentaire
  angle = 0.98 * (angle + gyroRate * dt) + 0.02 * accAngle;

}

void remoteControl() {
    // **on décode réellement ici**  
    if (IrReceiver.decode()) {

        cmd = IrReceiver.decodedIRData.command;
        
        if ( cmd == 0x18 ) moveCmd = 1;
        else if ( cmd == 0x08 ) turnCmd = 1;
        else if ( cmd == 0x5A ) turnCmd = -1;
        else if ( cmd == 0x52 ) moveCmd = -1;
        else Serial.println(cmd);


        IrReceiver.resume();
    }
}


void setMotors(double cmd, int turn) {
  double left = cmd;
  double right = cmd;

  if (turn == 1) {       // droite
    left += 50;
    right -= 50;
  } else if (turn == -1) { // gauche
    left -= 50;
    right += 50;
  }

  motorL.setSpeed(left);
  motorR.setSpeed(right);
}
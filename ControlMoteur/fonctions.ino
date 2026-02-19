void setReports(void) {
  Serial.println("Setting desired reports");
  if (imu.enableRotationVector() == true) {
    Serial.println(F("Rotation vector enabled"));
    Serial.println(F("Output in form roll, pitch, yaw"));
  } else {
    Serial.println("Could not enable rotation vector");
  }
}

float lowPassFilter(float value, float prevValue, float alpha) {
  return alpha * value + (1 - alpha) * prevValue;
}

void remoteControl() {
  // **on décode réellement ici**  
  if (IrReceiver.decode()) {

    cmd = IrReceiver.decodedIRData.command;

    if ( cmd == 0x18 ) move1Start = millis();
    else if ( cmd == 0x08 ) turn1Start = millis();
    else if ( cmd == 0x5A ) turn2Start = millis();
    else if ( cmd == 0x52 ) move2Start = millis();
  

    else if ( ( (IrReceiver.decodedIRData.flags & IRDATA_FLAGS_IS_REPEAT) && (millis() - lastRemote > 100) ) || !(IrReceiver.decodedIRData.flags & IRDATA_FLAGS_IS_REPEAT) ) {

      if ( cmd == 0x45 ) KpA += 1;
      else if ( cmd == 0x46 ) KiA += 0.01;
      else if ( cmd == 0x47 ) KdA += 0.1;
      else if ( cmd == 0x44 ) KpA -= 1;
      else if ( cmd == 0x40 ) KiA -= 0.01;
      else if ( cmd == 0x43 ) KdA -= 0.1;
      else if ( cmd == 0x07 ) KpV += 1;
      else if ( cmd == 0x15 ) KiV += 0.01;
      else if ( cmd == 0x09 ) KdV += 0.1;
      else if ( cmd == 0x16 ) KpV -= 1;
      else if ( cmd == 0x19 ) KiV -= 0.01;
      else if ( cmd == 0x0D ) KdV -= 0.1;


      
    }
    lastRemote = millis();
    IrReceiver.resume();
  }

  if ( millis() - move1Start < 100 ) moveCmd = 50;
  else if ( millis() - move2Start < 100 ) moveCmd = -50;
  else if ( millis() - turn1Start < 100 ) turnCmd = -150;
  else if ( millis() - turn2Start < 100 ) turnCmd = 150;
}


void setMotors(double cmd, int turn) {
  double left = cmd - turn;
  double right = cmd + turn;

  motorL->setSpeedInHz(abs(left));
  motorR->setSpeedInHz(abs(right));


  if (left < 0) motorL->runBackward();
  else motorL->runForward();

  if (right <= 0) motorR->runForward();
  else motorR->runBackward();

}


float measureSpeed() {
  static long lastPosL = 0;
  static long lastPosR = 0;
  static unsigned long lastTime = 0;
  static float filteredSpeed = 0;

  long posL = -motorL->getCurrentPosition();
  long posR = motorR->getCurrentPosition();
  long deltaStepsL = posL - lastPosL;
  long deltaStepsR = posR - lastPosR;
  unsigned long now = millis();
  //Serial.print(deltaStepsL); Serial.print(" => "); Serial.println(deltaStepsR);

  unsigned long deltaT = now - lastTime; // en ms

  lastPosL = posL;
  lastPosR = posR;
  lastTime = now;

  if (deltaT == 0) return 0; // éviter la division par 0

  float revsPerSecL = ((deltaStepsL * 1000.0) / deltaT) / (STEPS_REV * MICRO_STEPS);
  float revsPerSecR = ((deltaStepsR * 1000.0) / deltaT) / (STEPS_REV * MICRO_STEPS);
  float wheelSpeedL = (PI * WHEEL_DIAMETER) * revsPerSecL;
  float wheelSpeedR = (PI * WHEEL_DIAMETER) * revsPerSecR;
  float speed = (wheelSpeedL + wheelSpeedR) / 2.0;

  // filtrage passe-bas
  filteredSpeed = lowPassFilter(speed, filteredSpeed, 0.1);

  return filteredSpeed ; // [cm/s]
}


void lineTracking() {
  leftValue = digitalRead(LEFT_SENSOR_PIN);
  rightValue = digitalRead(RIGHT_SENSOR_PIN);

  if ( leftValue == LOW && rightValue == LOW ) moveCmd = 20;
  else if ( leftValue == HIGH && rightValue == HIGH ) moveCmd = 0;
  //else if ( abs(EQUILIBRE - inputA) < 2 ) {
    else if ( leftValue == HIGH ) turnCmd = -75;
    else if ( rightValue == HIGH ) turnCmd = 75;
  //}


}

unsigned long lastTime = 0;
unsigned long loopTime = 0;

void temps() {
  unsigned long now = micros();       // Temps actuel en µs
  loopTime = now - lastTime;          // Durée d'une itération
  lastTime = now;

  // Affiche toutes les 500 ms pour ne pas saturer la liaison série
  static unsigned long lastPrint = 0;
  if (millis() - lastPrint > 500) {
    Serial.print("Loop time (us): ");
    Serial.print(loopTime);
    Serial.print("  =>  Freq: ");
    Serial.print(1000000.0 / loopTime);
    Serial.println(" Hz");
    lastPrint = millis();
  }
}

void dmpDataReady() {
  mpuInterrupt = true;
}

void initIMU() {

  // initialize device
  Serial.println(F("Initializing I2C devices..."));
  mpu.initialize();

  Serial.println(mpu.testConnection() ? F("MPU6050 connection successful") : F("MPU6050 connection failed"));

  mpu.setXGyroOffset(-76);
  mpu.setYGyroOffset(-54);
  mpu.setZGyroOffset(2);
  mpu.setZAccelOffset(1384); 

  // load and configure the DMP
  Serial.println(F("Initializing DMP..."));
  devStatus = mpu.dmpInitialize();
  // make sure it worked (returns 0 if so)
  if (devStatus == 0) {

    mpu.setDMPEnabled(true);

    attachInterrupt(digitalPinToInterrupt(2), dmpDataReady, RISING);
    mpuIntStatus = mpu.getIntStatus();
    // set our DMP Ready flag so the main loop() function knows it's okay to use it
    Serial.println(F("DMP ready! Waiting for first interrupt..."));
    dmpReady = true;
    // get expected DMP packet size for later comparison
    packetSize = mpu.dmpGetFIFOPacketSize();

  } else {
    // ERROR!
    // 1 = initial memory load failed
    // 2 = DMP configuration updates failed
    // (if it's going to break, usually the code will be 1)
    Serial.print(F("DMP Initialization failed (code "));
    Serial.print(devStatus);
    Serial.println(F(")"));
  }
}


void remoteControl() {
  // **on décode réellement ici**  
  if (IrReceiver.decode()) {

    cmd = IrReceiver.decodedIRData.command;

    if ( cmd == 0x18 ) moveCmd = 15;
    else if ( cmd == 0x08 ) turnCmd = 50;
    else if ( cmd == 0x5A ) turnCmd = -50;
    else if ( cmd == 0x52 ) moveCmd = -15;

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
}


void setMotors(double cmd, int turn) {
  double left = cmd * balRate - turn;
  double right = cmd * balRate + turn;

  motorL->setSpeedInHz(abs(left));
  motorR->setSpeedInHz(abs(right));


  if (left < 0) motorL->runForward();
  else motorL->runBackward();

  if (right <= 0) motorR->runBackward();
  else motorR->runForward();

  
}


float measureSpeed() {
  static long lastPosL = 0;
  static long lastPosR = 0;
  static unsigned long lastTime = 0;
  static float filteredSpeed = 0;

  long posL = motorL->getCurrentPosition();
  long posR = motorR->getCurrentPosition();
  long deltaStepsL = posL - lastPosL;
  long deltaStepsR = posR - lastPosR;
  unsigned long now = millis();

  unsigned long deltaT = now - lastTime; // en ms

  lastPosL = posL;
  lastPosR = posR;
  lastTime = now;

  if (deltaT == 0) return 0; // éviter la division par 0

  float revsPerSecL = ((deltaStepsL * 10000.0) / deltaT) / (STEPS_REV * MICRO_STEPS);
  float revsPerSecR = ((deltaStepsR * 10000.0) / deltaT) / (STEPS_REV * MICRO_STEPS);
  float wheelSpeedL = (PI * WHEEL_DIAMETER) * revsPerSecL;
  float wheelSpeedR = (PI * WHEEL_DIAMETER) * revsPerSecR;
  float speed = (wheelSpeedL - wheelSpeedR) / 2.0;

  const float alpha = 0.1;  // entre 0 et 1 → plus petit = plus lisse
  filteredSpeed = alpha * speed + (1 - alpha) * filteredSpeed;

  return filteredSpeed; // [m/s]
}


float getPitchIMU() {
  // reset interrupt flag and get INT_STATUS byte
  mpuInterrupt = false;
  mpuIntStatus = mpu.getIntStatus();

  // get current FIFO count
  fifoCount = mpu.getFIFOCount();

  // check for overflow (this should never happen unless our code is too inefficient)
  if ((mpuIntStatus & 0x10) || fifoCount == 1024) {
    // reset so we can continue cleanly
    mpu.resetFIFO();
    Serial.println(F("FIFO overflow!"));
  // otherwise, check for DMP data ready interrupt (this should happen frequently)
  } else if (mpuIntStatus & 0x02) {
    // wait for correct available data length, should be a VERY short wait
    while (fifoCount < packetSize) fifoCount = mpu.getFIFOCount();
    // read a packet from FIFO
    mpu.getFIFOBytes(fifoBuffer, packetSize);
    // track FIFO count here in case there is > 1 packet available
    // (this lets us immediately read more without waiting for an interrupt)
    fifoCount -= packetSize;
    mpu.dmpGetQuaternion(&q, fifoBuffer); //get value for q
    mpu.dmpGetGravity(&gravity, &q); //get value for gravity
    mpu.dmpGetYawPitchRoll(ypr, &q, &gravity); //get value for ypr
    pitch = ypr[1] * 180/M_PI - 180;
    if ( pitch < -180) pitch += 360;
  }
  return pitch; 
}

void lineTracking() {


  leftValue = digitalRead(LEFT_SENSOR_PIN);
  rightValue = digitalRead(RIGHT_SENSOR_PIN);

  //if ( leftValue == LOW && rightValue == LOW ) moveCmd = 5;
  if ( !(leftValue == HIGH && rightValue == HIGH) ) {
    if ( leftValue == HIGH ) turnCmd = -50;
    else if ( rightValue == HIGH ) turnCmd = 50;
  } else moveCmd = 3;

  lastRemote = 0;

}

#include "header.h"


void setup() {
  Serial.begin(115200);

  Serial.println("Start Setup");

  // MPU init
  initIMU();

  // IR Receiver Setup and interrupt
  IrReceiver.begin(IR_PIN, ENABLE_LED_FEEDBACK);


  // Activer les moteurs
  engine.init();

  // Moteur droit
  motorR = engine.stepperConnectToPin(STEP_PIN_R);   // STEP droit
  if (motorR) {
    motorR->setDirectionPin(DIR_PIN_R);            // DIR droit
    motorR->setEnablePin(ENA_PIN_R, false);               // EN droit
    motorR->setAutoEnable(true);
    motorR->setAcceleration(2000);
    Serial.println("Motor Right OK !");
  }

  // Moteur gauche
  motorL = engine.stepperConnectToPin(STEP_PIN_L);   // STEP gauche
  if (motorL) {
    motorL->setDirectionPin(DIR_PIN_L);            // DIR gauche
    motorL->setEnablePin(ENA_PIN_L, false);               // EN gauche
    motorL->setAutoEnable(true);
    motorL->setAcceleration(2000);
    Serial.println("Motor Left OK !");
  }


  // Tracking Sensor Setup
  pinMode(LEFT_SENSOR_PIN, INPUT);
  pinMode(RIGHT_SENSOR_PIN, INPUT);

  // PID
  pidA.SetSampleTime(5);
  pidA.SetOutputLimits(-5000, 5000);
  pidA.SetMode(AUTOMATIC);

  pidV.SetSampleTime(5);
  pidV.SetOutputLimits(-45, 45);
  pidV.SetMode(AUTOMATIC);

}


void loop() {
  if ( millis() - lastRemote > 100 ) {
    moveCmd = 0;
    turnCmd = 0;
  }

  remoteControl();
  //pidA.SetTunings(KpA, KiA, KdA);
  //pidV.SetTunings(KpV, KiV, KdV);
  lineTracking();

  unsigned long now = millis();

  if (now - lastCmdTime >= 5) {
    lastCmdTime = now;

    currentMoveCmd += constrain(moveCmd - currentMoveCmd, -0.1, 0.1);
    currentTurnCmd += constrain(turnCmd - currentTurnCmd, -1, 1);
  }

  inputV = measureSpeed();

  pidV.Compute();
  setpointA = EQUILIBRE + outputV;


  if ( mpuInterrupt ) {
    
    inputA = getPitchIMU();

    pidA.Compute();
    //Serial.print(inputA); Serial.print(" => "); Serial.println(outputA);
    
  }

  if (abs(inputA) < 2) balRate = 0;
  else {
    balRate = 1;
    currentTurnCmd = 0;
  }

  //Serial.print(inputV); Serial.print(" => "); Serial.print(KpV); Serial.print(" => "); Serial.print(KiV); Serial.print(" => "); Serial.println(KdV);
  Serial.print(currentMoveCmd); Serial.print(" => "); Serial.println(currentTurnCmd);
  setMotors(outputA, currentTurnCmd);

}

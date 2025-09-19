#include "header.h"


void setup() {
  Serial.begin(115200);

  Wire.begin();

  // MPU init
  initIMU();

  // IR Receiver Setup and interrupt
  IrReceiver.begin(2, ENABLE_LED_FEEDBACK);


  // Activer les moteurs
  engine.init();

  // Moteur droit
  motorR = engine.stepperConnectToPin(STEP_PIN_R);   // STEP droit
  if (motorR) {
    motorR->setDirectionPin(DIR_PIN_R);            // DIR droit
    motorR->setEnablePin(ENA_PIN_R, false);               // EN droit
    motorR->setAutoEnable(true);
    //motorR->setAcceleration(1000);
  }

  // Moteur gauche
  motorL = engine.stepperConnectToPin(STEP_PIN_L);   // STEP gauche
  if (motorL) {
    motorL->setDirectionPin(DIR_PIN_L);            // DIR gauche
    motorL->setEnablePin(ENA_PIN_L, false);               // EN gauche
    motorL->setAutoEnable(true);
    //motorL->setAcceleration(1000);
  }


  // PID
  pidA.SetSampleTime(10);
  pidA.SetOutputLimits(-2000, 2000);
  pidA.SetMode(AUTOMATIC);

  pidV.SetSampleTime(10);
  pidV.SetOutputLimits(-5, 5); // par ex. correction d’angle en degrés max
  pidV.SetMode(AUTOMATIC);

}


void loop() {
  remoteControl();

  if (millis() - lastCmdTime >= 500) {
    lastCmdTime = millis();

    moveCmd = 0;  // stop
    turnCmd = 0;
  }

  inputV = (measureSpeed(motorR) + measureSpeed(motorR)) / 2;

  pidV.Compute();
  setpointA = EQUILIBRE + outputV;


  if ( imu.getSensorEvent() == true && imu.getSensorEventID() == SENSOR_REPORTID_ROTATION_VECTOR) {

    inputA = (imu.getPitch()) * 180.0 / PI;

    pidA.Compute();
    Serial.print(inputA); Serial.print(" => "); Serial.println(outputA);
    
  }

  setMotors(outputA, turnCmd);

}
